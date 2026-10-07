# -*- coding: utf-8 -*-
"""转换流程（与界面无关）：识别输入格式 → 必要时先转 PMX → 再转 VRM。

    import pipeline
    outs = pipeline.convert_file(path, opts, log)    # → [输出文件路径, ...]

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
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "formats")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

KINDS = {".pmx": "pmx", ".vrm": "vrm", ".fbx": "fbx", ".unitypackage": "unitypackage",
         ".uemodel": "uemodel", ".psk": "psk", ".pskx": "psk", ".xps": "xps"}
CONVERTIBLE = {"pmx", "fbx", "unitypackage", "uemodel", "psk", "xps"}

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
    """→ "pmx" / "vrm" / "fbx" / "unitypackage" / "uemodel" / "psk" / "xps"，不支持的为 None。"""
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


def output_dir(path, opts):
    """按 output 选项决定这个源文件的输出目录（会建好）。"""
    o = opts["output"]
    base = o.get("folder") if o.get("mode") == "folder" and o.get("folder") else \
        os.path.dirname(os.path.abspath(path))
    if o.get("subfolder", True):
        stem = os.path.splitext(os.path.basename(path))[0]
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
