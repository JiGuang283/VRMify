# -*- coding: utf-8 -*-
"""转换好的 VRM 再调整（不用重新转换）：预设表情指定和强度、视线范围、SpringBone 参数、标题、缩略图。

    import vrm_edit
    info = vrm_edit.describe(path)          # 界面要的：可选表情、当前预设指定、物理链、标题
    vrm_edit.apply(path, edits)             # 套用调整，原地改写文件

edits（纯 JSON，界面按源文件存）：

    {"title": "名字",
     "expressions": {"happy": "笑い", "blink": "", ...},   预设 → 来源表情 id（"" = 不设这个预设）
     "intensity": {"angry": 0.8, ...},                    预设强度 ×（形态键权重相乘，单个权重最多到 1）
     "lookAt": {"h": 15.0, "down": 10.0, "up": 7.0},       视线最大转角（度）：左右 / 向下 / 向上；不给 = 转换时的值
     "spring": {"stiffness": 1.0, "drag": 1.0, "gravity": 0.0,       整体：硬度 ×、阻尼 ×、重力 +
                "chains": {"链 key": {"stiffness": 1.0, "gravity": 0.0, "off": false}}},
     "thumbnail": "缩略图 PNG 的路径"}

可重复套用：第一次调整时把原始的表情 / 物理 / meta 存进 glTF 根节点的 extras.vrmconv_base，
以后每次都先还原再套用，不会越调越歪；edits 为空就是还原成转换时的样子。
来源表情 id：转换时给预设表情记了来源 PMX 表情名（extras.source），自定义表情就是它的名字。
"""
import copy
import os
import time

import vrmio
from vrmconv import morphbake

PRESETS_1 = ["happy", "angry", "sad", "relaxed", "surprised", "neutral",
             "aa", "ih", "ou", "ee", "oh", "blink", "blinkLeft", "blinkRight",
             "lookUp", "lookDown", "lookLeft", "lookRight"]
PRESETS_0 = ["joy", "angry", "sorrow", "fun", "neutral", "a", "i", "u", "e", "o",
             "blink", "blink_l", "blink_r", "lookup", "lookdown", "lookleft", "lookright"]
BASE_KEY = "vrmconv_base"
SPRING_DEFAULTS = {"stiffness": 1.0, "gravityPower": 0.0, "dragForce": 0.5}


def _uniq(name, used):
    if name not in used:
        return name
    k = 2
    while "%s_%d" % (name, k) in used:
        k += 1
    return "%s_%d" % (name, k)


def _num(v, default):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    return v if v == v and abs(v) < 1e6 else default


# ------------------------------------------------------------- baseline --
def _base(g, create):
    """glTF 根 extras 里的原始值；create=True 时没有就现在记下来。"""
    ext = g.get("extensions", {})
    b = g.get("extras", {}).get(BASE_KEY)
    if b is not None or not create:
        return b
    if "VRMC_vrm" in ext:
        v = ext["VRMC_vrm"]
        b = {"spec": "1.0", "expressions": copy.deepcopy(v.get("expressions")),
             "meta": copy.deepcopy(v["meta"]),
             "springBone": copy.deepcopy(ext.get("VRMC_springBone"))}
    else:
        v = ext["VRM"]
        b = {"spec": "0.x", "groups": copy.deepcopy(v.get("blendShapeMaster", {}).get("blendShapeGroups", [])),
             "meta": copy.deepcopy(v["meta"])}
    b["thumb"] = None
    _base_look(b, v)
    g.setdefault("extras", {})[BASE_KEY] = b
    return b


_LOOK_1 = {"h": ("rangeMapHorizontalInner", "rangeMapHorizontalOuter"), "down": ("rangeMapVerticalDown",),
           "up": ("rangeMapVerticalUp",)}
_LOOK_0 = {"h": ("lookAtHorizontalInner", "lookAtHorizontalOuter"), "down": ("lookAtVerticalDown",),
           "up": ("lookAtVerticalUp",)}


def _base_look(b, vrm):
    """基准里记下转换时的视线设置（以前调整过、基准里还没有这一项的文件，补记当前的——
    那时还没有视线调整，当前值就是转换时的值）。"""
    if "look" not in b:
        b["look"] = copy.deepcopy(vrm.get("lookAt") if b["spec"] == "1.0" else
                                  {k: vrm.get("firstPerson", {}).get(k) for ks in _LOOK_0.values() for k in ks})


def _look_deg(b):
    """基准里的视线最大转角 → {"h", "down", "up"}（度），没有视线设置时 None。"""
    look = b.get("look") or {}
    out = {}
    for k, names in (_LOOK_1 if b["spec"] == "1.0" else _LOOK_0).items():
        m = look.get(names[0])
        if not m:
            return None
        out[k] = m.get("outputScale") if b["spec"] == "1.0" else m.get("yRange")
    return out


def _apply_look(vrm, b, edits, g=None):
    """视线：先还原转换时的设置，再套用 edits（左右 / 向下 / 向上的最大转角）。
    转换时补的视线表情（lookLeft 等，横向场 + 深度场两个绑定，见 morphbake.split_gaze）按新角度重算权重。"""
    is1 = b["spec"] == "1.0"
    if is1:
        if b.get("look") is not None:
            vrm["lookAt"] = copy.deepcopy(b["look"])
    else:
        for k, m in (b.get("look") or {}).items():
            if m is not None:
                vrm.setdefault("firstPerson", {})[k] = copy.deepcopy(m)
    if not edits or _look_deg(b) is None:
        return
    target = vrm["lookAt"] if is1 else vrm["firstPerson"]
    for k, names in (_LOOK_1 if is1 else _LOOK_0).items():
        deg = _num(edits.get(k), None)
        if deg is None:
            continue
        deg = min(90.0, max(0.0, deg))
        for n in names:
            target[n]["outputScale" if is1 else "yRange"] = deg
    if g is not None:
        _gaze_weights(g, vrm, is1, edits)


def _target_name(g, bind, is1):
    """表情绑定指向的 morph target 名（找不到时 None）。1.0 的 node 是网格节点，0.x 的 mesh 是网格号。"""
    try:
        m = g["nodes"][bind["node"]]["mesh"] if is1 else bind["mesh"]
        names = g["meshes"][m]["extras"]["targetNames"]
        return names[bind["index"]] if 0 <= bind["index"] < len(names) else None
    except (KeyError, IndexError, TypeError):
        return None


def _gaze_weights(g, vrm, is1, edits):
    depth = set(morphbake.GAZE_DEPTH.values())
    for p, key in morphbake.GAZE_KEY.items():
        deg = _num(edits.get(key), None)
        if deg is None:
            continue
        axis = "h" if key == "h" else "v"
        wt, wd = morphbake.gaze_weights(deg, axis)
        if is1:
            obj = (vrm.get("expressions") or {}).get("preset", {}).get(p)
            binds = obj and (obj.get("extras") or {}).get("source") == p and obj.get("morphTargetBinds")
            scale = 1.0
        else:
            gr = next((x for x in vrm.get("blendShapeMaster", {}).get("blendShapeGroups", [])
                       if x.get("name") == p and x.get("presetName") == p.lower()), None)
            binds = gr and gr.get("binds")
            scale = 100.0
        # 只认转换时补的那种：横向场 + 深度场（眼睛分在几个网格里时每个网格各一对）
        names = [_target_name(g, bd, is1) for bd in binds or ()]
        if not names or None in names or len(set(names)) != 2 or not depth.intersection(names):
            continue
        for bd, n in zip(binds, names):
            bd["weight"] = round((wd if n in depth else wt) * scale, 6)


def _apply_intensity(vrm, is1, edits):
    """预设强度：把预设里的形态键权重乘上系数（单个权重最多到 1 / 100）。材质颜色、贴图偏移不变。"""
    for p, f in (edits or {}).items():
        f = _num(f, 1.0)
        if f == 1.0:
            continue
        f = max(0.0, f)
        if is1:
            obj = ((vrm.get("expressions") or {}).get("preset") or {}).get(p)
            for bd in (obj or {}).get("morphTargetBinds", []):
                bd["weight"] = round(min(1.0, bd["weight"] * f), 4)
        else:
            for gr in vrm.get("blendShapeMaster", {}).get("blendShapeGroups", []):
                if gr.get("presetName") == p:
                    for bd in gr.get("binds", []):
                        bd["weight"] = round(min(100.0, bd["weight"] * f), 2)


def _sources_1(expressions):
    """VRM 1.0 原始表情 → (pool {id: 表情}, 预设指定 {预设: id})。"""
    pool, assign = {}, {}
    for p, obj in ((expressions or {}).get("preset") or {}).items():
        sid = (obj.get("extras") or {}).get("source") or "@" + p
        if sid in pool:
            sid = "@" + p
        pool[sid] = obj
        assign[p] = sid
    for name, obj in ((expressions or {}).get("custom") or {}).items():
        pool[_uniq(name, pool)] = obj
    return pool, assign


def _sources_0(groups):
    pool, assign = {}, {}
    for gr in groups:
        sid = _uniq(gr.get("name") or "expression", pool)
        pool[sid] = gr
        p = str(gr.get("presetName") or "unknown").lower()
        if p in PRESETS_0 and p not in assign:
            assign[p] = sid
    return pool, assign


def _chain_keys(springs):
    keys, used = [], set()
    for i, sp in enumerate(springs or []):
        k = _uniq(sp.get("name") or "spring%d" % i, used)
        used.add(k)
        keys.append(k)
    return keys


def _label(sid):
    return sid[1:] if sid.startswith("@") else sid


# -------------------------------------------------------------- describe --
def describe(path):
    """→ 界面要的信息（都按转换时的原始值，不含已套用的调整）。"""
    g, _ = vrmio.read_glb(path)
    ext = g.get("extensions", {})
    if "VRMC_vrm" not in ext and "VRM" not in ext:
        raise ValueError("不是 VRM 文件")
    b = _base(g, create=True)
    out = {"spec": b["spec"], "springs": []}
    if b["spec"] == "1.0":
        pool, assign = _sources_1(b["expressions"])
        out["presets"] = PRESETS_1
        out["title"] = b["meta"].get("name", "")
        sb = b.get("springBone") or {}
        for k, sp in zip(_chain_keys(sb.get("springs")), sb.get("springs") or []):
            out["springs"].append({
                "key": k, "name": sp.get("name") or k,
                "joints": [{"node": j["node"], **{f: j.get(f, d) for f, d in SPRING_DEFAULTS.items()}}
                           for j in sp.get("joints", [])]})
    else:
        pool, assign = _sources_0(b["groups"])
        out["presets"] = PRESETS_0
        out["title"] = b["meta"].get("title", "")
    out["expressions"] = [{"id": sid, "label": _label(sid)} for sid in pool]
    out["assign"] = assign
    _base_look(b, ext["VRMC_vrm"] if b["spec"] == "1.0" else ext["VRM"])
    out["look"] = _look_deg(b)                  # 转换时的视线最大转角（没有视线设置时 None）
    return out


# ----------------------------------------------------------------- apply --
def _apply_expr_1(vrm, base_expr, edits):
    pool, assign = _sources_1(base_expr)
    for p, sid in (edits or {}).items():
        if p in PRESETS_1:
            if not sid:
                assign.pop(p, None)
            elif sid in pool:
                assign[p] = sid
    preset, custom, used = {}, {}, set(assign.values())
    for p in PRESETS_1:
        sid = assign.get(p)
        if sid:
            obj = copy.deepcopy(pool[sid])
            if sid.startswith("@"):
                obj.pop("extras", None)
            else:
                obj["extras"] = dict(obj.get("extras") or {}, source=sid)
            preset[p] = obj
    for sid, obj in pool.items():
        if sid in used:
            continue
        obj = copy.deepcopy(obj)
        obj.pop("extras", None)
        # 规范：自定义表情名不能和预设名重名
        name = _uniq(_label(sid) if not sid.startswith("@") else "orig_" + sid[1:],
                     set(custom) | set(PRESETS_1))
        custom[name] = obj
    expressions = {}
    if preset:
        expressions["preset"] = preset
    if custom:
        expressions["custom"] = custom
    if expressions:
        vrm["expressions"] = expressions
    else:
        vrm.pop("expressions", None)


def _apply_expr_0(vrm, base_groups, edits):
    pool, assign = _sources_0(base_groups)
    for p, sid in (edits or {}).items():
        if p in PRESETS_0:
            if not sid:
                assign.pop(p, None)
            elif sid in pool:
                assign[p] = sid
    by_sid = {}
    for p, sid in assign.items():
        by_sid.setdefault(sid, []).append(p)
    groups = []
    for sid, gr in pool.items():
        ps = by_sid.get(sid) or ["unknown"]
        for k, p in enumerate(ps):         # 一个表情指定给多个预设：复制一份
            ng = copy.deepcopy(gr)
            ng["presetName"] = p
            if k:
                ng["name"] = "%s_%s" % (ng.get("name") or sid, p)
            groups.append(ng)
    vrm.setdefault("blendShapeMaster", {})["blendShapeGroups"] = groups


def _apply_spring(g, base_sb, edits):
    ext = g["extensions"]
    used = g.setdefault("extensionsUsed", [])
    if base_sb is None:
        return
    sb = copy.deepcopy(base_sb)
    if edits:
        gs = _num(edits.get("stiffness"), 1.0)
        gd = _num(edits.get("drag"), 1.0)
        gg = _num(edits.get("gravity"), 0.0)
        chains = edits.get("chains") or {}
        springs = []
        for k, sp in zip(_chain_keys(sb.get("springs")), sb.get("springs") or []):
            c = chains.get(k) or {}
            if c.get("off"):
                continue
            cs, cg = _num(c.get("stiffness"), 1.0), _num(c.get("gravity"), 0.0)
            for j in sp.get("joints", []):
                st = j.get("stiffness", SPRING_DEFAULTS["stiffness"])
                gp = j.get("gravityPower", SPRING_DEFAULTS["gravityPower"])
                dr = j.get("dragForce", SPRING_DEFAULTS["dragForce"])
                j["stiffness"] = round(max(0.0, st * gs * cs), 4)
                j["gravityPower"] = round(max(0.0, gp + gg + cg), 4)
                j["dragForce"] = round(min(1.0, max(0.0, dr * gd)), 4)
            springs.append(sp)
        sb["springs"] = springs
    if sb.get("springs"):
        ext["VRMC_springBone"] = sb
        if "VRMC_springBone" not in used:
            used.append("VRMC_springBone")
    else:                                   # 全部关掉：规范要求 springs 非空，整个扩展去掉
        ext.pop("VRMC_springBone", None)
        if "VRMC_springBone" in used:
            used.remove("VRMC_springBone")


def _drop_thumb(g, bin_data, thumb):
    """去掉上一次加的缩略图。它总是追加在最后，在末尾时连数据一起截掉。"""
    if not thumb:
        return bin_data
    tex, img, bv = thumb.get("texture"), thumb.get("image"), thumb.get("bufferView")
    if tex is not None and tex == len(g.get("textures", [])) - 1:
        g["textures"].pop()
        if not g["textures"]:
            del g["textures"]
    if img is not None and img == len(g.get("images", [])) - 1:
        g["images"].pop()
        if not g["images"]:
            del g["images"]
    bvs = g.get("bufferViews", [])
    if bv is not None and bv == len(bvs) - 1:
        v = bvs.pop()
        if v.get("byteOffset", 0) + v["byteLength"] >= len(bin_data) - 3:
            bin_data = bin_data[:v.get("byteOffset", 0)]
    return bin_data


def _add_thumb(g, bin_data, png, is1):
    bin_data = bytearray(bin_data)
    while len(bin_data) % 4:
        bin_data += b"\x00"
    off = len(bin_data)
    bin_data += png
    bvs = g.setdefault("bufferViews", [])
    bvs.append({"buffer": 0, "byteOffset": off, "byteLength": len(png)})
    imgs = g.setdefault("images", [])
    mime = "image/jpeg" if png[:3] == b"\xff\xd8\xff" else "image/png"
    imgs.append({"name": "thumbnail", "bufferView": len(bvs) - 1, "mimeType": mime})
    thumb = {"bufferView": len(bvs) - 1, "image": len(imgs) - 1, "texture": None}
    if not is1:                              # 0.x 的 meta.texture 是贴图（texture）序号
        texs = g.setdefault("textures", [])
        if not g.get("samplers"):
            g["samplers"] = [vrmio.default_sampler()]
        texs.append({"sampler": 0, "source": len(imgs) - 1})
        thumb["texture"] = len(texs) - 1
    return bytes(bin_data), thumb


def is_empty(edits):
    if not edits:
        return True
    sp = edits.get("spring") or {}
    neutral_spring = (_num(sp.get("stiffness"), 1.0) == 1.0 and _num(sp.get("drag"), 1.0) == 1.0
                      and _num(sp.get("gravity"), 0.0) == 0.0
                      and not any(c.get("off") or _num(c.get("stiffness"), 1.0) != 1.0
                                  or _num(c.get("gravity"), 0.0) != 0.0
                                  for c in (sp.get("chains") or {}).values()))
    return (not edits.get("title") and not edits.get("expressions") and neutral_spring
            and not edits.get("thumbnail") and not edits.get("lookAt")
            and all(_num(v, 1.0) == 1.0 for v in (edits.get("intensity") or {}).values()))


def apply(path, edits, out_path=None):
    """把 edits 套到 VRM 上（先还原成转换时的样子再套），写到 out_path（默认原地）。返回改了几项。"""
    g, bin_data = vrmio.read_glb(path)
    ext = g.get("extensions", {})
    if "VRMC_vrm" not in ext and "VRM" not in ext:
        raise ValueError("不是 VRM 文件")
    edits = edits or {}
    empty = is_empty(edits)
    b = _base(g, create=not empty)
    if b is None:                       # 从没调过、这次也没有调整
        if out_path and out_path != path:
            vrmio.write_glb(g, bin_data, out_path)
        return 0
    is1 = b["spec"] == "1.0"
    vrm = ext["VRMC_vrm"] if is1 else ext["VRM"]
    # ---- 还原
    bin_data = _drop_thumb(g, bin_data, b.get("thumb"))
    b["thumb"] = None
    vrm["meta"] = copy.deepcopy(b["meta"])
    n = 0
    # ---- 套用
    _base_look(b, vrm)
    if is1:
        _apply_expr_1(vrm, b["expressions"], edits.get("expressions"))
        _apply_spring(g, b.get("springBone"), edits.get("spring"))
    else:
        _apply_expr_0(vrm, b["groups"], edits.get("expressions"))
    _apply_intensity(vrm, is1, edits.get("intensity"))
    _apply_look(vrm, b, edits.get("lookAt"), g)
    n += bool(edits.get("expressions")) + bool(edits.get("spring")) + bool(edits.get("lookAt"))
    n += any(_num(v, 1.0) != 1.0 for v in (edits.get("intensity") or {}).values())
    if edits.get("title"):
        vrm["meta"]["name" if is1 else "title"] = str(edits["title"])
        n += 1
    thumb = edits.get("thumbnail")
    if thumb and os.path.isfile(thumb):
        with open(thumb, "rb") as f:
            png = f.read()
        bin_data, b["thumb"] = _add_thumb(g, bin_data, png, is1)
        if is1:
            vrm["meta"]["thumbnailImage"] = b["thumb"]["image"]
        else:
            vrm["meta"]["texture"] = b["thumb"]["texture"]
        n += 1
    if empty:                           # 全部还原：不留痕迹
        del g["extras"][BASE_KEY]
        if not g["extras"]:
            del g["extras"]
    if g.get("buffers"):
        g["buffers"][0]["byteLength"] = len(bin_data)
    _write(g, bin_data, out_path or path)
    return n


def _write(g, bin_data, path):
    tmp = path + ".tmp"
    vrmio.write_glb(g, bin_data, tmp)
    for k in range(20):                 # 预览正在读这个文件时 Windows 不让替换，稍等再试
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if k == 19:
                raise
            time.sleep(0.1)
