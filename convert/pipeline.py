# -*- coding: utf-8 -*-
"""转换流程（与界面无关）：识别输入格式 → 必要时先转 PMX → 再转 VRM。

    import pipeline
    outs = pipeline.convert_file(path, opts, log)    # → [输出文件路径, ...]

压缩包（模型网站下载的整包：zip / 7z / rar / tar.gz 等）会先解压到临时目录，再转换里面的模型：
有 PMX 就转 PMX，没有再转其它能识别的格式；包里只有另一个压缩包时再解一层。输出放在压缩包
旁边（或指定的输出文件夹），临时目录随后删除。zip 用 Python 自带的 zipfile（处理 GBK / cp932
文件名），其它格式见 formats/archive.py（7-Zip / WinRAR / Bandizip / Windows 自带的 tar）。

opts 是纯 JSON 可序列化的 dict（界面直接存取），缺的键用 DEFAULTS 补齐：

    target   "vrm"（默认）| "pmx"（非 PMX 输入只转到 PMX 为止）
    output   {"mode": "app" | "beside" | "folder", "folder": 路径, "subfolder": 是否建「名字_日期_序号」子文件夹}
             app = 程序目录下的 vrm 文件夹（界面用；路径由调用方放在 "app_folder"，没有时同 beside）
    vrm      {"spec": "1.0" | "0.x", "spring", "spring_skirt", "spring_stiffness", "auto_spring",
              "morphs", "gaze", "combo_expr", "outline", "keep_pose", "double_sided",
              "tex_max": 0 | 4096 | 2048 | 1024, "tex_jpeg"}
    meta     VRM 作者 / 许可信息（键见 vrmconv/vrm_ext.build_vrm1），标题用模型文件名
    fbx      {"alpha": "auto" | "keep" | "strip", "auto_facing", "face_180", "morphs", "center", "flip_z"}
    uemodel  {"alpha", "align_ref"}
    psk      {"jp_bones", "ik", "morphs", "alpha"}
    xps      {"jp_bones", "ik", "hide_unused", "double_sided", "edge"}
    jobs     同时转几个（多个文件、或一个包里的几个模型）：0 = 自动（见 parallel.jobs_for），1 = 一个一个转

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
import traceback
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "formats")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import archive  # noqa: E402
import parallel  # noqa: E402

KINDS = {".pmx": "pmx", ".vrm": "vrm", ".fbx": "fbx", ".unitypackage": "unitypackage",
         ".uemodel": "uemodel", ".psk": "psk", ".pskx": "psk", ".xps": "xps", ".zip": "zip"}
ARCHIVES = {"zip", "7z", "rar", "tar"}
CONVERTIBLE = {"pmx", "fbx", "unitypackage", "uemodel", "psk", "xps"} | ARCHIVES

DEFAULTS = {
    "target": "vrm",
    "jobs": 0,
    "output": {"mode": "beside", "folder": "", "subfolder": True},
    "vrm": {"spec": "1.0", "spring": True, "spring_skirt": True, "spring_stiffness": 1.0, "auto_spring": True, "gaze": True, "combo_expr": True,
            "morphs": True, "outline": False, "keep_pose": False, "double_sided": False,
            "tex_max": 0, "tex_jpeg": False},
    "meta": {"author": "", "version": "", "contact": "", "copyright": "", "license_url": "",
             "avatar_permission": "everyone", "commercial_usage": "personalNonProfit",
             "credit_notation": "required", "allow_redistribution": False,
             "modification": "prohibited"},
    "fbx": {"alpha": "auto", "auto_facing": True, "face_180": False, "morphs": True,
            "center": True, "flip_z": True},
    "uemodel": {"alpha": True, "align_ref": ""},
    "psk": {"jp_bones": True, "ik": True, "morphs": True, "alpha": True},
    "xps": {"jp_bones": True, "ik": True, "hide_unused": True, "double_sided": True, "edge": False},
}


def classify(path):
    """→ "pmx" / "vrm" / "fbx" / "unitypackage" / "uemodel" / "psk" / "xps" /
    "zip" / "7z" / "rar" / "tar"，不支持的为 None（分卷压缩包只认第一卷）。"""
    return KINDS.get(os.path.splitext(path)[1].lower()) or archive.kind(path)


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
    if o.get("mode") == "folder" and o.get("folder"):
        base = o["folder"]
    elif o.get("mode") == "app" and o.get("app_folder"):
        base = o["app_folder"]
    else:
        base = os.path.dirname(os.path.abspath(path))
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
    # 有的转换器只在拷贝贴图时才建输出目录：没有贴图的模型（白模）写 PMX 时目录还不存在
    os.makedirs(out_dir, exist_ok=True)
    if kind in ("fbx", "unitypackage"):
        fbxs, dest = _unpack_fbxs(path, log) if kind == "unitypackage" else ([path], None)
        try:
            return _fbxs_to_pmx(fbxs, out_dir, opts["fbx"], log)
        finally:
            if dest:
                shutil.rmtree(dest, ignore_errors=True)
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


def _unpack_fbxs(path, log):
    """unitypackage 解到临时目录（几百个资源文件，转完就删）→ ([FBX，大的在前], 临时目录)。"""
    from unitypackage_unpack import unpack
    dest = tempfile.mkdtemp(prefix="model2vrm_unitypackage_")
    with contextlib.redirect_stdout(_LogWriter(log)):
        unpack(path, dest, verbose=False)
    n = sum(len(fs) for _, _, fs in os.walk(dest))
    fbxs = [p for p in find_files(dest, ".fbx") if os.path.getsize(p) > 1024]
    log("解包 %s：%d 个文件，%d 个 FBX" % (os.path.basename(path), n, len(fbxs)), "info")
    if not fbxs:
        shutil.rmtree(dest, ignore_errors=True)
        raise RuntimeError("包里没有找到 FBX")
    fbxs.sort(key=lambda p: -os.path.getsize(p))
    return fbxs, dest


def _fbx_names(fbxs):
    """每个 FBX 的输出名；包里不同文件夹下的同名 FBX 加 _2、_3。"""
    names, used = [], set()
    for fbx in fbxs:
        base = name = os.path.splitext(os.path.basename(fbx))[0]
        k = 2
        while name.lower() in used:
            name = "%s_%d" % (base, k)
            k += 1
        used.add(name.lower())
        names.append(name)
    return names


def _fbx_to_pmx(fbx, out, name, o, log):
    import fbx2pmx
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


def _fbxs_to_pmx(fbxs, out_dir, o, log):
    """FBX → PMX（写进 out_dir，贴图在 out_dir/textures），返回 PMX 路径列表。"""
    outs = []
    for fbx, name in zip(fbxs, _fbx_names(fbxs)):
        out = os.path.join(out_dir, name + ".pmx")
        _fbx_to_pmx(fbx, out, name, o, log)
        outs.append(out)
    return outs


def fbx_to_vrm(fbx, name, out_dir, opts, log, edits=None, check_avatar=False):
    """一个 FBX → VRM；中间的 PMX 和贴图放自己的临时目录。check_avatar（包里有几个模型时）：
    只有衣服 / 配件的跳过。→ [VRM 路径] 或 []。并行时在子进程里跑（parallel.run）。"""
    work = tempfile.mkdtemp(prefix="model2vrm_work_")
    try:
        pmx = os.path.join(work, name + ".pmx")
        _fbx_to_pmx(fbx, pmx, name, opts["fbx"], log)
        if check_avatar:
            why = not_an_avatar(pmx)
            if why:
                log("跳过 %s：%s（主体模型里通常已经带着；要转换可以单独添加这个文件）" % (name, why), "warn")
                return []
        log("PMX → VRM：%s.pmx" % name, "info")
        return [pmx_to_vrm(pmx, out_dir, opts, log, edits=edits)]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _fbxs_to_vrm(fbxs, out_dir, opts, log, edits=None):
    """几个 FBX（unitypackage 里常有好几套服装）→ VRM：能并行时同时转，各自的日志前面带上模型名。
    包里某个模型失败不影响其它的，都失败才报错。"""
    names = _fbx_names(fbxs)
    multi = len(fbxs) > 1
    tasks = [(i, fbx_to_vrm, (fbx, names[i], out_dir, opts), {"edits": edits, "check_avatar": multi})
             for i, fbx in enumerate(fbxs)]
    jobs = parallel.jobs_for(len(tasks), opts.get("jobs"))
    if not multi:
        return fbx_to_vrm(fbxs[0], names[0], out_dir, opts, log, edits=edits)
    if jobs > 1:
        log("包里有 %d 个模型，同时转 %d 个" % (len(tasks), jobs), "info")
        results = parallel.run(tasks, jobs, lambda i: (lambda m, t=None: log("[%s] %s" % (names[i], m), t)))
    else:
        results = {}
        for i, func, args, kw in tasks:
            try:
                results[i] = (True, func(*args, log=log, **kw))
            except Exception as e:  # noqa: BLE001  一个模型失败不影响其它的
                results[i] = (False, ("%s: %s" % (type(e).__name__, e), traceback.format_exc()))
    outs, errors = [], []
    for i in range(len(tasks)):
        ok, r = results.get(i, (False, ("没有执行", "")))
        if ok:
            outs += r
        else:
            errors.append(r[0])
            log("%s 转换失败：%s" % (names[i], r[1] or r[0]), "err")
    if errors and not outs:
        raise RuntimeError(errors[0])
    return outs


def find_files(root, ext):
    return [os.path.join(d, f) for d, _, fs in os.walk(root) for f in fs if f.lower().endswith(ext)]


def pmx_to_vrm(pmx, out_dir, opts, log, name=None, edits=None):
    """PMX → VRM，返回 VRM 路径。edits：界面里对这个模型做的调整（见 vrm_edit），转完套上。"""
    import pmx2vrm
    o = opts["vrm"]
    stem = name or os.path.splitext(os.path.basename(pmx))[0]
    out = os.path.join(out_dir, stem + ".vrm")
    spec = "1.0" if str(o["spec"]).startswith("1") else "0.x"
    meta = dict(opts.get("meta") or {}, title=stem)
    pmx2vrm.convert(pmx, out, spec=spec, meta=meta, log=log,
                    max_morphs=None if o["morphs"] else 0,
                    force_double_sided=o["double_sided"], tpose=not o["keep_pose"],
                    outline=o["outline"], spring=o["spring"], spring_skirt=o["spring_skirt"],
                    spring_stiffness=float(o["spring_stiffness"]),
                    tex_max=int(o.get("tex_max") or 0), tex_jpeg=bool(o.get("tex_jpeg")),
                    auto_spring=bool(o.get("auto_spring", True)), gaze=bool(o.get("gaze", True)),
                    combo_expr=bool(o.get("combo_expr", True)))
    if edits:
        import vrm_edit
        if not vrm_edit.is_empty(edits):
            n = vrm_edit.apply(out, edits)
            log("已套用界面里的调整（%d 项）" % n, "info")
    return out


def convert_file(path, opts=None, log=None, out_dir=None, edits=None):
    """转换一个文件，返回输出文件列表。out_dir 不给时按 opts["output"] 决定；
    edits：这个文件在界面里的调整（vrm_edit），套到每个输出的 VRM 上。"""
    opts = merged(opts)
    log = log or (lambda m, t=None: None)
    kind = classify(path)
    if kind not in CONVERTIBLE:
        raise ValueError("不能转换这个文件：%s" % os.path.basename(path))
    out_dir = out_dir or output_dir(path, opts)
    log("→ %s" % os.path.basename(path), "head")
    log("输出目录：%s" % out_dir, "info")
    if kind in ARCHIVES:
        return _convert_archive(path, kind, out_dir, opts, log, edits)
    return _convert_model(path, kind, out_dir, opts, log, edits)


def extract_archive(path, kind, dest, log=None):
    """解压任意支持的压缩包，返回文件数。"""
    if kind == "zip":
        return extract_zip(path, dest)
    return archive.extract(path, dest, log)


def _convert_archive(path, kind, out_dir, opts, log, edits=None):
    tmp = tempfile.mkdtemp(prefix="model2vrm_%s_" % kind)
    try:
        n = extract_archive(path, kind, tmp, log)
        found = find_inputs(tmp)
        inner = [p for p in found if classify(p) in CONVERTIBLE - ARCHIVES]
        if not inner:                # 包里套包（zip 里放 7z 之类）：再解一层
            for i, p in enumerate(x for x in found if classify(x) in ARCHIVES):
                sub = os.path.join(tmp, "__inner%d" % i)
                log("解压内层压缩包 %s" % os.path.relpath(p, tmp), "info")
                n += extract_archive(p, classify(p), sub, log)
                inner += [q for q in find_inputs(sub) if classify(q) in CONVERTIBLE - ARCHIVES]
        pmx = [p for p in inner if classify(p) == "pmx"]
        todo = pmx or inner          # 有 PMX 就只转 PMX（同包里的 FBX 等通常是同一个模型）
        log("解压 %d 个文件，找到 %d 个模型：%s" % (n, len(todo), "、".join(
            os.path.relpath(p, tmp) for p in todo)), "info")
        if not todo:
            raise RuntimeError("压缩包里没有可转换的模型（PMX / FBX / uemodel / psk / xps）")
        outs = []
        for p in todo:
            log("→ %s" % os.path.relpath(p, tmp), "head")
            outs += _convert_model(p, classify(p), out_dir, opts, log, edits)
        return outs
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _convert_model(path, kind, out_dir, opts, log, edits=None):
    if kind == "pmx":
        return [pmx_to_vrm(path, out_dir, opts, log, edits=edits)]
    if opts["target"] == "pmx":
        pmxs = to_pmx(path, kind, out_dir, opts, log)
        for p in pmxs:
            log("已写出 %s" % os.path.basename(p), "ok")
        return pmxs
    # 转 VRM 时中间的 PMX 和它的贴图只是过渡：放临时目录，转完删掉，输出目录只留 VRM
    if kind in ("fbx", "unitypackage"):
        fbxs, dest = _unpack_fbxs(path, log) if kind == "unitypackage" else ([path], None)
        try:
            return _fbxs_to_vrm(fbxs, out_dir, opts, log, edits)
        finally:
            if dest:
                shutil.rmtree(dest, ignore_errors=True)
    work = tempfile.mkdtemp(prefix="model2vrm_work_")
    try:
        pmxs = to_pmx(path, kind, work, opts, log)
        log("PMX → VRM：%s" % os.path.basename(pmxs[0]), "info")
        return [pmx_to_vrm(p, out_dir, opts, log, edits=edits) for p in pmxs]
    finally:
        shutil.rmtree(work, ignore_errors=True)


def not_an_avatar(pmx_path):
    """看起来不是一个完整角色（只有衣服 / 配件）时返回原因，否则 None。

    VRChat 等的模型包里常有单独的服装 FBX：骨架是完整的，网格只有衣服。判断：头部（头骨及其子骨，
    包括头发、眼睛）没挂任何顶点，或者两只手都没挂顶点。骨架里找不到头 / 手时不判断。
    """
    import pmxio
    from vrmconv import mesh
    from vrmconv.humanoid import _map_humanoid
    m = pmxio.read_pmx(pmx_path)
    human = _map_humanoid(m["bones"], True, {})
    parents = [b["parent"] for b in m["bones"]]

    def subtree(root):
        out, grew = {root}, True
        while grew:
            grew = False
            for i, p in enumerate(parents):
                if p in out and i not in out:
                    out.add(i)
                    grew = True
        return out

    if "head" not in human or "leftHand" not in human or "rightHand" not in human:
        return None
    weights = mesh.vertex_weights(m["vertices"], len(m["bones"]), {})
    used = {b for wl in weights for b, w in wl if w > 0.3}
    if not used & subtree(human["head"]):
        return "头部没有网格，看起来只有衣服或配件"
    if not used & (subtree(human["leftHand"]) | subtree(human["rightHand"])):
        return "手上没有网格，看起来只有衣服或配件"
    return None
