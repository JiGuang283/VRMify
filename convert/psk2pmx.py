# -*- coding: utf-8 -*-
"""PSK / PSKX（Unreal ActorX）→ MMD PMX，纯 Python 标准库。

和项目里其它转换器保持同一套口径：
  · 换轴   (x, y, z)_psk → (x, z, y)_pmx（Y/Z 对调，详见 convert() 里的长注释）
  · 缩放   按包围盒高度归一到 MMD 常规的 20 单位
  · 居中   x/z 取包围盒中心、y 脚底归零
  · 骨骼   Bip001 这套 3dsMax Biped 命名默认转成 MMD 标准日文骨名，
            英文原名写进骨骼的英文名（name_en）字段，两边都不丢
  · 表情   PSK 的 MRPHINFO/MRPHDATA（顶点位移增量）→ PMX 顶点表情
  · 材质   按材质名在源目录找同名贴图；toon 一律不使用

骨骼世界矩阵的坑见 formats/pskio.py 头部注释（四元数要取共轭）。
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

import pskio
import pmxio


# --------------------------------------------------------- toon（不使用） ------
# PMX 材质的 toon 字段由「共有Toonフラグ」决定宽度，两个方向都别搞反
# （PMX 规范 + mmd_tools 的 `is_shared_toon_texture` 一致）：
#
#   flag = 1 → 共享/内建 toon。字段是 **1 字节编号**，引用 MMD 安装目录
#              `Data/` 下的 toon01.bmp … toon10.bmp。
#              ⚠ **编号 0 就是 toon01.bmp，不是「不使用」**。mmd_tools 源码里
#                写死了这个 +1：
#                    toon_path = "toon%02d.bmp" % (shared_toon_texture + 1)
#                MMD 的 Data 目录里**根本没有 toon00.bmp**（只有 toon01..toon10），
#                所以「编号 0 = toon00.bmp = 不使用 toon」这个说法是错的。
#
#   flag = 0 → 本模型**纹理表**里的贴图。字段是**纹理索引宽度**（ti），
#              **-1 = なし（不使用）**。
#
# 所以「不使用 toon」唯一正确的写法是 `toon_flag = 0, toon = -1`。
#
# 2026-09-24 修正：以前四个转换器都写 `flag=1, toon=0`（注释误以为 0 是
# toon00.bmp = 不使用），实际等于给每个材质硬套了一层 **toon01.bmp**，用户反馈
# 「PMX 文件还是自动上了 Toon 路径」。
# 对照实测：用户本机那份公认正常的 `Anastasya.pmx`，全部 13 个材质都是
# `toon_flag=0 / toon=-1`；而本工具产出的 `R2T1FeiBiMd10011.pmx` 是 `flag=1/0`。
_TOON_NONE_FLAG = 0
_TOON_NONE_INDEX = -1


def _log_default(msg, tag=None):
    print(msg)


# ------------------------------------------------------------- 骨名映射 ------
# Bip001（3dsMax Biped）→ MMD 标准骨名。映射不到的保留原名。
_BONE_JP = {
    "Root": "全ての親",
    "Bip001": "グルーブ",
    "Bip001Pelvis": "センター",
    "Bip001Spine": "上半身",
    "Bip001Spine1": "上半身1",
    "Bip001Spine2": "上半身2",
    "Bip001Neck": "首",
    "Bip001Head": "頭",
    "Bip001LClavicle": "左肩",
    "Bip001RClavicle": "右肩",
    "Bip001LUpperArm": "左腕",
    "Bip001RUpperArm": "右腕",
    "Bip001LForearm": "左ひじ",
    "Bip001RForearm": "右ひじ",
    "Bip001LHand": "左手首",
    "Bip001RHand": "右手首",
    "Bip001LThigh": "左足",
    "Bip001RThigh": "右足",
    "Bip001LCalf": "左ひざ",
    "Bip001RCalf": "右ひざ",
    "Bip001LFoot": "左足首",
    "Bip001RFoot": "右足首",
    "Bip001LToe0": "左つま先",
    "Bip001RToe0": "右つま先",
}

# 手指：Bip001LFinger0 / LFinger01 / LFinger1 / LFinger11 ...
# 拇指从 ０ 起，其余手指从 １ 起（MMD 标准骨名的惯例）。
_FINGER_JP = ("親指", "人指", "中指", "薬指", "小指")
_FW = ("０", "１", "２", "３", "４", "５")
_SIDE_JP = {"L": "左", "R": "右"}
_FINGER_RE = re.compile(r"^Bip001([LR])Finger(\d)(\d?)$")


def bone_jp(name):
    """单根骨骼名 → MMD 日文名；没有对应条目就返回 None（保留原名）。"""
    if name in _BONE_JP:
        return _BONE_JP[name]
    m = _FINGER_RE.match(name or "")
    if m:
        side, finger, seg = m.group(1), int(m.group(2)), m.group(3)
        if 0 <= finger < len(_FINGER_JP):
            base = 0 if finger == 0 else 1          # 拇指从 ０、其余从 １
            idx = base + (int(seg) if seg else 0)
            if idx < len(_FW):
                return "%s%s%s" % (_SIDE_JP[side], _FINGER_JP[finger], _FW[idx])
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


def _morph_panel(name):
    """PMX 表情面板：1=眉 2=目 3=口 4=その他（只影响 MMD 里的分组）。"""
    n = (name or "").lower()
    if n.startswith("b_") or "brow" in n or n.startswith("mayu"):
        return 1
    if n.startswith("e_") or "pupil" in n or "eye" in n or n.startswith("hitomi"):
        return 2
    if (n.startswith("l_") or n.startswith("r_") or n.startswith("aa")
            or n.startswith("mouth") or "lip" in n or "tooth" in n
            or "tongue" in n or n.startswith("kuch")):
        return 3
    return 4


_TEX_EXT = (".png", ".bmp", ".tga", ".jpg", ".jpeg", ".dds")


def _copy_tex(src, dst, remove_alpha):
    """拷贴图；remove_alpha 时复用 uemodel2pmx 那套去 alpha 逻辑。"""
    if remove_alpha:
        try:
            import uemodel2pmx
            r = uemodel2pmx._copy_texture(src, dst, True)
            if r:
                return r
        except Exception:
            pass
    shutil.copyfile(src, dst)
    return "copied"


def _find_texture(folder, mat_name):
    """按材质名在同目录（含 Textures 子目录）找贴图文件，返回绝对路径或 None。"""
    cands = []
    nm = mat_name or ""
    cands.append(nm)
    if nm.startswith("MI_"):
        cands.append(nm[3:])
    if nm.startswith("M_"):
        cands.append(nm[2:])
    for base in list(cands):
        for suf in ("_D", "_Diffuse", "_BaseColor", "_Albedo", "_d", "_C"):
            cands.append(base + suf)
    dirs = [folder]
    for sub in ("Textures", "textures", "Texture"):
        d = os.path.join(folder, sub)
        if os.path.isdir(d):
            dirs.append(d)
    parent = os.path.dirname(folder)
    for sub in ("Textures", "textures"):
        d = os.path.join(parent, sub)
        if os.path.isdir(d):
            dirs.append(d)

    lower = {}
    for d in dirs:
        try:
            for f in os.listdir(d):
                st, ext = os.path.splitext(f)
                if ext.lower() in _TEX_EXT:
                    lower.setdefault((d, st.lower()), f)
        except OSError:
            pass
    for base in cands:
        if not base:
            continue
        for (d, st), f in lower.items():
            if st == base.lower():
                return os.path.join(d, f)
    return None


# ------------------------------------------------------------------- 主体 ----
def _warn_anchor_collision(out_path, log):
    """提示 `.psk` 同名冲突（PmxEditor 的「アンカーデータ」误读）。

    PmxEditor 打开 `xxx.pmx` 时会自动去找**同名**的 `xxx.psk`，当成它的
    「アンカー（锚点/权重编辑）数据」加载 —— 见菜单
    `[ファイル] → [アンカーデータの自動読み込み／保存]` 与它的 readme：
        「モデルファイル名と同名のアンカーデータファイル(*.psk)がある場合自動読み込み」
    而 `*.psk` 恰好又是 Unreal ActorX 网格的扩展名，也就是本工具的**输入**格式。
    于是「源 psk 和输出 pmx 同名同目录」这种最自然的用法，一定会让 PmxEditor 弹：

        アンカーデータの読み込みに失敗しました。

    这个报错**不影响模型**（点确定后模型照常显示），但很吓人，所以主动说一声。
    """
    d = os.path.dirname(os.path.abspath(out_path)) or "."
    stem = os.path.splitext(os.path.basename(out_path))[0]
    clash = [f for f in (stem + ".psk", stem + ".pskx")
             if os.path.isfile(os.path.join(d, f))]
    if not clash:
        return
    log("提示：同目录有同名的 %s。PmxEditor 打开这个 PMX 时会把它当成"
        "「アンカーデータ」（锚点/权重数据）去加载，然后弹"
        "「アンカーデータの読み込みに失敗しました。」—— 点确定即可，不影响模型。"
        "想彻底不弹：把 PMX 挪到别的文件夹 / 改个名 / 在 PmxEditor 里关掉"
        "[ファイル]→[アンカーデータの自動読み込み／保存]。"
        % " / ".join(clash), "warn")


def convert(path, out_path, scale_mode="mmd", log=None, name=None,
            enable_edge=False, force_double_sided=False, textures=True,
            keep_add_uv=True, remove_alpha=True, flip_winding="auto",
            center=True, jp_bones=True, make_ik=True, export_morphs=True,
            fbx_path=None):
    """PSK/PSKX → PMX。返回统计 dict。"""
    _l = log or _log_default

    m = pskio.read_psk(path)
    kind = "PSKX" if m.face32 else "PSK"
    _l("读取 %s：顶点 %d · 楔 %d · 三角面 %d · 材质 %d · 骨骼 %d · 权重 %d"
       % (kind, len(m.points), len(m.wedges), len(m.faces),
          len(m.materials), len(m.bones), len(m.weights)))
    if not m.points or not m.wedges:
        raise ValueError("这个文件里没有网格数据（PNTS / VTXW 为空）")
    if not m.faces:
        raise ValueError("这个文件里没有面数据（FACE 块为空）")
    _l("表情 %d 个 · 附加 UV %d 套 · 法线 %s"
       % (len(m.morphs), len(m.extra_uvs),
          "有" if m.normals else "无（将自动计算）"))

    # ---- 坐标变换
    #
    # 实测（R2T1FeiBiMd10011.psk / R2T1AimisiMd10011.pskx 两个文件一致）：
    #     Bip001LHand 在 +X、Bip001RHand 在 -X  →  PSK 的 +X = 模型**左手**
    #     脚踝 → 脚尖的方向 ≈ (0, -0.47, -0.88) →  PSK 的 -Y = 模型**正面**
    #     （另有一条独立佐证：整体包围盒在 +Y 侧明显更长，那是头发/披风/背包）
    #     整体 +Z = 上
    # 而 MMD/PMX 的约定是：**正面 -Z、左手 +X、上 +Y**
    # （见 pmx2vrm.py 里对实测模型「小食Taberu」的记录：両目 Z=-0.75、
    #   左腕 X=+1.34；本机 F:\测试\...\Anastasya.pmx 复核同样成立）。
    #
    # 所以只要把 PSK 的 Y 和 Z 对调就同时满足三条：
    #     左手 +X → +X ✓   上 +Z → +Y ✓   正面 -Y → -Z ✓
    #
    # 注意这里**不是** uemodel2pmx 那种 (x, z, -y)：
    #   (x, z, -y) 行列式 +1（纯旋转，不改手性），会把正面 -Y 送到 **+Z**，
    #   于是人物在 MMD 里背对镜头 —— 这正是 2026-09-24 修掉的那个 bug。
    #   (x, z, y) 行列式 -1，正好把 PSK 的右手系翻成 MMD 的左手系，手性才对上。
    # 因为绕序会跟着反射一起翻，flip_winding="auto" 的投票（以及没有法线时的
    # 兜底）都要相应反转，见下面「绕序」那一段。
    def xf(p):
        return (p[0], p[2], p[1])

    # ---- 顶点：一个 wedge 对应一个 PMX 顶点（UV/材质分流都在 wedge 上）
    n_wedge = len(m.wedges)
    n_point = len(m.points)
    raw_pos = []
    bad_wedge = 0
    for pi, u, v, _mi in m.wedges:
        if not (0 <= pi < n_point):
            pi = 0
            bad_wedge += 1
        raw_pos.append(m.points[pi])
    if bad_wedge:
        _l("提示：%d 个楔的点索引越界，已回退到第 0 个点" % bad_wedge, "warn")

    pts = [xf(p) for p in raw_pos]
    ys = [p[1] for p in pts]
    lo_y, hi_y = (min(ys), max(ys)) if ys else (0.0, 1.0)
    if scale_mode in ("mmd", "auto", None):
        scale = 20.0 / max(hi_y, 1e-6)
        if hi_y <= 0:
            scale = 20.0 / max(abs(lo_y), 1e-6)
        _l("缩放：源高 %.2f 单位 × %.6f → %.2f（MMD 常规 20）"
           % (hi_y, scale, hi_y * scale))
    elif scale_mode in ("raw", "none", 1, 1.0):
        # 与 fbx2pmx 的 --scale raw 对齐：保持源尺寸不动。
        # （以前这里只认 "mmd" 和数字，写 --scale raw 会直接
        #   ValueError: could not convert string to float: 'raw'。）
        scale = 1.0
        _l("缩放：保持源尺寸（scale=1.0），源高 %.2f 单位" % hi_y)
    else:
        scale = float(scale_mode)
        _l("缩放：源高 %.2f 单位 × %.6f → %.2f"
           % (hi_y, scale, hi_y * scale))

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

    def sdir(d):
        """源位移 → PMX 位移（只换轴 + 缩放，不带平移）。"""
        t = xf(d)
        return (t[0] * scale, t[1] * scale, t[2] * scale)

    # ---- 骨骼世界坐标
    _wrot, wpos = pskio.bone_world(m)
    bone_pos = [sp(p) for p in wpos]
    n_src = len(m.bones)

    # ---- 骨骼顺序：DFS，保证父在子前面
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
    for src in order:
        b = m.bones[src]
        jp = bone_jp(b.name) if jp_bones else None
        main_name = jp if jp else b.name
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
            "_kids": [],
        })
    for src in order:
        b = m.bones[src]
        p = b.parent_index if 0 <= b.parent_index < n_src else -1
        if p in slot and slot[p] != slot[src]:
            pmx_bones[slot[p]]["_kids"].append(slot[src])

    for bk in pmx_bones:
        flag = 0x0002 | 0x0004 | 0x0008 | 0x0010      # 可旋转/可移动/可显示/可操作
        k = bk.pop("_kids")
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
    _l("骨骼：%d 根%s"
       % (len(pmx_bones),
          "" if not jp_bones else "（其中 %d 根用了 MMD 标准日文名，"
                                  "英文原名写在英文名里）" % n_renamed))

    # ---- 足 IK（MMD 摆姿势几乎必用；源骨架没有，这里补标准的一套）
    def find_bone(jp_name):
        for i, b in enumerate(pmx_bones):
            if b["name"] == jp_name:
                return i
        return -1

    ik_made = 0
    if make_ik:
        ground_y = min(p[1] for p in (sp(x) for x in raw_pos)) if raw_pos else 0.0
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

    # ---- 面：按材质排序，保证 PMX 材质吃连续的面段
    nmats = max(1, len(m.materials))
    tri = []
    for (i0, i1, i2), mi in m.faces:
        if i0 >= n_wedge or i1 >= n_wedge or i2 >= n_wedge:
            continue
        if not (0 <= mi < nmats):
            mi = 0
        tri.append((mi, i0, i1, i2))
    tri.sort(key=lambda t: t[0])

    # ---- 绕序自动判定（用变换后的数据投票；缩放/平移不影响手性）
    # xf 是一次反射（行列式 -1），几何绕序会跟着翻，所以下面「没有法线」的兜底
    # 也从「保持不变」改成「反转」——不然没带 VTXNORMS 的 psk 会整片背面朝外。
    pos_t = [sp(p) for p in raw_pos]
    if m.normals and len(m.normals) == n_point:
        nrm_t = []
        for pi, _u, _v, _mi in m.wedges:
            n = m.normals[pi] if 0 <= pi < len(m.normals) else (0.0, 0.0, 1.0)
            t = xf(n)
            ln = (t[0] ** 2 + t[1] ** 2 + t[2] ** 2) ** 0.5 or 1.0
            nrm_t.append((t[0] / ln, t[1] / ln, t[2] / ln))
    else:
        nrm_t = []
    face_idx = []
    for _mi, a, b, c in tri:
        face_idx.extend((a, b, c))
    if flip_winding in ("auto", None, ""):
        if nrm_t:
            need_flip, ag, dis = pmxio.detect_winding(pos_t, nrm_t, face_idx)
            _l("绕序自动判定：与法线同向 %d 面 / 反向 %d 面 → %s"
               % (ag, dis, "需要反转" if need_flip else "保持不变"))
        else:
            need_flip = True
            _l("绕序：没有法线数据，按换轴反射一律反转（保持正面朝外）", "info")
    else:
        need_flip = bool(flip_winding)

    pmx_faces = []
    for _mi, a, b, c in tri:
        pmx_faces.extend((c, b, a) if need_flip else (a, b, c))

    # ---- 权重：RAWWEIGHTS 是按「点」索引的，楔要按 point_index 去查
    per_point = {}
    for w, pi, bi in m.weights:
        if 0 <= pi < n_point:
            per_point.setdefault(pi, []).append((w, bi))

    n_dropped = 0
    no_weight = 0
    pmx_verts = []
    n_add = min(4, len(m.extra_uvs)) if keep_add_uv else 0
    for wi, (pi, u, v, _mi) in enumerate(m.wedges):
        P = sp(m.points[pi] if 0 <= pi < n_point else (0.0, 0.0, 0.0))
        if nrm_t:
            N = nrm_t[wi]
        else:
            N = (0.0, 0.0, 1.0)
        wl = [(b, w) for w, b in per_point.get(pi, []) if w > 1e-6]
        if len(wl) > 4:
            n_dropped += len(wl) - 4
            wl.sort(key=lambda t: -t[1])
            wl = wl[:4]
        wl = [(b, w) for b, w in wl if 0 <= b < n_src]
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
        add = []
        for k in range(n_add):
            st = m.extra_uvs[k]
            uv = st[wi] if wi < len(st) else (0.0, 0.0)
            add.append((uv[0], uv[1], 0.0, 0.0))
        pmx_verts.append({"pos": P, "normal": N, "uv": (u, v),
                          "add_uv": add, "wtype": wtype, "wbones": wbones,
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

    def use_texture(mat_name):
        if mat_name in tex_slot:
            return tex_slot[mat_name]
        real = _find_texture(folder, mat_name)
        if real is None:
            real = _find_texture(out_dir, mat_name)
        if real is None:
            tex_slot[mat_name] = -1
            return -1
        ext = os.path.splitext(real)[1].lower()
        if ext not in _TEX_EXT:
            tex_slot[mat_name] = -1
            return -1
        dst = os.path.join(tex_dir, os.path.basename(real))
        try:
            os.makedirs(tex_dir, exist_ok=True)
            _copy_tex(real, dst, remove_alpha)
        except OSError:
            tex_slot[mat_name] = -1
            return -1
        pmx_textures.append("textures/" + os.path.basename(real))
        tex_slot[mat_name] = len(pmx_textures) - 1
        return tex_slot[mat_name]

    counts = [0] * nmats
    for _mi, _a, _b, _c in tri:
        counts[_mi] += 1

    pmx_materials = []
    for k in range(nmats):
        mt = m.materials[k] if k < len(m.materials) else None
        mname = (mt.name if mt and mt.name else "mat%d" % k)
        ti = use_texture(mname) if textures else -1
        if ti >= 0:
            n_linked += 1
        flag = 0x0E | (0x10 if enable_edge else 0)
        if force_double_sided:
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
            # toon：**不使用**。见文件顶部 _TOON_NONE_FLAG 的说明。
            "toon_flag": _TOON_NONE_FLAG, "toon": _TOON_NONE_INDEX, "memo": "",
            "faces": counts[k],
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

    # ---- 表情：MRPH 的位移是按「点」索引的，要摊到该点对应的所有楔上
    point_to_wedges = {}
    for wi, (pi, _u, _v, _mi) in enumerate(m.wedges):
        if 0 <= pi < n_point:
            point_to_wedges.setdefault(pi, []).append(wi)

    used_mo = set()
    pmx_morphs = []
    if export_morphs:
        for mo in m.morphs:
            offs = []
            for pi, d in mo.offsets:
                dp = sdir(d)
                if abs(dp[0]) + abs(dp[1]) + abs(dp[2]) < 1e-9:
                    continue
                for wi in point_to_wedges.get(pi, ()):
                    if wi < len(pmx_verts):
                        offs.append((wi, dp))
            if not offs:
                continue
            nm = _uniq(mo.name, used_mo)
            pmx_morphs.append({"name": nm, "name_en": mo.name or nm,
                               "panel": _morph_panel(mo.name), "kind": 1,
                               "offsets": offs})
        _l("表情：%d 个（顶点表情）" % len(pmx_morphs))

    # ---- 表示枠 / 模型信息
    frames = []
    if pmx_bones:
        frames.append({"name": "Root", "name_en": "Root", "special": 0,
                       "items": [(0, i) for i in range(len(pmx_bones))]})
    if pmx_morphs:
        frames.append({"name": "表情", "name_en": "Exp", "special": 1,
                       "items": [(1, i) for i in range(len(pmx_morphs))]})

    stem = os.path.splitext(os.path.basename(path))[0]
    title = name or stem
    comment = ["由 %s 转换：%s" % (kind, os.path.basename(path)),
               "骨骼 %d 根 · 材质 %d 个 · 表情 %d 个" % (len(pmx_bones),
                                                       len(pmx_materials),
                                                       len(pmx_morphs)),
               "converted by psk2pmx.py"]
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
    model["morphs"] = pmx_morphs
    model["frames"] = frames
    model["add_uv"] = n_add
    if not enable_edge:
        pmxio.strip_edges(model, force_double_sided=False)

    size = pmxio.write_pmx(model, out_path)
    _l("已写出 %s（%.2f MB）· 顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d · 表情 %d"
       % (os.path.basename(out_path), size / 1048576.0, len(pmx_verts),
          len(pmx_faces) // 3, len(pmx_bones), len(pmx_materials),
          len(pmx_morphs)), "ok")
    _warn_anchor_collision(out_path, _l)

    st = {"bytes": size, "vertices": len(pmx_verts),
          "tris": len(pmx_faces) // 3, "bones": len(pmx_bones),
          "materials": len(pmx_materials), "morphs": len(pmx_morphs),
          "textures": len(pmx_textures), "scale": scale}

    if fbx_path:
        try:
            import fbxout
            st["fbx"] = fbxout.export_model(
                fbx_path, pmx_verts, pmx_faces, pmx_materials,
                pmx_bones, pmx_morphs,
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
    ap = argparse.ArgumentParser(description="PSK / PSKX → MMD PMX（纯 Python）")
    ap.add_argument("src")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--scale", default="mmd",
                    help="mmd（归一到 20 单位，默认）/ raw（保持源尺寸）/ 具体倍率")
    ap.add_argument("--edge", action="store_true", help="给材质开启 MMD 轮廓线")
    ap.add_argument("--force-double-sided", action="store_true")
    ap.add_argument("--no-textures", action="store_true")
    ap.add_argument("--keep-alpha", action="store_true", help="保留贴图 alpha")
    ap.add_argument("--no-add-uv", action="store_true")
    ap.add_argument("--raw-bone-names", action="store_true",
                    help="骨骼保留原始英文名，不转 MMD 日文标准名")
    ap.add_argument("--no-ik", action="store_true", help="不补 MMD 足 IK 骨")
    ap.add_argument("--no-morphs", action="store_true")
    ap.add_argument("--fbx", action="store_true", help="同时导出 FBX")
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    out = a.out or (os.path.splitext(a.src)[0] + ".pmx")
    fbx = (os.path.splitext(out)[0] + ".fbx") if a.fbx else None
    st = convert(a.src, out, scale_mode=a.scale,
                 log=lambda m_, t=None: print(m_), name=a.name,
                 enable_edge=a.edge, force_double_sided=a.force_double_sided,
                 textures=not a.no_textures, keep_add_uv=not a.no_add_uv,
                 remove_alpha=not a.keep_alpha, jp_bones=not a.raw_bone_names,
                 make_ik=not a.no_ik, export_morphs=not a.no_morphs,
                 fbx_path=fbx)
    print("完成：%s（%.2f MB）" % (out, st["bytes"] / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
