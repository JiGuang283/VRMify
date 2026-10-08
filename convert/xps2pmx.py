# -*- coding: utf-8 -*-
"""XPS / XNALara → MMD PMX，纯 Python 标准库。

和项目里其它转换器保持同一套口径（换轴 → 归一到 20 单位 → 居中 → 骨骼转
MMD 标准日文名 → 材质按名字找贴图），差别只在本格式的坐标系：

换轴
----
实测样本（轩辕剑柒 chu hong）：
    `head eyeball left` 在 +X、`head eyeball right` 在 -X   → XPS 的 +X = **左**
    `head nose tip` z=1.030 > 头根 z=0.925，
    `leg left toes`  z=0.974 > 脚踝 z=0.879                  → XPS 的 +Z = **正面**
    `root ground` 在 y=0、头顶 y≈1.71                        → XPS 的 +Y = **上**
也就是「左手 +X、正面 +Z、上 +Y」的右手系。

MMD/PMX 的约定是「左手 +X、正面 -Z、上 +Y」（见 psk2pmx.py 里同一段推导）。
两边只差**一个 Z 的符号**，所以：

    (x, y, z)_xps → (x, y, -z)_pmx

这是**一次反射**（行列式 -1，右手系 → MMD 的左手系），几何绕序会跟着翻，
所以绕序要么按法线投票自动判、要么在没法线时一律反转 —— 见下面「绕序」段。

注意**不能**照抄 uemodel2pmx 的 (x, z, -y)，也不能照抄 psk2pmx 的 (x, z, y)：
那两个是给各自格式准备的，套到 XPS 上会把人物转成躺倒或背对镜头。

骨骼
----
XPS 的骨名是英文描述式（`spine lower` / `arm left elbow` / `leg right knee`），
按规则转成 MMD 标准日文名，英文原名写进 PMX 的 name_en 字段，两边都不丢。
以 `unused ` 开头的占位骨（XNALara 用来标记「不在姿势界面显示」的骨）默认
保留在骨架里（权重可能引用它们，删了会破面），但放进独立的「unused」表示枠，
免得主枠里 100 多根骨挤成一团。

贴图
----
XPS 每个网格带一组贴图，第 0 个是漫反射（实测样本 4 张一组时顺序是
`_d / _l / _n / _s`）。贴图原样拷进输出目录的 `textures/`。**不去 alpha** ——
XPS 的 alpha 就是头发/睫毛的透明度（和 UE 的「alpha 是数据遮罩」正好相反），
去掉的话头发会变成实心板。DDS 也照拷：MMD 官方支持 .dds 贴图（见 VPVP wiki
的「拡張子」表），不需要转码。
"""
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _sub in ("formats", "convert"):
    _p = os.path.join(BASE, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import xpsio
import pmxio

# toon 一律不使用：唯一正确的写法是 flag=0 / index=-1（原因见 psk2pmx.py 顶部）
_TOON_NONE_FLAG = 0
_TOON_NONE_INDEX = -1

_TEX_EXT = (".png", ".bmp", ".tga", ".jpg", ".jpeg", ".dds", ".hdr")


def _log_default(msg, tag=None):
    print(msg)


# ------------------------------------------------------------- 骨名映射 ------
# XPS 的骨名是英文描述式，先按「整名」查表，再按「部位 + 左右」规则拆。
_SIDE_JP = {"left": "左", "right": "右", "l": "左", "r": "右",
            "lt": "左", "rt": "右"}

# 主干（整名精确匹配，小写、空格归一化后比对）
_EXACT = {
    "root ground": "全ての親",
    "root": "全ての親",
    "root hips": "センター",
    "root pelvis": "センター",
    "hips": "センター",
    "pelvis": "センター",
    "spine lower": "上半身",
    "spine": "上半身",
    "spine middle": "上半身2",
    "chest": "上半身2",
    "head neck lower": "首",
    "neck": "首",
    "head neck upper": "頭",
    "head": "頭",
}

# 部位关键词 → MMD 骨名（不带左右）
_PART = {
    "shoulder": "肩",
    "clavicle": "肩",
    "upperarm": "腕",
    "upper arm": "腕",
    "arm": "腕",
    "forearm": "ひじ",
    "elbow": "ひじ",
    "wrist": "手首",
    "hand": "手首",
    "thigh": "足",
    "upper leg": "足",
    "upperleg": "足",
    "knee": "ひざ",
    "shin": "ひざ",
    "calf": "ひざ",
    "ankle": "足首",
    "foot": "足首",
    "toes": "つま先",
    "toe": "つま先",
}

# 手指：XPS 的 finger 1..5，其中 1 是拇指（实测「arm left finger 1a」相对
# 其余四指在 Y 上明显错开，是拇指的特征）。段号 a/b/c → 0/1/2。
_FINGER_JP = {1: "親指", 2: "人指", 3: "中指", 4: "薬指", 5: "小指"}
_FW = ("０", "１", "２", "３", "４", "５")
_FINGER_RE = re.compile(r"^(?:arm|hand)?\s*([lr]|left|right)[\s_-]*"
                        r"(?:finger|thumb|index|middle|ring|pinky|little)"
                        r"[\s_-]*(\d)([a-d]?)$")


def _norm(name):
    """骨名归一化：小写、下划线/连字符→空格、压缩空格。"""
    s = (name or "").strip().lower()
    s = s.replace("_", " ").replace("-", " ").replace(".", " ")
    return " ".join(s.split())


def bone_jp(name):
    """XPS 骨名 → MMD 日文名；没有对应条目返回 None（调用方保留原名）。"""
    n = _norm(name)
    if not n:
        return None
    if n in _EXACT:
        return _EXACT[n]
    if n.startswith("unused"):
        return None                     # 占位骨不映射

    # 手指：arm left finger 2b / l finger 3a ...
    m = _FINGER_RE.match(n)
    if m:
        side, finger, seg = m.group(1), int(m.group(2)), m.group(3)
        jp = _FINGER_JP.get(finger)
        if jp and side in _SIDE_JP:
            # 拇指从 ０ 起，其余四指从 １ 起（MMD 标准骨名惯例）
            base = 0 if finger == 1 else 1
            idx = base + (ord(seg) - ord("a") if seg else 0)
            if 0 <= idx < len(_FW):
                return "%s%s%s" % (_SIDE_JP[side], jp, _FW[idx])
        return None

    # 眼珠：head eyeball left / left eye ...
    if "eyeball" in n or n.endswith(" eye") or n == "eye":
        side = None
        for tok in n.split():
            if tok in _SIDE_JP:
                side = _SIDE_JP[tok]
        if side:
            return side + "目"
        return None

    # 四肢：arm left elbow / leg right knee / left upper arm ...
    side = None
    for tok in n.split():
        if tok in _SIDE_JP:
            side = _SIDE_JP[tok]
            break
    if not side:
        return None
    body = " ".join(t for t in n.split() if t not in _SIDE_JP)
    body = body.replace("arm ", "").replace("leg ", "").strip()
    if body in _PART:
        return side + _PART[body]
    # 带序号的部位：XPS 的 `arm left shoulder 1 / 2` 里 1 是锁骨、2 是大臂
    m2 = re.match(r"^(.+?)\s*(\d+)$", body)
    if m2:
        stem, num = m2.group(1), m2.group(2)
        if stem == "shoulder":
            return side + ("肩" if num == "1" else "腕")
        if stem in _PART:
            return side + _PART[stem]
    for key, jp in _PART.items():
        if body.endswith(key):
            return side + jp
    return None


# ----------------------------------------------------------------- 小工具 ----
def _uniq(name, used):
    base = (name or "").strip() or "bone"
    if base not in used:
        used.add(base)
        return base
    i = 1
    while "%s_%d" % (base, i) in used:
        i += 1
    nm = "%s_%d" % (base, i)
    used.add(nm)
    return nm


def _clean_name(name):
    """网格名（如 `24_Material25_0.25_0_0`）→ 给 MMD 看的材质名。

    XPS 的网格名常常是「渲染参数 + 材质名 + 一堆数值」拼起来的，末段那些
    `_0.25_0_0` 是着色器参数，对用户没意义，去掉后更干净；去不干净就原样保留。
    """
    s = (name or "").strip()
    if not s:
        return ""
    m = re.match(r"^\d+_(.+?)(?:_\d+(?:\.\d+)?)+$", s)
    if m and m.group(1):
        return m.group(1)
    return s


def _find_texture(folder, filename):
    """按 XPS 里记的贴图名找文件（同目录 + Textures 子目录，大小写不敏感）。"""
    raw = (filename or "").strip().replace("\\", "/")
    if not raw:
        return None
    base = os.path.basename(raw)
    if not base:
        return None
    dirs = [folder]
    for sub in ("Textures", "textures", "Texture", "tex"):
        d = os.path.join(folder, sub)
        if os.path.isdir(d):
            dirs.append(d)
    parent = os.path.dirname(folder)
    for sub in ("Textures", "textures"):
        d = os.path.join(parent, sub)
        if os.path.isdir(d):
            dirs.append(d)

    # 先按原名精确找
    for d in dirs:
        p = os.path.join(d, base)
        if os.path.isfile(p):
            return p
    # 再退一步：同目录（含子目录）里按小写名 + 换扩展名找
    stem = os.path.splitext(base)[0].lower()
    for d in dirs:
        try:
            for f in os.listdir(d):
                st, ext = os.path.splitext(f)
                if ext.lower() in _TEX_EXT and st.lower() == stem:
                    return os.path.join(d, f)
        except OSError:
            pass
    return None


def _copy_tex(src, dst):
    """原样拷贴图（XPS 的 alpha 是真正的透明度，不能去）。"""
    shutil.copyfile(src, dst)
    return "copied"


# ------------------------------------------------------------------- 主体 ----
def convert(path, out_path, scale_mode="mmd", log=None, name=None,
            enable_edge=False, force_double_sided=False, textures=True,
            center=True, jp_bones=True, make_ik=True, hide_unused=True,
            all_double_sided=True, fbx_path=None):
    """XPS / XNALara → PMX。返回统计 dict。"""
    _l = log or _log_default

    m = xpsio.read_xps(path, log=_l)
    kind = "XPS" if m.is_generic2 else "XNALara"
    if not m.meshes:
        raise ValueError("这个文件里没有网格数据")
    _l("读取 %s：骨骼 %d 根 · 网格 %d 个 · 顶点 %d · 三角面 %d"
       % (kind, len(m.bones), len(m.meshes), m.n_vertices, m.n_triangles))

    # ---- 坐标变换：一次 Z 取反（反射），详见文件顶部说明
    def xf(p):
        return (p[0], p[1], -p[2])

    # ---- 所有网格的顶点拼在一起（PMX 只有一张顶点表）
    raw_pos = []
    raw_nrm = []
    raw_uv = []
    raw_w = []                  # [(bone_index, weight), ...]
    mesh_vert_start = []        # 每个网格在顶点表里的起始下标
    for mesh in m.meshes:
        mesh_vert_start.append(len(raw_pos))
        nv = len(mesh.positions)
        for vi in range(nv):
            raw_pos.append(mesh.positions[vi])
            n = mesh.normals[vi]
            raw_nrm.append(n if n and (n[0] or n[1] or n[2]) else None)
            raw_uv.append(mesh.uvs[vi] or (0.0, 0.0))
            idxs = mesh.bone_indices[vi] or ()
            wts = mesh.bone_weights[vi] or ()
            w = [(int(b), float(wt)) for b, wt in zip(idxs, wts)
                 if int(b) < len(m.bones) and wt > 1e-6]
            raw_w.append(w)

    n_vert = len(raw_pos)
    pts = [xf(p) for p in raw_pos]
    ys = [p[1] for p in pts]
    lo_y, hi_y = (min(ys), max(ys)) if ys else (0.0, 1.0)
    height = max(hi_y - lo_y, 1e-6)

    if scale_mode in ("mmd", "auto", None):
        scale = 20.0 / height
        _l("缩放：源高 %.3f 单位 × %.6f → %.2f（MMD 常规 20）"
           % (height, scale, height * scale))
    elif scale_mode in ("raw", "none", 1, 1.0):
        scale = 1.0
        _l("缩放：保持源尺寸（scale=1.0），源高 %.3f 单位" % height)
    else:
        scale = float(scale_mode)
        _l("缩放：源高 %.3f 单位 × %.6f → %.2f"
           % (height, scale, height * scale))

    if center and pts:
        xs = [p[0] for p in pts]
        zs = [p[2] for p in pts]
        off = (-(min(xs) + max(xs)) * 0.5 * scale,
               -lo_y * scale,
               -(min(zs) + max(zs)) * 0.5 * scale)
        _l("居中：偏移 (%.3f, %.3f, %.3f)" % off, "info")
    else:
        off = (0.0, 0.0, 0.0)

    def sp(p):
        t = xf(p)
        return (t[0] * scale + off[0], t[1] * scale + off[1],
                t[2] * scale + off[2])

    def sn(n):
        """法线：只换轴（反射会把法线也翻一次，所以 Z 同样取反）。"""
        t = xf(n)
        ln = (t[0] ** 2 + t[1] ** 2 + t[2] ** 2) ** 0.5 or 1.0
        return (t[0] / ln, t[1] / ln, t[2] / ln)

    # ---- 骨骼：世界坐标就是 XPS 里存的位置（不需要累乘父级）
    bone_pos = [sp(b.position) for b in m.bones]
    n_src = len(m.bones)

    # ---- 骨骼顺序：DFS，保证父在子前面（PMX 要求父索引 < 子索引）
    kids = [[] for _ in range(n_src)]
    roots = []
    for i, b in enumerate(m.bones):
        p = b.parent_index
        if 0 <= p < n_src and p != i:
            kids[p].append(i)
        else:
            roots.append(i)
    order = []
    seen = [False] * n_src
    stack = list(reversed(roots))
    while stack:
        i = stack.pop()
        if seen[i]:
            continue
        seen[i] = True
        order.append(i)
        stack.extend(reversed(kids[i]))
    for i in range(n_src):
        if not seen[i]:
            seen[i] = True
            order.append(i)

    slot = {bi: k for k, bi in enumerate(order)}
    used = set()
    pmx_bones = []
    n_renamed = 0
    n_unused = 0
    for src in order:
        b = m.bones[src]
        jp = bone_jp(b.name) if jp_bones else None
        main_name = jp if jp else (b.name or "bone")
        if jp:
            n_renamed += 1
        p = b.parent_index if 0 <= b.parent_index < n_src else -1
        pmx_bones.append({
            "name": _uniq(main_name, used),
            "name_en": b.name or "bone",
            "pos": bone_pos[src],
            "parent": slot.get(p, -1),
            "layer": 0, "flag": 0, "tail_kind": 0, "tail": (0.0, 0.0, 0.0),
            "inherit_rot": None, "inherit_mov": None, "fixed_axis": None,
            "local_axis": None, "external": None, "ik": None,
            "_kids": [], "_unused": (b.name or "").lower().startswith("unused"),
        })
    for src in order:
        b = m.bones[src]
        p = b.parent_index if 0 <= b.parent_index < n_src else -1
        if p in slot and slot[p] != slot[src]:
            pmx_bones[slot[p]]["_kids"].append(slot[src])

    for bk in pmx_bones:
        flag = 0x0002 | 0x0004 | 0x0008 | 0x0010      # 可旋转/可移动/可显示/可操作
        k = bk.pop("_kids")
        if bk.pop("_unused"):
            n_unused += 1
            bk["_is_unused"] = True
        else:
            bk["_is_unused"] = False
        if k:
            flag |= 0x0001                            # 尾 = 骨骼（取第一个子骨骼）
            bk["tail_kind"] = 1
            bk["tail"] = k[0]
        else:
            p = bk["parent"]
            if 0 <= p < len(pmx_bones):
                d = (bk["pos"][0] - pmx_bones[p]["pos"][0],
                     bk["pos"][1] - pmx_bones[p]["pos"][1],
                     bk["pos"][2] - pmx_bones[p]["pos"][2])
                ln = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
                bk["tail"] = ((d[0] / ln, d[1] / ln, d[2] / ln) if ln > 1e-9
                              else (0.0, 1.0, 0.0))
            else:
                bk["tail"] = (0.0, 1.0, 0.0)
        bk["flag"] = flag
    _l("骨骼：%d 根%s%s"
       % (len(pmx_bones),
          "" if not jp_bones else "（其中 %d 根用了 MMD 标准日文名，"
                                  "英文原名写在英文名里）" % n_renamed,
          "；%d 根 unused 占位骨单独放进 unused 表示枠" % n_unused
          if n_unused else ""))

    # ---- 足 IK（MMD 摆姿势几乎必用；源骨架没有，这里补标准的一套）
    def find_bone(jp_name):
        for i, b in enumerate(pmx_bones):
            if b["name"] == jp_name:
                return i
        return -1

    ik_made = 0
    if make_ik:
        ground_y = min(p[1] for p in pts) if pts else 0.0
        center_i = find_bone("センター")
        for side, s in (("左", "L"), ("右", "R")):
            i_ankle = find_bone(side + "足首")
            i_knee = find_bone(side + "ひざ")
            i_leg = find_bone(side + "足")
            i_toe = find_bone(side + "つま先")
            if min(i_ankle, i_knee, i_leg) < 0:
                continue
            ankle = pmx_bones[i_ankle]["pos"]
            i_legik = len(pmx_bones)
            pmx_bones.append({
                "name": _uniq(side + "足ＩＫ", used),
                "name_en": "LegIK_" + s,
                "pos": (ankle[0], ground_y, ankle[2]),
                "parent": center_i if center_i >= 0 else 0,
                "layer": 0,
                "flag": 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020,
                "tail_kind": 0, "tail": (0.0, 1.0, 0.0),
                "inherit_rot": None, "inherit_mov": None,
                "fixed_axis": None, "local_axis": None, "external": None,
                "_is_unused": False,
                "ik": {"target": i_ankle, "iterations": 40,
                       "limit_angle": 1.0,
                       "links": [{"bone": i_knee, "min": None, "max": None},
                                 {"bone": i_leg, "min": None, "max": None}]},
            })
            ik_made += 1
            if i_toe >= 0:
                toe = pmx_bones[i_toe]["pos"]
                pmx_bones.append({
                    "name": _uniq(side + "つま先ＩＫ", used),
                    "name_en": "ToeIK_" + s,
                    "pos": (toe[0], ground_y, toe[2]),
                    "parent": i_legik,
                    "layer": 0,
                    "flag": 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020,
                    "tail_kind": 0, "tail": (0.0, 1.0, 0.0),
                    "inherit_rot": None, "inherit_mov": None,
                    "fixed_axis": None, "local_axis": None,
                    "external": None,
                    "_is_unused": False,
                    "ik": {"target": i_toe, "iterations": 3,
                           "limit_angle": 1.0,
                           "links": [{"bone": i_ankle,
                                      "min": None, "max": None}]},
                })
                ik_made += 1
        if ik_made:
            _l("足 IK：补了 %d 根（足ＩＫ / つま先ＩＫ，父级 センター）" % ik_made,
               "info")
        else:
            _l("提示：源骨架里找不到完整的腿部骨链，没补 IK 骨", "info")

    # ---- 面：按网格（= 材质）排序，保证 PMX 材质吃连续的面段
    nmats = len(m.meshes)
    tri = []
    for mi, mesh in enumerate(m.meshes):
        base = mesh_vert_start[mi]
        for (a, b, c) in mesh.triangles:
            tri.append((mi, base + a, base + b, base + c))
    tri.sort(key=lambda t: t[0])

    # ---- 绕序自动判定（用变换后的数据投票）
    pos_t = [sp(p) for p in raw_pos]
    nrm_t = []
    n_has = 0
    for i, n in enumerate(raw_nrm):
        if n is None:
            nrm_t.append(None)
        else:
            nrm_t.append(sn(n))
            n_has += 1
    face_idx = []
    for _mi, a, b, c in tri:
        face_idx.extend((a, b, c))
    if n_has >= max(1, len(raw_nrm) // 2):
        # 少数顶点缺法线时用「有效法线的平均值」补上，别让投票函数拿到 None
        if n_has < len(raw_nrm):
            ax = ay = az = 0.0
            for n in nrm_t:
                if n is not None:
                    ax += n[0]
                    ay += n[1]
                    az += n[2]
            ln = (ax * ax + ay * ay + az * az) ** 0.5 or 1.0
            fill = (ax / ln, ay / ln, az / ln)
            nrm_vote = [n if n is not None else fill for n in nrm_t]
            _l("提示：%d 个顶点没有法线，绕序投票时按平均法线处理"
               % (len(raw_nrm) - n_has), "info")
        else:
            nrm_vote = nrm_t
        need_flip, ag, dis = pmxio.detect_winding(pos_t, nrm_vote, face_idx)
        _l("绕序自动判定：与法线同向 %d 面 / 反向 %d 面 → %s"
           % (ag, dis, "需要反转" if need_flip else "保持不变"))
    else:
        # Z 取反是一次反射，几何绕序必然翻面；没有法线时一律反转
        need_flip = True
        _l("绕序：法线数据不全，按换轴反射一律反转（保持正面朝外）", "info")

    pmx_faces = []
    for _mi, a, b, c in tri:
        pmx_faces.extend((c, b, a) if need_flip else (a, b, c))

    # ---- 权重：XPS 的权重是按顶点索引的，直接摊到 PMX 顶点上
    n_dropped = 0
    no_weight = 0
    pmx_verts = []
    for vi in range(n_vert):
        P = sp(raw_pos[vi])
        N = nrm_t[vi] if nrm_t[vi] is not None else (0.0, 0.0, 1.0)
        wl = [(b, w) for b, w in raw_w[vi] if w > 1e-6]
        if len(wl) > 4:
            n_dropped += len(wl) - 4
            wl.sort(key=lambda t: -t[1])
            wl = wl[:4]
        if not wl:
            no_weight += 1
            wl = [(0, 1.0)]
        tot = sum(w for _, w in wl) or 1.0
        wl = [(slot.get(b, 0), w / tot) for b, w in wl]
        if len(wl) == 1:
            wtype, wbones, wweights = 0, [wl[0][0]], []
        elif len(wl) == 2:
            wtype, wbones, wweights = 1, [wl[0][0], wl[1][0]], [wl[0][1]]
        else:
            wtype = 2
            wbones = [wl[k][0] if k < len(wl) else wl[0][0] for k in range(4)]
            wweights = [wl[k][1] if k < len(wl) else 0.0 for k in range(4)]
        u, v = raw_uv[vi]
        pmx_verts.append({"pos": P, "normal": N, "uv": (u, v),
                          "add_uv": [], "wtype": wtype, "wbones": wbones,
                          "wweights": wweights, "sdef": None, "edge": 1.0})
    if no_weight:
        _l("提示：%d 个顶点没有权重，已挂到第 0 根骨骼" % no_weight, "warn")
    if n_dropped:
        _l("提示：%d 条第 5 及以后的骨骼权重被丢弃（PMX 最多 4 根）"
           % n_dropped, "info")

    # ---- 材质 + 贴图
    folder = os.path.dirname(os.path.abspath(path))
    out_dir = os.path.dirname(os.path.abspath(out_path))
    tex_dir = os.path.join(out_dir, "textures")
    pmx_textures = []
    tex_slot = {}
    n_linked = 0
    n_missing = []

    def use_texture(filename):
        key = os.path.basename((filename or "").replace("\\", "/")).lower()
        if key in tex_slot:
            return tex_slot[key]
        real = _find_texture(folder, filename) if filename else None
        if real is None:
            tex_slot[key] = -1
            return -1
        ext = os.path.splitext(real)[1].lower()
        if ext not in _TEX_EXT:
            tex_slot[key] = -1
            return -1
        dst_name = os.path.basename(real)
        dst = os.path.join(tex_dir, dst_name)
        try:
            os.makedirs(tex_dir, exist_ok=True)
            if not os.path.isfile(dst):
                _copy_tex(real, dst)
        except OSError:
            tex_slot[key] = -1
            return -1
        rel = "textures/" + dst_name
        if rel not in pmx_textures:
            pmx_textures.append(rel)
        tex_slot[key] = pmx_textures.index(rel)
        return tex_slot[key]

    counts = [0] * max(1, nmats)
    for mi, _a, _b, _c in tri:
        counts[mi] += 1

    pmx_materials = []
    for mi, mesh in enumerate(m.meshes):
        mname = _clean_name(mesh.name) or ("mat%d" % mi)
        diff = mesh.diffuse
        ti = use_texture(diff) if textures else -1
        if ti >= 0:
            n_linked += 1
        elif textures and diff:
            n_missing.append(os.path.basename(diff.replace("\\", "/")))
        flag = 0x0E | (0x10 if enable_edge else 0)
        if force_double_sided or all_double_sided:
            flag |= 0x01
        pmx_materials.append({
            "name": mname, "name_en": mname,
            "diffuse": (1.0, 1.0, 1.0, 1.0),
            "specular": (0.0, 0.0, 0.0),
            "shininess": 0.0,
            "ambient": (0.5, 0.5, 0.5),
            "flag": flag,
            "edge_color": (0.0, 0.0, 0.0, 1.0),
            "edge_size": 1.0 if enable_edge else 0.0,
            "tex": ti, "sph": -1, "sph_mode": 0,
            "toon_flag": _TOON_NONE_FLAG, "toon": _TOON_NONE_INDEX, "memo": "",
            "faces": counts[mi],
        })
    # 空材质（源里声明了但一个面都没用上）会让 PMX 出现 0 面材质段，去掉更干净
    pmx_materials = [x for x in pmx_materials if x["faces"] > 0]
    if not pmx_materials:
        pmx_materials.append({
            "name": "material_0", "name_en": "material_0",
            "diffuse": (1.0, 1.0, 1.0, 1.0), "specular": (0.0, 0.0, 0.0),
            "shininess": 0.0, "ambient": (0.5, 0.5, 0.5),
            "flag": 0x0E | (0x10 if enable_edge else 0),
            "edge_color": (0.0, 0.0, 0.0, 1.0),
            "edge_size": 1.0 if enable_edge else 0.0, "tex": -1, "sph": -1,
            "sph_mode": 0, "toon_flag": _TOON_NONE_FLAG,
            "toon": _TOON_NONE_INDEX, "memo": "",
            "faces": len(pmx_faces) // 3,
        })
    # PMX 材质的「面数」字段其实是**索引数**（每个三角面占 3 个）
    for x in pmx_materials:
        x["faces"] = int(x["faces"]) * 3
    _l("材质：%d 个 · 关联到贴图 %d 个%s"
       % (len(pmx_materials), n_linked,
          "" if n_linked else "（源目录里没找到同名贴图，导出的是白模）"))
    if n_missing:
        _l("提示：这些贴图在源目录里没找到 → %s"
           % " / ".join(n_missing[:8]), "warn")

    # ---- 表示枠：主干骨进 Root，unused 占位骨单独一枠，表情一枠
    main_items = []
    unused_items = []
    for i, bk in enumerate(pmx_bones):
        (unused_items if (hide_unused and bk.get("_is_unused"))
         else main_items).append(i)
    frames = []
    if main_items:
        frames.append({"name": "Root", "name_en": "Root", "special": 0,
                       "items": [(0, i) for i in main_items]})
    if unused_items:
        frames.append({"name": "unused", "name_en": "unused", "special": 0,
                       "items": [(0, i) for i in unused_items]})

    stem = os.path.splitext(os.path.basename(path))[0]
    title = name or stem
    comment = ["由 %s 转换：%s" % (kind, os.path.basename(path)),
               "骨骼 %d 根 · 材质 %d 个 · 顶点 %d · 三角面 %d"
               % (len(pmx_bones), len(pmx_materials), len(pmx_verts),
                  len(pmx_faces) // 3),
               "converted by xps2pmx.py"]
    model = pmxio.new_model(title)
    model["name"] = title
    model["name_en"] = stem
    model["comment"] = "\r\n".join(comment)
    model["comment_en"] = "\r\n".join(comment)
    model["vertices"] = pmx_verts
    model["faces"] = pmx_faces
    model["textures"] = pmx_textures
    model["materials"] = pmx_materials
    model["bones"] = pmx_bones
    model["morphs"] = []
    model["frames"] = frames
    model["add_uv"] = 0
    if not enable_edge:
        pmxio.strip_edges(model, force_double_sided=False)

    size = pmxio.write_pmx(model, out_path)
    _l("已写出 %s（%.2f MB）· 顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d"
       % (os.path.basename(out_path), size / 1048576.0, len(pmx_verts),
          len(pmx_faces) // 3, len(pmx_bones), len(pmx_materials)), "ok")

    st = {"bytes": size, "vertices": len(pmx_verts),
          "tris": len(pmx_faces) // 3, "bones": len(pmx_bones),
          "materials": len(pmx_materials), "morphs": 0,
          "textures": len(pmx_textures), "scale": scale}

    if fbx_path:
        try:
            import fbxout
            st["fbx"] = fbxout.export_model(
                fbx_path, pmx_verts, pmx_faces, pmx_materials,
                pmx_bones, [],
                mirror_z=True, reverse_winding=True, tex_dir=tex_dir,
                log=_l, name=title)
            _l("已写出 %s（ASCII FBX 7.4 · %.2f MB）"
               % (os.path.basename(fbx_path),
                  st["fbx"]["bytes"] / 1048576.0), "ok")
        except Exception as e:
            st["fbx_error"] = str(e)
            _l("FBX 导出失败：%s" % e, "err")
    return st


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="XPS / XNALara → MMD PMX（纯 Python）")
    ap.add_argument("src")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--scale", default="mmd",
                    help="mmd（归一到 20 单位，默认）/ raw（保持源尺寸）/ 具体倍率")
    ap.add_argument("--edge", action="store_true", help="给材质开启 MMD 轮廓线")
    ap.add_argument("--no-textures", action="store_true")
    ap.add_argument("--raw-bone-names", action="store_true",
                    help="骨骼保留原始英文名，不转 MMD 日文标准名")
    ap.add_argument("--no-ik", action="store_true", help="不补 MMD 足 IK 骨")
    ap.add_argument("--show-unused", action="store_true",
                    help="unused 占位骨也放进 Root 表示枠")
    ap.add_argument("--one-sided", action="store_true",
                    help="材质不默认开两面描画")
    ap.add_argument("--fbx", action="store_true", help="同时导出 FBX")
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    out = a.out or (os.path.splitext(a.src)[0] + ".pmx")
    fbx = (os.path.splitext(out)[0] + ".fbx") if a.fbx else None
    st = convert(a.src, out, scale_mode=a.scale,
                 log=lambda m_, t=None: print(m_), name=a.name,
                 enable_edge=a.edge, textures=not a.no_textures,
                 jp_bones=not a.raw_bone_names, make_ik=not a.no_ik,
                 hide_unused=not a.show_unused,
                 all_double_sided=not a.one_sided, fbx_path=fbx)
    print("完成：%s（%.2f MB）" % (out, st["bytes"] / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
