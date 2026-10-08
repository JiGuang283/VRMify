# -*- coding: utf-8 -*-
"""uemodel2pmx.py - UEFormat(.uemodel) → MMD 的 PMX 2.0，纯 Python 标准库。

UEFormat 是 FortnitePorting / FModel / CUE4Parse 生态的公开交换格式
（规范 https://github.com/h4lfheart/UEFormat），.uemodel 就是它的模型容器。

坐标（实测样本 R2T1XinMd10011.uemodel，别凭"UE 惯例"想当然）
    数据里  +X = 模型左侧、+Y = 前方、+Z = 上方、单位 cm。
    证据：Bip001LHand.x = +33.445 / Bip001RHand.x = -33.445（左右轴 = X）；
          趾-踝 = (+0.56, +9.13, -10.70)（水平分量主要落在 +Y ⇒ 前方 = +Y）；
          包围盒 z 0.00..174.43 ⇒ 上方 = +Z、身高 174.43 cm。
    MMD/PMX 是  +X = 左侧、+Y = 上方、脸朝 -Z（前方 = -Z）。
    两者同为**左手系**，只差绕 Z 的 90°：
        (x, y, z)_ue → (x, z, -y)_pmx       行列式 = +1，不改手性
    所以**不做镜面反射**（不需要像 VRM 那样取反某个轴）。

绕序
    不做反射 ⇒ 手性不变，但 UE/D3D 的正面缠绕与 MMD 相反：实测几何法线 · 顶点法线
    反向 23339 面 / 同向 6661 面。所以**必须反转三角形绕序**，由
    pmxio.detect_winding 在变换后的数据上自动投票决定（与 pmx2vrm 同一套口径）。

其他
    * WEIGHTS 是**稀疏表**（骨索引 + 顶点索引 + 权重的三元组，一个顶点最多见过 8 条），
      转 PMX 时按权重取前 4 条归一化后写成 BDEF1/2/4。
    * 贴图不在 .uemodel 里。导出目录里通常同时有 MI_<材质名>.json（材质实例，
      里面 Textures.MainTex 指向基础色贴图）和导出好的 T_*.png，照它关联即可。
    * 表情（MORPHTARGETS）是顶点位移，直接转成 PMX 顶点表情。

用法：
    python uemodel2pmx.py model.uemodel -o model.pmx
    python uemodel2pmx.py model.uemodel --scale fixed --fbx
"""

import argparse
import json
import os
import shutil
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "formats")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pmxio          # noqa: E402
import uemodelio      # noqa: E402


def _log_default(msg, tag=None):
    try:
        print(msg)
    except UnicodeEncodeError:
        print(msg.encode("utf-8", "replace").decode("ascii", "replace"))


# --------------------------------------------------------------- 矩阵 / 四元 --
def _qmat(q):
    x, y, z, w = q
    n = (x * x + y * y + z * z + w * w) ** 0.5 or 1.0
    x, y, z, w = x / n, y / n, z / n, w / n
    return [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]


def _mm(a, b):
    return [[a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j]
             for j in range(3)] for i in range(3)]


def _mv(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


# --------------------------------------------------------------- 贴图关联 --
class _FolderIndex(object):
    """目录文件索引（大小写不敏感），避免每个材质都 listdir。"""

    def __init__(self, folder):
        self.map = {}
        try:
            for fn in os.listdir(folder):
                self.map.setdefault(fn.lower(), fn)
        except OSError:
            pass

    def get(self, name):
        return self.map.get((name or "").lower())


def _mat_json(folder, idx, mat):
    """找出材质对应的 MI_*.json：先按材质名，再按材质资源路径的最后一段。"""
    cands = []
    nm = (mat.name or "").strip()
    if nm:
        cands.append(nm + ".json")
    tail = (mat.path or "").split("/")[-1]
    if tail:
        tail = tail.split(".")[-1]
        if tail:
            cands.append(tail + ".json")
    for c in cands:
        real = idx.get(c)
        if real and os.path.isfile(os.path.join(folder, real)):
            return os.path.join(folder, real)
    return None


def _read_mat_json(path):
    """读材质实例 json，取基础色贴图名 / 颜色 / 不透明度。"""
    out = {"tex": "", "color": None, "blend": None, "translucent": False}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception:
        return out
    texs = d.get("Textures") or {}
    for key in ("MainTex", "BaseColor", "T_BaseColor", "Diffuse"):
        v = texs.get(key)
        if v:
            out["tex"] = str(v).split("/")[-1].split(".")[0]
            break
    pars = d.get("Parameters") or {}
    cols = pars.get("Colors") or {}
    tint = cols.get("BaseColorTint") or cols.get("BaseColor") or {}
    if tint:
        try:
            out["color"] = (float(tint.get("R", 1.0)), float(tint.get("G", 1.0)),
                            float(tint.get("B", 1.0)), float(tint.get("A", 1.0)))
        except (TypeError, ValueError):
            out["color"] = None
    out["blend"] = pars.get("BlendMode")
    out["translucent"] = bool(pars.get("IsTranslucent"))
    return out


def find_material_texture(folder, mat, idx=None):
    """在 uemodel 导出目录里找出某个材质的基础色贴图文件（绝对路径）。

    路径是 `MI_<材质名>.json` → `Textures.MainTex` → 同目录的 `T_*.png`。
    GUI 预览也走这里，保证「预览看到的贴图」和「转换后拷进 PMX 的贴图」一致。
    找不到返回 None（调用方自行回退到纯色）。
    """
    idx = idx if idx is not None else _FolderIndex(folder)
    jp = _mat_json(folder, idx, mat)
    if not jp:
        return None
    name = (_read_mat_json(jp) or {}).get("tex")
    if not name:
        return None
    for ext in (".png", ".PNG", ".tga", ".TGA", ".jpg", ".jpeg", ".bmp",
                ".dds", ".DDS"):
        r = idx.get(name + ext)
        if r:
            return os.path.join(folder, r)
    return None


# ----------------------------------------------------------------- 表情面板 --
def _morph_panel(name):
    """PMX 表情面板：1=眉 2=目 3=口 4=その他（只影响 MMD 里的分组，不影响变形）。"""
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


def _copy_texture(src, dst, remove_alpha):
    """拷贴图；remove_alpha 时写成不透明 RGB。

    **UE 的贴图 alpha 几乎都是数据遮罩而不是透明度**（实测本样本 Face_D 里
    alpha=255 的像素是 0 个、Cloth_D 只有 165 个）——原样拷进 MMD 会因为
    「MMD 把贴图 alpha 当透明」而整个模型几乎全透。所以 UE 路径默认去 alpha。
    复用 fbx2pmx 里那套（有 Pillow 用 Pillow，否则纯标准库解 PNG/TGA）。
    """
    if remove_alpha:
        try:
            import fbx2pmx
            r = fbx2pmx._strip_alpha(src, dst)
            if r:
                return r
        except Exception:
            pass
    shutil.copyfile(src, dst)
    return "copied"


# --------------------------------------------------------------------- 主体 --
def _bone_tokens(nm):
    """把骨名切成有意义的 token（去掉纯数字序号与命名空间前缀）。

    `clavicle_l_adjust` → ('clavicle','l')
    `spine_01_adjust`   → ('spine',)
    `Bip001-L-Clavicle` → ('clavicle','l')   ← 序号与 `Bip` 一律丢掉
    `neck_01_adjust`    → ('neck',)
    `head_adjust`       → ('head',)
    """
    s = _strip_ns(nm or "")
    low = s.lower()
    for suf in ("_adjust", "-adjust", ".adjust", "adjust", "_adj", "_ctrl"):
        if low.endswith(suf) and len(low) > len(suf):
            s = s[:len(s) - len(suf)]
            low = s.lower()
            break
    for pre in ("adjust_", "adj_", "ctrl_"):
        if low.startswith(pre) and len(low) > len(pre):
            s = s[len(pre):]
            low = s.lower()
            break
    toks = []
    for part in s.replace("-", "_").replace(".", "_").replace(" ", "_").split("_"):
        part = "".join(c for c in part.lower() if c.isalnum())
        if not part:
            continue
        # 去掉尾部的数字序号：`bip001` → `bip`、`spine1` → `spine`、`01` → 空
        core = part.rstrip("0123456789")
        if not core:
            continue
        # `bip` 只是 Biped 包的壳，不带语义；`bn` / `bone` 同理
        if core in ("bip", "bn", "bone", "b"):
            continue
        toks.append(core)
    # 左右标记**统一挪到末尾**：`clavicle_l` 与 `l_clavicle` 是同一根骨的不同写法
    # （`Bn_l_hairA_001` 是前者、`Bip001-L-Clavicle` 切出来是后者），
    # 不归一化就永远对不上。
    side = [t for t in toks if t in ("l", "r", "lf", "rt", "left", "right")]
    body = [t for t in toks if t not in ("l", "r", "lf", "rt", "left", "right")]
    if side:
        s0 = side[0]
        s0 = {"lf": "l", "left": "l", "rt": "r", "right": "r"}.get(s0, s0)
        body.append(s0)
    return tuple(body)


def _bone_world(m):
    """骨骼局部坐标（T·R·S，父左乘）→ 源世界坐标列表。与主体同一套公式。"""
    n = len(m.skeleton.bones)
    world = [None] * n
    state = [0] * n

    def build(i, depth=0):
        if i < 0 or i >= n or state[i] == 2 or depth > 512:
            return
        b = m.skeleton.bones[i]
        if state[i] == 1:                 # 环：当根处理
            world[i] = (_qmat(b.rot), tuple(b.pos))
            state[i] = 2
            return
        state[i] = 1
        p = b.parent
        if p < 0 or p >= n:
            world[i] = (_qmat(b.rot), tuple(b.pos))
        else:
            build(p, depth + 1)
            pr, pt = world[p]
            if pr is None:
                world[i] = (_qmat(b.rot), tuple(b.pos))
            else:
                world[i] = (_mm(pr, _qmat(b.rot)),
                            _add(_mv(pr, b.pos), pt))
        state[i] = 2

    for i in range(n):
        build(i)
    for i in range(n):
        if world[i] is None:
            world[i] = (_qmat(m.skeleton.bones[i].rot),
                        tuple(m.skeleton.bones[i].pos))
    return [world[i][1] for i in range(n)]


# 「部件文件」自带的、指明它挂在参考模型哪个位置的调节骨。命名是各家约定，
# 但 *_adjust 这个后缀在本样本（鸣潮 / 黑鸟 042）里非常稳定，且能精确命中
# 参考模型的 Bip001 骨（误差 < 1e-4 cm）。
_ALIGN_SUFFIXES = ("_adjust",)


def _norm_bone(nm):
    """骨名归一化，用于跨 LOD / 跨部件的模糊配对。

    去掉命名空间前缀与常见前后缀、只留字母数字，再小写。
    例：`clavicle_l_adjust` → `claviclel`；`adjust_clavicle_l` → `claviclel`；
        `Bip001-L-Clavicle`  → `bip001lclavicle`（**不同**，所以匹配要按
        「全名 → 去前后缀 → 归一化」三级依次放宽，见 find_align_offset）。
    """
    s = (nm or "").strip()
    low = s.lower()
    for suf in ("_adjust", "-adjust", ".adjust", "adjust", "_adj", "_ctrl",
                "_control", "_ref", "_target", "_offset", "_helper"):
        if low.endswith(suf) and len(low) > len(suf):
            s = s[:len(s) - len(suf)]
            low = s.lower()
            break
    for pre in ("adjust_", "adjust-", "adj_", "ctrl_", "control_", "ref_",
                "target_", "offset_", "helper_"):
        if low.startswith(pre) and len(low) > len(pre):
            s = s[len(pre):]
            low = s.lower()
            break
    return "".join(c for c in s.lower() if c.isalnum())


def _strip_ns(nm):
    """再去掉一层命名空间前缀（`xxx:yyy` / `xxx|yyy` / `xxx.yyy`）。"""
    out = []
    for sep in (":", "|", "/"):
        if sep in nm:
            out = nm.split(sep)
            break
    return nm if not out else out[-1]


def _bone_alias(nm):
    """一个骨名可能对应的所有归一化别名（含去命名空间版本）。"""
    al = set()
    if not nm:
        return al
    for base in (nm, _strip_ns(nm)):
        al.add(_norm_bone(base))
        # UE/Maya 里常见的 <N>_ 编号前缀：`1_head` → `head`
        b2 = base
        for sep in ("_", "-", "."):
            parts = b2.split(sep)
            if len(parts) > 1 and parts[0].isdigit():
                al.add(_norm_bone(sep.join(parts[1:])))
    al.discard("")
    return al


def _bone_spread(m):
    """骨骼世界坐标的包围盒对角线长度（源 cm）。用来判断「这是整身还是部件」。

    整身模型骨架铺开（脚 0 → 头 ~150cm），对角线上百 cm；
    局部空间部件（如头骨局部）骨架挤在原点附近，对角线很小。
    """
    ws = _bone_world(m)
    if not ws:
        return 0.0
    xs = [p[0] for p in ws]
    ys = [p[1] for p in ws]
    zs = [p[2] for p in ws]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    dz = max(zs) - min(zs)
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def _bbox_diag(m):
    """第 0 个 LOD 顶点包围盒对角线长度（源 cm），用于「部件是否已落在参考内」。"""
    if not m.lods:
        return 0.0
    vs = m.lods[0].vertices
    if not vs:
        return 0.0
    xs = [v[0] for v in vs]
    ys = [v[1] for v in vs]
    zs = [v[2] for v in vs]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    dz = max(zs) - min(zs)
    return (dx * dx + dy * dy + dz * dz) ** 0.5


def find_align_offset(part, ref, log=None):
    """求「部件 uemodel」挂到「参考 uemodel」上所需的刚性平移（源 cm）。

    UEFormat 一个角色常被拆成多个 .uemodel（身体 / 头发+皮肤 / 服装…）。
    但**部件文件不一定和整身共用同一世界原点**：实测
    `Blackbird042_nighty_hair_skin.uemodel` 的坐标是**头部骨的局部空间**
    （它的 `head_adjust` 就在原点，`clavicle_l_adjust` = (1.812, -1.916, -10.748)），
    而它自带的 `*_adjust` 骨加一个平移后能**精确**落在参考模型的
    `Bip001-L-Clavicle` = (1.812, 0.194, 137.905) 上，平移量
    = 参考模型 `Bip001-Head` 的世界坐标 = (0, 2.110, 148.653)。

    匹配顺序（逐级放宽，先精确后模糊）：
      ① 部件骨名**原样**在参考模型里找同名骨；
      ② 去掉 `_adjust` 之类后缀 / `adjust_` 之类前缀后再找；
      ③ 归一化（只留字母数字、小写）后找——这一级能吃下
         `Bip001-L-Clavicle` ↔ `bip001lclavicle` 这种前缀差异。
    命中骨对的位移差按 0.02cm 分箱投票，取票数最高的一档求精确平均；
    要求 ≥3 票且最大残差 ≤0.05cm，否则判定不可信、放弃对齐。

    返回 (offset, n_hits, total) 或 (None, 0, 0)。
    """
    _l = log or _log_default
    pn = [b.name or "" for b in part.skeleton.bones]
    rn = [b.name or "" for b in ref.skeleton.bones]
    pw = _bone_world(part)
    rw = _bone_world(ref)

    # 参考模型：多级索引（全名 / 去前后缀 / 归一化别名 / token 序列）→ 骨世界坐标
    idx_exact, idx_alias = {}, {}
    tok_map = {}
    for i, nm in enumerate(rn):
        if not nm:
            continue
        idx_exact.setdefault(nm.lower(), rw[i])
        for a in _bone_alias(nm):
            idx_alias.setdefault(a, rw[i])
        tok_map.setdefault(_bone_tokens(nm), []).append(rw[i])
    # 歧义时取**列表第一根**：骨骼按父先子后排列，第一根就是层级最靠上的那根
    # （`Bip001-Spine` 先于 `Bip001-Spine1/2`，正是部件 adjust 骨要挂的那个）。
    tok_first = {k: v[0] for k, v in tok_map.items()}

    # 逐骨求「部件骨 → 参考骨」的位移差，按 0.02cm 分箱投票
    BIN = 0.02
    votes = {}
    n_matched = 0
    for i, nm in enumerate(pn):
        if not nm:
            continue
        target = idx_exact.get(nm.lower())
        if target is None:
            # ② 只去掉本侧后缀/前缀（`clavicle_l_adjust` → `clavicle_l`），
            #    避免直接归一化把 `Bip001-L-Clavicle` 也一起搅进来
            cands = []
            low = nm.lower()
            for suf in ("_adjust", "-adjust", ".adjust", "_adj", "_ctrl"):
                if low.endswith(suf):
                    cands.append(nm[:len(nm) - len(suf)])
            for pre in ("adjust_", "adj_", "ctrl_"):
                if low.startswith(pre):
                    cands.append(nm[len(pre):])
            for c in cands:
                target = idx_exact.get(c.lower())
                if target is not None:
                    break
        if target is None:
            # ③ 归一化兜底
            for a in _bone_alias(nm):
                target = idx_alias.get(a)
                if target is not None:
                    break
        if target is None:
            # ④ token 序列（已丢掉数字序号与 bip/bn/bone 包壳），
            #    这一级吃下 `clavicle_l_adjust` ↔ `Bip001-L-Clavicle`、
            #    `spine_01_adjust` ↔ `Bip001-Spine` 这类前缀/编号差异。
            #    token 有歧义时（`Bip001-Spine/Spine1/Spine2` 都归成 ('spine',)）
            #    取层级最靠上的第一根，靠后面的「分箱投票 + 残差复核」兜底：
            #    选错了那一票就会落在别的箱里、票数不够而被整体否决。
            target = tok_first.get(_bone_tokens(nm))
        if target is None:
            continue
        n_matched += 1
        d = (target[0] - pw[i][0], target[1] - pw[i][1], target[2] - pw[i][2])
        k = (round(d[0] / BIN), round(d[1] / BIN), round(d[2] / BIN))
        votes.setdefault(k, []).append((pw[i], target, nm))

    if not votes:
        _l("对齐：部件 %d 根骨里没找到与参考模型同名/近名的骨，放弃"
           % len(pn), "warn")
        return None, 0, len(pn)
    best_key = max(votes.items(), key=lambda kv: len(kv[1]))[0]
    lst = votes[best_key]
    n = float(len(lst))
    avg = (sum(t[1][0] - t[0][0] for t in lst) / n,
           sum(t[1][1] - t[0][1] for t in lst) / n,
           sum(t[1][2] - t[0][2] for t in lst) / n)
    # 用平均位移复核：命中够多、且残差足够小才算可信
    res = [(abs(t[1][0] - t[0][0] - avg[0]) + abs(t[1][1] - t[0][1] - avg[1])
            + abs(t[1][2] - t[0][2] - avg[2])) for t in lst]
    spread = max(res) if res else 9e9
    if len(lst) < 3 or spread > 0.05:
        _l("对齐：最高票只有 %d 根（共匹配 %d 根 · 最大残差 %.4f cm），不够可信，跳过"
           % (len(lst), n_matched, spread), "warn")
        return None, len(lst), len(pn)

    # ---- 方向守卫：参考模型必须是「整身（世界空间）」才有资格当参考。
    #
    #  对齐是**单向**的：它把「部件」搬进「参考」的空间。如果用户把方向选反了
    #  （拿部件当参考、拿整身当部件 —— 实测很容易发生：两个文件都勾了「自动对齐」
    #  并都选了同一个参考），整身反而会被搬进部件的头骨局部空间，
    #  表现成「整个材质和骨骼整体往下掉 15.5 单位」。数学上这个平移是**成立**的
    #  （残差极小、票数充足），所以光靠残差判据拦不住，必须另加几何判据：
    #
    #  ① 参考骨架的铺开程度必须明显大于部件（整身 150cm vs 部件几十 cm）；
    #  ② 若参考的骨架反而比部件还挤，说明参考自己就是局部空间的部件 → 拒绝。
    p_spread = _bone_spread(part)
    r_spread = _bone_spread(ref)
    if p_spread > 0 and r_spread < p_spread * 0.8:
        _l("对齐：参考模型的骨架铺开 %.1f cm，比本文件 %.1f cm 还小 —— "
           "参考本身就是个局部空间部件（多半是把方向选反了），跳过对齐"
           % (r_spread, p_spread), "warn")
        return None, len(lst), len(pn)

    _l("对齐：匹配 %d 根骨 · 其中 %d 根落在同一刚性位置（残差 ≤ %.4f cm）"
       % (n_matched, len(lst), spread))
    _l("      平移 (%.4f, %.4f, %.4f) cm" % avg)
    for t in lst[:8]:
        _l("      %-22s 部件(%8.3f,%8.3f,%8.3f) → 参考(%8.3f,%8.3f,%8.3f)"
           % (t[2], t[0][0], t[0][1], t[0][2],
              t[1][0], t[1][1], t[1][2]), "info")
    return avg, len(lst), len(pn)


def convert(path, out_path, scale_mode="fixed", lod_index=0, log=None,
            name=None, enable_edge=False, force_double_sided=False,
            textures=True, keep_add_uv=True, fbx_path=None,
            remove_alpha=True, flip_winding="auto", center=True,
            align_to=None):
    """uemodel → PMX。返回统计 dict。fbx_path 非空时同时导出 ASCII FBX。

    align_to：可选，参考模型（整身 .uemodel）路径。给了就把本文件当成
    「挂在参考模型某处的部件」，按自带的 *_adjust 骨与参考模型同名骨求平移，
    把它放回参考模型的世界位置（见 `find_align_offset` 的说明）。
    """
    _l = log or _log_default

    m = uemodelio.read_uemodel(path)
    _l("UEFormat v%d · %s · 骨骼 %d · LOD %d"
       % (m.version, m.name or os.path.basename(path),
          len(m.skeleton.bones), len(m.lods)))
    if not m.lods:
        raise ValueError("这个 .uemodel 里没有 LOD 数据")

    # 选 LOD：越界就退到最后一个（LOD 号越大越粗）
    if lod_index < 0 or lod_index >= len(m.lods):
        lod_index = 0
    lod = m.lods[lod_index]
    _l("使用 LOD[%d] %s：顶点 %d · 三角面 %d · 材质 %d · 表情 %d · UV %d"
       % (lod_index, lod.name, len(lod.vertices), len(lod.indices) // 3,
          len(lod.materials), len(lod.morphs), len(lod.uvs)))
    if len(m.lods) > 1:
        _l("提示：该文件有 %d 个 LOD，只导出第 %d 个"
           % (len(m.lods), lod_index), "info")

    # ---- 坐标变换：(x, y, z)_ue → (x, z, -y)_pmx（左手系→左手系，无镜面）
    def xf(p):
        return (p[0], p[2], -p[1])

    # ---- 缩放：UE 单位 = 1 cm，MMD 惯例 1 单位 = 8 cm（≈ 20 单位 = 160cm 身高）。
    #      用**固定**比例（UE cm → MMD 单位），而不是「按身高归一到 20 单位」：
    #      UEFormat 一个角色常被拆成多个 .uemodel（身体 / 头发+皮肤 / 服装…），
    #      每个都是同一世界尺度下的部件。若按各自身高都归一化到 20，部件会被
    #      各自放大成「整只角色」大小，载入同一场景就互相错位，表现成
    #      「某部件比例变大」——这正是与 Blender+mmd_tools 不一致的根源
    #      （mmd_tools 对所有文件用同一固定比例，部件间相对大小保持正确）。
    #      固定比例对**整身**模型同样没问题：161cm 角色 → 161/8 ≈ 20.1 单位，
    #      仍是 MMD 的标准身高。要「每个文件都拉到 20 单位」时用 --scale mmd。
    #
    #      注意 scale 必须**在骨骼之前**算出来：骨骼世界坐标和顶点共用同一个
    #      scale，否则会出现「网格高 20、骨链却还在源尺度」的鬼模型。
    UE_CM_PER_MMD = 8.0
    vs_src = lod.vertices
    pts = [xf(v) for v in vs_src]
    ys = [p[1] for p in pts]
    lo_y, hi_y = (min(ys), max(ys)) if ys else (0.0, 1.0)
    head = max(abs(lo_y), abs(hi_y), 1e-6)
    if scale_mode in ("fixed", "auto", None, ""):
        scale = 1.0 / UE_CM_PER_MMD
    elif scale_mode == "mmd":
        scale = 20.0 / max(hi_y, 1e-6)
        if hi_y <= 0:
            scale = 20.0 / head
    else:
        scale = float(scale_mode)
    src_h = hi_y - lo_y
    _l("缩放：源高 %.2f cm × %.6f → %.2f（fixed=1/8 cm，mmd=归一到 20）"
       % (src_h, scale, src_h * scale))

    # ---- 部件对齐：把本文件（头发/皮肤等）放回参考模型的世界位置。
    #      必须在「换轴 + 缩放」之前做，因为位移是**源 cm**，跟着顶点一起缩放。
    #      注意 align_off 一定要**同时**加到顶点和骨骼上（与 scale 同理）。
    align_off = (0.0, 0.0, 0.0)
    align_ref_pts = []
    if align_to:
        try:
            ref = uemodelio.read_uemodel(align_to)
            off, hit, tot = find_align_offset(m, ref, log=_l)
            if off is not None:
                align_off = off
                # 居中基准也要取自参考模型（否则各自居中 = 又错位）
                if ref.lods:
                    align_ref_pts = ref.lods[0].vertices
                _l("已按 %s 对齐（源坐标整体平移）"
                   % os.path.basename(align_to), "ok")
            else:
                _l("对齐失败（命中 %d/%d），按自身原点导出" % (hit, tot), "warn")
        except Exception as e:
            _l("对齐失败：%s（按自身原点导出）" % e, "warn")

    if align_off != (0.0, 0.0, 0.0):
        aoff = xf(align_off)
        pts = [(p[0] + aoff[0], p[1] + aoff[1], p[2] + aoff[2]) for p in pts]
        ys = [p[1] for p in pts]
        lo_y, hi_y = (min(ys), max(ys)) if ys else (0.0, 1.0)
        _l("对齐后：源高 %.2f cm → %.2f cm（缩放不变）" % (src_h, hi_y - lo_y))

    # ---- 居中：x/z 取包围盒中心、y 脚底归零（与 fbx2pmx 的 center=True 同口径）
    #
    #     ⚠ 部件（align_to 生效时）不能按**自己**的包围盒居中：那会把刚对齐好的
    #     位置又拉回原点，等于白对齐 —— 部件和整身各自居中后当然还是错位的。
    #     所以对齐时改用**参考模型**的居中基准（由参考模型的顶点包围盒算），
    #     这样部件和整身共用同一套偏移，相对位置才对得上。
    if center and pts:
        if align_ref_pts:
            refs = [xf(v) for v in align_ref_pts]
            ra = [p[0] for p in refs]
            rb = [p[1] for p in refs]
            rc = [p[2] for p in refs]
            off = (-(min(ra) + max(ra)) * 0.5 * scale,
                   -min(rb) * scale,
                   -(min(rc) + max(rc)) * 0.5 * scale)
            _l("居中：按参考模型包围盒（偏移 (%.3f, %.3f, %.3f)）" % off, "info")
        else:
            xs = [p[0] for p in pts]
            zs = [p[2] for p in pts]
            off = (-(min(xs) + max(xs)) * 0.5 * scale,
                   -lo_y * scale,
                   -(min(zs) + max(zs)) * 0.5 * scale)
            _l("居中：偏移 (%.3f, %.3f, %.3f)" % off, "info")
    else:
        off = (0.0, 0.0, 0.0)

    def sp(p):
        """源坐标 → PMX 坐标（对齐 + 换轴 + 缩放 + 居中），顶点和骨骼共用。"""
        q = (p[0] + align_off[0], p[1] + align_off[1], p[2] + align_off[2]) \
            if align_off != (0.0, 0.0, 0.0) else p
        t = xf(q)
        return (t[0] * scale + off[0], t[1] * scale + off[1],
                t[2] * scale + off[2])

    src_bones = m.skeleton.bones
    n_src = len(src_bones)

    # 世界矩阵（局部 = T·R·S，父亲左乘）
    world = [None] * n_src
    state = [0] * n_src

    def build(i, depth=0):
        if i < 0 or i >= n_src or state[i] == 2 or depth > 512:
            return
        if state[i] == 1:               # 环：当成根处理，别死循环
            q = _qmat(src_bones[i].rot)
            world[i] = (q, tuple(src_bones[i].pos))
            state[i] = 2
            return
        state[i] = 1
        b = src_bones[i]
        p = b.parent
        if p < 0 or p >= n_src:
            world[i] = (_qmat(b.rot), tuple(b.pos))
        else:
            build(p, depth + 1)
            pr, pt = world[p]
            if pr is None:
                world[i] = (_qmat(b.rot), tuple(b.pos))
            else:
                r = _mm(pr, _qmat(b.rot))
                t = _add(_mv(pr, b.pos), pt)
                world[i] = (r, t)
        state[i] = 2

    for i in range(n_src):
        build(i)
    for i in range(n_src):
        if world[i] is None:
            world[i] = (_qmat(src_bones[i].rot), tuple(src_bones[i].pos))

    bone_pos = [sp(world[i][1]) for i in range(n_src)]

    # ---- 骨骼顺序：DFS，保证父在子前面（PMXEditor 的骨顺表依赖这个）
    kids = [[] for _ in range(n_src)]
    roots = []
    for i, b in enumerate(src_bones):
        p = b.parent
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
        if not seen[i]:                  # 环里漏掉的补到最后
            seen[i] = True
            order.append(i)

    slot = {bi: k for k, bi in enumerate(order)}
    used = set()
    pmx_bones = []
    for src in order:
        b = src_bones[src]
        p = b.parent if 0 <= b.parent < n_src else -1
        pmx_bones.append({
            "name": _uniq(b.name, used),
            "name_en": b.name or "bone",
            "pos": bone_pos[src],
            "parent": slot.get(p, -1),
            "layer": 0, "flag": 0, "tail_kind": 0, "tail": (0.0, 0.0, 0.0),
            "inherit_rot": None, "inherit_mov": None, "fixed_axis": None,
            "local_axis": None, "external": None, "ik": None,
            "_kids": [],
        })
    for src in order:
        b = src_bones[src]
        p = b.parent if 0 <= b.parent < n_src else -1
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
    _l("骨骼：%d 根（原始 %d 根）" % (len(pmx_bones), n_src))

    # ---- 绕序自动判定（必须用变换后的数据投票；缩放/平移不影响手性判定）
    pos_t = pts
    nrm_t = [xf(n) for n in lod.normals] if lod.normals else []
    if flip_winding in ("auto", None, ""):
        need_flip, ag, dis = pmxio.detect_winding(pos_t, nrm_t, lod.indices)
        _l("绕序自动判定：与法线同向 %d 面 / 反向 %d 面 → %s"
           % (ag, dis, "需要反转" if need_flip else "保持不变"))
    else:
        need_flip = bool(flip_winding)

    # ---- 顶点
    per_vertex = {}
    for w in lod.weights:
        if 0 <= w.vertex:
            per_vertex.setdefault(w.vertex, []).append((w.bone, w.weight))

    uv0 = lod.uvs[0].uvs if lod.uvs else []
    extra = []
    if keep_add_uv:
        for st in lod.uvs[1:]:
            extra.append(st.uvs)
    pmx_verts = []
    n_weights_dropped = 0
    for i, v in enumerate(vs_src):
        P = sp(v)
        nv = lod.normals[i] if i < len(lod.normals) else (0.0, 0.0, 1.0)
        N = xf(nv)
        ln = (N[0] ** 2 + N[1] ** 2 + N[2] ** 2) ** 0.5 or 1.0
        N = (N[0] / ln, N[1] / ln, N[2] / ln)
        U = uv0[i] if i < len(uv0) else (0.0, 0.0)
        wl = [(b, w) for b, w in per_vertex.get(i, []) if w > 1e-6]
        if len(wl) > 4:
            n_weights_dropped += len(wl) - 4
            wl.sort(key=lambda t: -t[1])
            wl = wl[:4]
        wl = [(b, w) for b, w in wl if 0 <= b < len(pmx_bones)]
        if not wl:
            wl = [(0, 1.0)]
        tot = sum(w for _, w in wl) or 1.0
        wl = [(b, w / tot) for b, w in wl]
        if len(wl) == 1:
            wtype, wbones, wweights = 0, [wl[0][0]], []
        elif len(wl) == 2:
            wtype, wbones, wweights = 1, [wl[0][0], wl[1][0]], [wl[0][1]]
        else:
            wtype = 2
            wbones = [wl[k][0] if k < len(wl) else wl[0][0] for k in range(4)]
            wweights = [wl[k][1] if k < len(wl) else 0.0 for k in range(4)]
        add = []
        for st in extra:
            uv = st[i] if i < len(st) else (0.0, 0.0)
            add.append((uv[0], uv[1], 0.0, 0.0))
        pmx_verts.append({"pos": P, "normal": N, "uv": (U[0], U[1]),
                          "add_uv": add, "wtype": wtype, "wbones": wbones,
                          "wweights": wweights, "sdef": None, "edge": 1.0})

    # ---- 面
    pmx_faces = []
    idx = lod.indices
    nv_tot = len(vs_src)
    for t in range(0, len(idx) - 2, 3):
        a, b, c = idx[t], idx[t + 1], idx[t + 2]
        if a >= nv_tot or b >= nv_tot or c >= nv_tot:
            continue
        if need_flip:
            a, c = c, a
        pmx_faces.extend((a, b, c))

    # ---- 材质 + 贴图
    folder = os.path.dirname(os.path.abspath(path))
    out_dir = os.path.dirname(os.path.abspath(out_path))
    tex_dir = os.path.join(out_dir, "textures")
    idx_dir = _FolderIndex(folder)
    pmx_textures = []
    tex_slot = {}
    copied = []
    n_linked = 0

    def use_texture(name):
        if not name:
            return -1
        if name in tex_slot:
            return tex_slot[name]
        real = None
        for ext in (".png", ".PNG", ".tga", ".TGA", ".jpg", ".jpeg", ".bmp", ".dds"):
            r = idx_dir.get(name + ext)
            if r:
                real = r
                break
        if real is None:
            tex_slot[name] = -1
            return -1
        src = os.path.join(folder, real)
        # MMD 只认 png/tga/bmp/jpg/dds；其它后缀统一按 png 拷过去
        ext = os.path.splitext(real)[1].lower()
        dst_name = name + (ext if ext in (".png", ".tga", ".bmp", ".jpg",
                                          ".jpeg", ".dds") else ".png")
        try:
            os.makedirs(tex_dir, exist_ok=True)
            _copy_texture(src, os.path.join(tex_dir, dst_name), remove_alpha)
        except OSError:
            tex_slot[name] = -1
            return -1
        pmx_textures.append("textures/" + dst_name)
        copied.append(dst_name)
        tex_slot[name] = len(pmx_textures) - 1
        return tex_slot[name]

    pmx_materials = []
    expect = 0
    noncontig = 0
    for mt in lod.materials:
        info = {"tex": "", "color": None, "blend": None, "translucent": False}
        n_face = max(0, mt.num_faces)
        # PMX 的材质按顺序吃连续的面段，所以 uemodel 里 first_index 必须依次接上；
        # 对不上就记账，最后统一在日志里说清楚（不静默）。
        if mt.first_index // 3 != expect:
            noncontig += 1
        expect = mt.first_index // 3 + n_face
        ti = -1
        diff = (1.0, 1.0, 1.0, 1.0)
        if textures:
            jp = _mat_json(folder, idx_dir, mt)
            if jp:
                info = _read_mat_json(jp)
                ti = use_texture(info["tex"])
                if ti >= 0:
                    n_linked += 1
                if info["color"]:
                    diff = info["color"]
        flag = 0x0E | (0x10 if enable_edge else 0)
        if force_double_sided:
            flag |= 0x01
        pmx_materials.append({
            "name": mt.name or ("mat%d" % len(pmx_materials)),
            "name_en": mt.name or ("mat%d" % len(pmx_materials)),
            "diffuse": (diff[0], diff[1], diff[2], diff[3]),
            "specular": (0.0, 0.0, 0.0),
            "shininess": 0.0,
            "ambient": (0.5, 0.5, 0.5),
            "flag": flag,
            "edge_color": (0.0, 0.0, 0.0, 1.0),
            "edge_size": 1.0 if enable_edge else 0.0,
            "tex": ti, "sph": -1, "sph_mode": 0,
            # toon：**不使用**。
            # ⚠ 方向极易搞反：flag=1 才是内建 toon（1 字节编号，引用 MMD 的
            #   toon01.bmp..toon10.bmp，**编号 0 = toon01.bmp 而不是「不使用」**，
            #   见 mmd_tools 的 `"toon%02d.bmp" % (shared + 1)`，且 MMD 的 Data
            #   目录里根本没有 toon00.bmp）；flag=0 是本模型纹理表索引，
            #   **-1 = なし**。所以「不使用 toon」= flag 0 + 索引 -1。
            # 2026-09-24 修：以前写 flag=1/0，等于硬套一层 toon01.bmp。
            "toon_flag": 0, "toon": -1, "memo": "",
            "faces": n_face,
            "_tex_file": (os.path.basename(pmx_textures[ti])
                          if isinstance(ti, int) and 0 <= ti < len(pmx_textures)
                          else None),
        })
    if len(pmx_materials) == 0:
        pmx_materials.append({
            "name": "material_0", "name_en": "material_0",
            "diffuse": (1.0, 1.0, 1.0, 1.0), "specular": (0.0, 0.0, 0.0),
            "shininess": 0.0, "ambient": (0.5, 0.5, 0.5),
            "flag": 0x0E | (0x10 if enable_edge else 0), "edge_color": (0.0, 0.0, 0.0, 1.0),
            "edge_size": 1.0 if enable_edge else 0.0, "tex": -1, "sph": -1,
            "sph_mode": 0, "toon_flag": 0, "toon": -1, "memo": "",
            "faces": len(pmx_faces) // 3,
        })
    else:
        cover = sum(x["faces"] for x in pmx_materials)
        if cover != len(pmx_faces) // 3:
            pmx_materials[0]["faces"] += len(pmx_faces) // 3 - cover
    # PMX 材质的「面数」字段其实是**索引数**（每个三角面占 3 个），不是三角面数！
    # 上面按三角面记账更直观，写出前统一 ×3。fbx2pmx（`pw.i32(cnt * 3)`）也是
    # 这个口径；写成三角面数会让 MMD 只画 1/3 的面、
    # PMX→VRM 也会把面段整体错位。
    for x in pmx_materials:
        x["faces"] = int(x["faces"]) * 3
    _l("材质：%d 个 · 关联到贴图 %d 个 · 拷贝贴图 %d 张%s"
       % (len(pmx_materials), n_linked, len(copied),
          "" if not noncontig else "（注意：%d 个材质的面区间不连续）" % noncontig))

    # ---- 表情（MORPHTARGETS → PMX 顶点表情）
    # UEFormat 规范：struct FMorphData { FVector PositionDelta; FVector TangentZDelta;
    # uint VertexIndex; } —— **是位移增量，不是绝对坐标**（实测样本也是：|delta|
    # 0.5~2.5 而顶点坐标 ~134）。所以 PMX 的顶点表情偏移 = 「换轴 + 缩放」后的位移，
    # **不能**套带居中平移的 sp()、更不能再减一次顶点自身坐标 —— 那等于把顶点搬到
    # 绝对坐标 pos（≈ 原点），一拖表情滑块整束头发就塌到脚底、留下从头顶拖到地面的
    # 长条拉伸面（用户报的「表情顶点拉伸」就是这个）。
    def sdir(d):
        """源位移 → PMX 位移（只换轴 + 缩放，不平移、也不加对齐）。"""
        t = xf(d)
        return (t[0] * scale, t[1] * scale, t[2] * scale)

    used_mo = set()
    pmx_morphs = []
    for mop in lod.morphs:
        offs = []
        for d in mop.deltas:
            if not (0 <= d.vertex < len(pmx_verts)):
                continue
            if abs(d.pos[0]) + abs(d.pos[1]) + abs(d.pos[2]) < 1e-9:
                continue
            dp = sdir(d.pos)
            if abs(dp[0]) + abs(dp[1]) + abs(dp[2]) < 1e-9:
                continue
            offs.append((d.vertex, dp))
        if not offs:
            continue
        nm = _uniq(mop.name, used_mo)
        pmx_morphs.append({"name": nm, "name_en": mop.name or nm,
                           "panel": _morph_panel(mop.name), "kind": 1,
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
    title = name or m.name or stem
    comment = ["由 UEFormat(.uemodel) 转换：%s" % os.path.basename(path),
               "UEFormat v%d · LOD[%d] %s" % (m.version, lod_index, lod.name),
               "骨骼/表情沿用原名称；英文骨名无法直接套用 MMD 日文标准动作数据",
               "converted by uemodel2pmx.py"]
    model = pmxio.new_model(title)
    model["name"] = title
    model["name_en"] = title
    model["comment"] = "\r\n".join(comment)
    model["comment_en"] = "\r\n".join(comment)
    model["vertices"] = pmx_verts
    model["faces"] = pmx_faces
    model["textures"] = pmx_textures
    model["materials"] = pmx_materials
    model["bones"] = pmx_bones
    model["morphs"] = pmx_morphs
    model["frames"] = frames
    model["add_uv"] = len(extra) if extra else 0
    if not enable_edge:
        pmxio.strip_edges(model, force_double_sided=False)

    size = pmxio.write_pmx(model, out_path)
    _l("已写出 %s（%.2f MB）· 顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d · 表情 %d"
       % (os.path.basename(out_path), size / 1048576.0, len(pmx_verts),
          len(pmx_faces) // 3, len(pmx_bones), len(pmx_materials),
          len(pmx_morphs)), "ok")
    if n_weights_dropped:
        _l("提示：%d 条第 5 及以后的骨骼权重被丢弃（PMX 最多 4 根）"
           % n_weights_dropped, "info")

    # height = **输出 PMX 里**的真实高度（源高度 × scale），不是源高度本身：
    # 之前写成 hi_y-lo_y，mmd 模式下会误报成源高度，容易让人以为缩放没生效。
    st = {"bytes": size, "vertices": len(pmx_verts),
          "tris": len(pmx_faces) // 3, "bones": len(pmx_bones),
          "materials": len(pmx_materials), "morphs": len(pmx_morphs),
          "textures": len(pmx_textures), "scale": scale, "lod": lod_index,
          "height": (hi_y - lo_y) * scale, "src_height": src_h,
          "align_off": align_off,
          "aligned": align_off != (0.0, 0.0, 0.0)}

    # ---- 可选：同时导出 FBX（与 PMX 同一姿势，Y-up 右手系，便于进 Blender/UE）
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
    ap = argparse.ArgumentParser(description="UEFormat(.uemodel) → MMD PMX（纯 Python）")
    ap.add_argument("src")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--scale", default="fixed",
                    help="fixed（默认：UE cm→MMD 1/8，部件保持相对大小）"
                         " / mmd（每个文件归一到 20 单位） / 具体倍率")
    ap.add_argument("--lod", type=int, default=0)
    ap.add_argument("--edge", action="store_true", help="给材质开启 MMD 轮廓线")
    ap.add_argument("--force-double-sided", action="store_true")
    ap.add_argument("--no-textures", action="store_true", help="不关联/拷贴图")
    ap.add_argument("--keep-alpha", action="store_true",
                    help="保留贴图 alpha（默认去掉：UE 贴图的 alpha 多是数据遮罩，"
                         "留着会让 MMD 里整个模型透明）")
    ap.add_argument("--no-add-uv", action="store_true", help="不写附加 UV")
    ap.add_argument("--align-to", default=None,
                    help="参考模型（整身 .uemodel）路径：按 *_adjust 骨求平移，"
                         "把本文件当成挂在它上面的部件放回正确位置")
    ap.add_argument("--fbx", action="store_true", help="同时导出 FBX")
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    out = a.out or (os.path.splitext(a.src)[0] + ".pmx")
    fbx = (os.path.splitext(out)[0] + ".fbx") if a.fbx else None
    st = convert(a.src, out, scale_mode=a.scale, lod_index=a.lod,
                 log=lambda m_, t=None: print(m_), name=a.name,
                 enable_edge=a.edge, force_double_sided=a.force_double_sided,
                 textures=not a.no_textures, keep_add_uv=not a.no_add_uv,
                 remove_alpha=not a.keep_alpha, fbx_path=fbx,
                 align_to=a.align_to)
    print("完成：%s（%.2f MB）" % (out, st["bytes"] / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
