# -*- coding: utf-8 -*-
"""转换流程（与界面无关）：识别输入格式 → 必要时先转 PMX → 再转 VRM。

    import pipeline
    outs = pipeline.convert_file(path, opts, log)    # → [输出文件路径, ...]

.zip 压缩包（模型网站下载的整包）会先解压到临时目录，再转换里面的模型：有 PMX 就转 PMX，
没有再转其它能识别的格式；输出放在压缩包旁边（或指定的输出文件夹），临时目录随后删除。

opts 是纯 JSON 可序列化的 dict（界面直接存取），缺的键用 DEFAULTS 补齐：

    target   "vrm"（默认）| "pmx"（非 PMX 输入只转到 PMX 为止）
    output   {"mode": "beside" | "folder", "folder": 路径, "subfolder": 是否建「名字_日期_序号」子文件夹}
    vrm      {"spec": "1.0" | "0.x", "spring", "spring_skirt", "spring_stiffness",
              "morphs", "outline", "keep_pose", "double_sided"}
    fbx      {"alpha": "auto" | "keep" | "strip", "auto_facing", "face_180", "morphs", "center", "flip_z"}
    uemodel  {"alpha", "align_ref"}
    psk      {"jp_bones", "ik", "morphs", "alpha"}
    xps      {"jp_bones", "ik", "hide_unused", "double_sided", "edge"}

log(msg, tag=None)：tag 为 None / "info" / "ok" / "warn" / "err" / "head"。
"""
import contextlib
import copy
import io
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "formats")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

KINDS = {".pmx": "pmx", ".vrm": "vrm", ".fbx": "fbx", ".unitypackage": "unitypackage",
         ".uemodel": "uemodel", ".psk": "psk", ".pskx": "psk", ".xps": "xps", ".zip": "zip"}
CONVERTIBLE = {"pmx", "fbx", "unitypackage", "uemodel", "psk", "xps", "zip"}

DEFAULTS = {
    "target": "vrm",
    "output": {"mode": "beside", "folder": "", "subfolder": True},
    "vrm": {"spec": "1.0", "spring": True, "spring_skirt": True, "spring_stiffness": 1.0,
            "morphs": True, "outline": False, "keep_pose": False, "double_sided": False},
    "fbx": {"alpha": "auto", "auto_facing": True, "face_180": False, "morphs": True,
            "center": True, "flip_z": True},
    "uemodel": {"alpha": True, "align_ref": ""},
    "psk": {"jp_bones": True, "ik": True, "morphs": True, "alpha": True},
    "xps": {"jp_bones": True, "ik": True, "hide_unused": True, "double_sided": True, "edge": False},
}


def classify(path):
    """→ "pmx" / "vrm" / "fbx" / "unitypackage" / "uemodel" / "psk" / "xps" / "zip"，不支持的为 None。"""
    return KINDS.get(os.path.splitext(path)[1].lower())


def merged(opts):
    """用户选项和 DEFAULTS 合并（两层），不改动传入的 dict。"""
    out = copy.deepcopy(DEFAULTS)
    for k, v in (opts or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k].update(v)
        else:
            out[k] = v
    return out


def find_inputs(folder):
    """文件夹里（递归）所有能转换或预览的文件。"""
    found = []
    for d, dirs, files in os.walk(folder):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in sorted(files):
            if classify(f):
                found.append(os.path.join(d, f))
    return found


def _score(text):
    """文件名解码结果像不像正常的中日文：常用汉字、假名、ASCII 加分，半角片假名等乱码特征扣分。"""
    sc = 0
    for ch in text:
        o = ord(ch)
        if o < 0x80 or 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF or 0x3000 <= o <= 0x303F                 or 0xFF01 <= o <= 0xFF5E:
            sc += 1
        elif 0xFF61 <= o <= 0xFF9F or 0xE000 <= o <= 0xF8FF:
            sc -= 3                    # 半角片假名 / 私用区：GBK 被当成 cp932 解的典型样子
    return sc


def _decodings(raw, first):
    """一个文件名的候选解码，按「常用字多」排序（同分时 first 优先），只保留严格解码得通的。

    同一个包里常混着 GBK（中文系统）和 cp932（日文系统）的名字，而一种编码的字节按另一种解，
    得到的多半是生僻字（「武器」的 cp932 字节按 GBK 解成「晲婍」）。用 GB2312 能不能编码当作
    「常用字」的判断：它收了常用汉字和日文假名，GBK 扩展区的生僻字和半角片假名都不在里面。
    """
    tries = [raw]
    # cp932 的「ダメ文字」：第二字节是 0x5C（反斜杠），有的打包工具把它当路径分隔符改成了 /
    fixed = re.sub(rb"([\x81-\x9f\xe0-\xfc])/", rb"\1\\", raw)
    if fixed != raw:
        tries.append(fixed)
    out = []
    for r in tries:
        for enc in (first, "gbk", "cp932", "utf-8"):
            try:
                name = r.decode(enc)
            except UnicodeDecodeError:
                continue
            if name not in out:
                out.append(name)
    if not out:
        return [raw.decode(first, "replace")]

    def common(n):
        k = 0
        for ch in n:
            try:
                ch.encode("gb2312")
                k += 1
            except UnicodeEncodeError:
                pass
        return k
    order = {n: i for i, n in enumerate(out)}
    out.sort(key=lambda n: (-common(n), order[n]))
    return out


def zip_members(zf):
    """→ [(ZipInfo, [候选文件名, …])]，第一个是默认用的。

    没设 UTF-8 标志的压缩包，文件名是打包那台电脑的本地编码（中文系统 GBK，日文系统 cp932），
    而且同一个包里可能两种混着（作者的日文原文件 + 后加的中文文件）。先给整包选一种主编码
    （严格解得通、且「像正常中日文」得分最高的），每个文件再按常用字多少挑解法（见 _decodings），
    解压后还会用 PMX 里记录的贴图路径纠正（见 extract_zip）。
    """
    infos = zf.infolist()
    legacy = [i.filename.encode("cp437") for i in infos if not i.flag_bits & 0x800]
    best, best_score = "gbk", None
    for enc in ("utf-8", "gbk", "cp932"):
        try:
            text = "".join(r.decode(enc) for r in legacy)
        except UnicodeDecodeError:
            continue
        sc = _score(text) + (len(text) if enc == "utf-8" else 0)   # 严格 UTF-8 解得通基本就是它
        if best_score is None or sc > best_score:
            best, best_score = enc, sc
    return [(i, [i.filename] if i.flag_bits & 0x800 else _decodings(i.filename.encode("cp437"), best))
            for i in infos]


def _safe_parts(name):
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    if parts and ":" in parts[0]:                   # 盘符
        parts = parts[1:]
    return parts


def extract_zip(path, dest):
    """安全解压（去掉绝对路径和 ..，不会写到 dest 外面），返回解出的文件数。

    文件名按 zip_members 的默认解码落盘后，再读每个 PMX：某个文件换一种解码正好是 PMX 引用的
    贴图路径（或 PMX 自己的模型名），就改成那个名字——否则贴图对不上，转出来没有贴图。
    """
    import pmxio
    entries = []                                     # (落盘相对路径, [候选相对路径])
    n = 0
    with zipfile.ZipFile(path) as zf:
        for info, names in zip_members(zf):
            if info.is_dir():
                continue
            cands = ["/".join(_safe_parts(x)) for x in names]
            cands = [c for c in cands if c]
            if not cands:
                continue
            target = os.path.join(dest, *cands[0].split("/"))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as f:
                shutil.copyfileobj(src, f)
            entries.append((cands[0], cands))
            n += 1
    if all(len(c) == 1 for _, c in entries):
        return n
    wanted = set()                                   # PMX 期望存在的相对路径（小写）
    pmx_names = {}                                   # PMX 相对路径 → 内部模型名
    for rel, _ in entries:
        if not rel.lower().endswith(".pmx"):
            continue
        try:
            m = pmxio.read_pmx(os.path.join(dest, *rel.split("/")))
        except Exception:  # noqa: BLE001  读不了就不纠正
            continue
        base = rel.rsplit("/", 1)[0] + "/" if "/" in rel else ""
        for t in m["textures"]:
            wanted.add((base + "/".join(_safe_parts(t))).lower())
        pmx_names[rel] = (m.get("name") or "").strip()
    for rel, cands in entries:
        if len(cands) < 2:
            continue
        fix = None
        if rel.lower() not in wanted:
            fix = next((c for c in cands[1:] if c.lower() in wanted), None)
        if fix is None and rel in pmx_names:
            stem = lambda c: os.path.splitext(c.rsplit("/", 1)[-1])[0]
            if stem(rel) != pmx_names[rel]:
                fix = next((c for c in cands[1:] if stem(c) == pmx_names[rel]), None)
        if fix:
            os.renames(os.path.join(dest, *rel.split("/")), os.path.join(dest, *fix.split("/")))
    return n


GAMES = {"崩坏：星穹铁道": "星穹铁道"}          # 下载站的游戏名 → 简称


def nice_name(stem):
    """「<名字>_by_<游戏>_<哈希>」（模之屋的下载命名）→「<游戏>—<名字>」，去掉「(修模绑骨后)」
    这类说明和结尾的版本号；「（星）」这种有意义的括号保留。其它名字原样返回。"""
    name, sep, rest = stem.partition("_by_")
    if not sep:
        return stem
    game = rest.rsplit("_", 1)[0]
    game = GAMES.get(game, game)
    name = name.strip("【】")
    m = re.search(r"[（(][^（(]*$", name)
    if m and re.search(r"修|绑|更新|版", m.group()):
        name = name[:m.start()]
    name = re.sub(r"\d+(\.\d+)*$", "", name)
    for pre in (game + "—", game + "_"):
        if name.startswith(pre):
            name = name[len(pre):]
    return game + "—" + name.strip("_— ")


def output_dir(path, opts):
    """按 output 选项决定这个源文件的输出目录（会建好）。"""
    o = opts["output"]
    base = o.get("folder") if o.get("mode") == "folder" and o.get("folder") else \
        os.path.dirname(os.path.abspath(path))
    if o.get("subfolder", True):
        stem = nice_name(os.path.splitext(os.path.basename(path))[0])
        day = time.strftime("%Y%m%d")
        for n in range(1, 10000):
            d = os.path.join(base, "%s_%s_%03d" % (stem, day, n))
            if not os.path.exists(d):
                base = d
                break
    os.makedirs(base, exist_ok=True)
    return base


class _LogWriter(io.TextIOBase):
    """把 print() 的输出转成 log 回调（fbx2pmx 用 print 汇报进度）。"""

    def __init__(self, log):
        self._log = log
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._log(line.rstrip(), None)
        return len(s)

    def flush(self):
        if self._buf.strip():
            self._log(self._buf.rstrip(), None)
        self._buf = ""


def to_pmx(path, kind, out_dir, opts, log):
    """非 PMX 输入 → PMX，返回写出的 PMX 路径列表（unitypackage 可能有多个）。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    if kind in ("fbx", "unitypackage"):
        import fbx2pmx
        o = opts["fbx"]
        if kind == "unitypackage":
            from unitypackage_unpack import unpack
            dest = os.path.join(out_dir, stem + "_extracted")
            log("解包 %s" % os.path.basename(path), "info")
            with contextlib.redirect_stdout(_LogWriter(log)):
                unpack(path, dest)
            fbxs = [p for p in find_files(dest, ".fbx") if os.path.getsize(p) > 1024]
            if not fbxs:
                raise RuntimeError("包里没有找到 FBX")
            fbxs.sort(key=lambda p: -os.path.getsize(p))
            src_dir = dest
        else:
            fbxs, src_dir = [path], out_dir
        outs = []
        for fbx in fbxs:
            name = os.path.splitext(os.path.basename(fbx))[0]
            out = os.path.join(src_dir, name + ".pmx")
            log("读取 FBX：%s" % os.path.basename(fbx), "info")
            w = _LogWriter(log)
            with contextlib.redirect_stdout(w):
                scene = fbx2pmx.Scene(fbx)
                st = fbx2pmx.convert(scene, out, scale_mode="auto", flip_z=o["flip_z"],
                                     name=name, center=o["center"], morphs=o["morphs"],
                                     alpha_mode=o["alpha"], auto_facing=o["auto_facing"],
                                     face_180=o["face_180"])
            w.flush()
            if st.get("facing_reason"):
                log("朝向：%s" % st["facing_reason"], None)
            outs.append(out)
        return outs
    out = os.path.join(out_dir, stem + ".pmx")
    if kind == "uemodel":
        import uemodel2pmx
        o = opts["uemodel"]
        uemodel2pmx.convert(path, out, scale_mode="auto", log=log, name=stem,
                            remove_alpha=o["alpha"], align_to=o.get("align_ref") or None,
                            force_double_sided=opts["vrm"]["double_sided"])
    elif kind == "psk":
        import psk2pmx
        o = opts["psk"]
        psk2pmx.convert(path, out, scale_mode="auto", log=log, name=stem,
                        jp_bones=o["jp_bones"], make_ik=o["ik"], export_morphs=o["morphs"],
                        remove_alpha=o["alpha"], force_double_sided=opts["vrm"]["double_sided"])
    elif kind == "xps":
        import xps2pmx
        o = opts["xps"]
        xps2pmx.convert(path, out, scale_mode="auto", log=log, name=stem,
                        jp_bones=o["jp_bones"], make_ik=o["ik"], hide_unused=o["hide_unused"],
                        all_double_sided=o["double_sided"], enable_edge=o["edge"],
                        force_double_sided=opts["vrm"]["double_sided"])
    else:
        raise ValueError("不支持的输入：%s" % kind)
    return [out]


def find_files(root, ext):
    return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.lower().endswith(ext)]


def pmx_to_vrm(pmx, out_dir, opts, log, name=None):
    """PMX → VRM，返回 VRM 路径。"""
    import pmx2vrm
    o = opts["vrm"]
    stem = name or os.path.splitext(os.path.basename(pmx))[0]
    out = os.path.join(out_dir, stem + ".vrm")
    spec = "1.0" if str(o["spec"]).startswith("1") else "0.x"
    pmx2vrm.convert(pmx, out, spec=spec, meta={"title": stem}, log=log,
                    max_morphs=None if o["morphs"] else 0,
                    force_double_sided=o["double_sided"], tpose=not o["keep_pose"],
                    outline=o["outline"], spring=o["spring"], spring_skirt=o["spring_skirt"],
                    spring_stiffness=float(o["spring_stiffness"]))
    return out


def convert_file(path, opts=None, log=None, out_dir=None):
    """转换一个文件，返回输出文件列表。out_dir 不给时按 opts["output"] 决定。"""
    opts = merged(opts)
    log = log or (lambda m, t=None: None)
    kind = classify(path)
    if kind not in CONVERTIBLE:
        raise ValueError("不能转换这个文件：%s" % os.path.basename(path))
    out_dir = out_dir or output_dir(path, opts)
    log("→ %s" % os.path.basename(path), "head")
    log("输出目录：%s" % out_dir, "info")
    if kind == "zip":
        return _convert_zip(path, out_dir, opts, log)
    return _convert_model(path, kind, out_dir, opts, log)


def _convert_zip(path, out_dir, opts, log):
    tmp = tempfile.mkdtemp(prefix="model2vrm_zip_")
    try:
        n = extract_zip(path, tmp)
        inner = [p for p in find_inputs(tmp) if classify(p) in CONVERTIBLE - {"zip"}]
        pmx = [p for p in inner if classify(p) == "pmx"]
        todo = pmx or inner          # 有 PMX 就只转 PMX（同包里的 FBX 等通常是同一个模型）
        log("解压 %d 个文件，找到 %d 个模型：%s" % (n, len(todo), "、".join(
            os.path.relpath(p, tmp) for p in todo)), "info")
        if not todo:
            raise RuntimeError("压缩包里没有可转换的模型（PMX / FBX / uemodel / psk / xps）")
        outs = []
        for p in todo:
            log("→ %s" % os.path.relpath(p, tmp), "head")
            outs += _convert_model(p, classify(p), out_dir, opts, log)
        return outs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _convert_model(path, kind, out_dir, opts, log):
    if kind == "pmx":
        return [pmx_to_vrm(path, out_dir, opts, log)]
    pmxs = to_pmx(path, kind, out_dir, opts, log)
    for p in pmxs:
        log("已写出 %s" % os.path.basename(p), "ok")
    if opts["target"] == "pmx":
        return pmxs
    outs = list(pmxs)
    for p in pmxs:
        log("PMX → VRM：%s" % os.path.basename(p), "info")
        outs.append(pmx_to_vrm(p, out_dir, opts, log))
    return outs
