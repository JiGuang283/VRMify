"""fbx2pmx.py - convert a binary FBX (7.x) into a PMX 2.0 model.

Standalone: no Autodesk FBX SDK, no Blender, no mmd_tools.

What it maps:
    Geometry  -> PMX vertices + triangles
    Model(Mesh)  material link -> PMX materials (one draw segment per mesh)
    Deformer/Skin + Cluster    -> PMX bone weights (BDEF1/2/4)
    Model(LimbNode) hierarchy  -> PMX bones
    LayerElementUV / Normal    -> PMX UV0 / vertex normals
    Deformer/BlendShape chain  -> PMX vertex morphs (表情)
        Geometry -> BlendShape -> BlendShapeChannel -> Shape, where each Shape
        holds Indexes (affected control points) + Vertices (relative position
        deltas).  The morph panel (眉/目/口/その他) is inferred from the channel
        name; every morph is written as PMX kind=1 (頂点).

What it does NOT and cannot invent: physics rigid bodies / joints.  Texture
references ARE extracted when the FBX links them via Texture/Video nodes; the
actual image files are copied into ``<out>/textures/`` only if they can be found
on disk (the FBX often stores an absolute game path).

Usage:
    python fbx2pmx.py "model.fbx" -o "model.pmx"
    python fbx2pmx.py "model.fbx" --info
    python fbx2pmx.py "model.fbx" --no-morphs     # skip blend shapes
    python fbx2pmx.py "model.fbx" --alpha auto    # per-texture alpha decision
    python fbx2pmx.py "model.fbx" --keep-untextured-meshes

Texture alpha (``--alpha``): MMD uses the **texture alpha channel as material
transparency**, and game textures often store something else there.  Three
modes: ``keep`` (copy as-is), ``strip`` (always drop the alpha channel),
``auto`` (default - decide per image, see :func:`decide_alpha`).  Stripped
copies are written as ``<name>_noalpha.<ext>`` so the original is never
touched.  ``--remove-alpha`` is kept as a legacy alias for ``--alpha strip``.

Facing (``--no-auto-facing`` / ``--face-180``): FBX has no "which way does the
character look" field, and MMD always wants the model to face -Z.  Because the
Z negation below already turns a +Z-facing model into a -Z-facing one, only
models authored facing -Z need an extra 180 degree turn.  That is decided per
file by :func:`detect_forward_turn` (toe bones, then face-mesh centroid).

Untextured meshes (``--keep-untextured-meshes``): meshes that carry **no
material at all** are dropped by default.  Game exports (Unity / HoYo style)
often ship a sheet named ``EffectMesh`` - no material node, no texture, UVs
spread over the whole atlas, geometry a thin plate spanning the body.  Exported
as a normal mesh it becomes an opaque grey slab sitting on top of the dress,
which looks exactly like "the texture got flipped" in MMD.  Pass
``--keep-untextured-meshes`` to keep them anyway.
"""
import argparse
import math
import os
import shutil
import struct
import sys

# 让 `python convert/fbx2pmx.py ...` 也能直接跑：脚本自己所在目录（convert/）和同级的
# formats/ 都要进 sys.path。以前这里只加了 convert/，于是 `import pmxio` 之类会
# ModuleNotFoundError —— 而 README 里给的命令行用法就是这么调的。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "formats")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import fbx_reader as fbx
import unityasset
import vrmio


# --------------------------------------------------- 贴图 alpha / 朝向 ------
#
# 这一段是 PEPlugins-FBXimport（C# 插件）里两个修复的 Python 移植，
# 原始判据见 PEPlugins-FBXimport\FbxImporterPlugin\TextureAlphaStripper.cs
# 与 FbxReader.cs 的 DetectForwardTurn / LooksLikeToe / LooksLikeFace。
# 两边的常量、阈值、名字特征刻意保持一致，改一边要同步另一边。

ALPHA_KEEP = "keep"          # 原样保留 alpha
ALPHA_AUTO = "auto"          # 逐张贴图判一次（默认）
ALPHA_STRIP = "strip"        # 一律去掉 alpha
ALPHA_MODES = (ALPHA_KEEP, ALPHA_AUTO, ALPHA_STRIP)

ALPHA_NOALPHA_SUFFIX = "_noalpha"   # 去 alpha 副本的后缀；改了要同步 README

ALPHA_TRANSPARENT_BELOW = 8     # alpha 低于它算「全透明」
ALPHA_OPAQUE_ABOVE = 250        # alpha 大于等于它算「实心」
ALPHA_MOSTLY_EMPTY = 0.75       # 「几乎全是全透明」的下限
ALPHA_ALMOST_NO_SOLID = 0.05    # 「几乎没有实心像素」的上限
ALPHA_MAX_SAMPLES = 4000        # 每张贴图最多采样多少个 UV

# 没有 UV 时更保守：要求「几乎全空」才敢去
ALPHA_NOUV_EMPTY = 0.9
ALPHA_NOUV_SOLID = 0.01


def alpha_mode_of(value, legacy_remove_alpha=None):
    """把界面/配置里拿到的值归一成三个 alpha 模式之一。

    ``legacy_remove_alpha`` 供旧配置的布尔键 ``remove_alpha`` 兜底：
    True -> "auto"（旧行为是「一律去 alpha」，正是要修的那个；迁移到自动判定
    既保留「要去掉遮罩型 alpha」的意图，又不会再把真透明贴图一起削掉），
    False -> "keep"。

    ⚠ **两边都没给（``None``/``None``）时返回 "auto"**，这才是新默认。
    所以调用方**不要**把 ``remove_alpha`` 的默认值写成 ``False`` —— 那会被
    当成「用户显式要 keep」，把 auto 默认遮蔽掉（`convert()` /
    ``--remove-alpha`` 都踩过这个坑，已改成 None）。
    """
    if isinstance(value, str) and value.lower() in ALPHA_MODES:
        return value.lower()
    if isinstance(value, bool):
        return ALPHA_AUTO if value else ALPHA_KEEP
    if legacy_remove_alpha is not None:
        return ALPHA_AUTO if legacy_remove_alpha else ALPHA_KEEP
    return ALPHA_AUTO


def looks_like_toe(name):
    """脚尖骨的名字特征（刻意排除 ToeTwist / ToeNub 这类不是尖端的骨）。

    与 C# 的 LooksLikeToe 逐字一致：只排除 toetip / toe_nub / toeend /
    toetwist 这几个写法，``Toe0Nub`` 这种驼峰串不会被排除 —— 保持与已验证
    版本相同的行为，免得两边判据分叉。
    """
    if not name:
        return False
    low = name.lower()
    if any(t in low for t in ("toetip", "toe_nub", "toeend", "toetwist")):
        return False
    return ("toe" in low or "つま先" in name or "爪先" in name or "ball" in low)


def looks_like_face(name):
    """「一定长在正面」的网格名特征：脸 / 眼 / 瞳 / 眉 / 睫 / 嘴 / 鼻。"""
    if not name:
        return False
    low = name.lower()
    if any(t in low for t in ("face", "eye", "pupil", "iris", "brow", "lash",
                              "mouth", "nose", "lip")):
        return True
    return any(t in name for t in ("顔", "脸", "臉", "眼", "瞳", "眉", "睫",
                                   "口", "鼻"))


def _detect_forward_by_toe(scene, bones):
    """判据一：脚 → 脚尖的位移方向。

    解剖学上脚尖一定朝前，这个判据不会被「脸/眼材质叫什么」这类美术习惯影响。
    返回 (需要转 180° 吗, 依据说明)；判不出来返回 (None, 依据)。
    """
    sx = sz = 0.0
    n = 0
    hit = []
    for m in bones:
        if not looks_like_toe(m.name or ""):
            continue
        p = m.parent
        if p is None or p.cls != "LimbNode":
            continue
        tp = m_translation(m.world)
        pp = m_translation(p.world)
        dx = tp[0] - pp[0]
        dz = tp[2] - pp[2]
        if abs(dx) + abs(dz) < 1e-9:
            continue
        sx += dx
        sz += dz
        n += 1
        if len(hit) < 4:
            hit.append(m.name)
    if n == 0:
        return None, "no toe bone"
    mx = sx / n
    mz = sz / n
    names = ", ".join(hit)
    if abs(mx) > abs(mz):
        # 朝 ±X（侧身 / 特殊摆位），180° 旋转修不了，只能交给用户
        return None, ("toe bones point along X (%s), a 180 turn cannot fix it"
                      % names)
    need = mz < 0.0
    return need, ("toe bones %s -> front %s"
                  % (names, "+Z (no turn)" if not need else "-Z (needs 180)"))


def _detect_forward_by_face_meshes(scene):
    """判据二（兜底）：脸 / 眼 / 眉这些一定长在正面的网格，重心相对整体重心偏哪边。"""
    face_z = 0.0
    face_n = 0
    all_z = 0.0
    all_n = 0
    left = right = None
    for gid, mid in scene.geometry_model.items():
        g = scene.byid.get(gid)
        model = scene.models.get(mid)
        if g is None or model is None or all_n > 40000:
            continue
        is_face = looks_like_face(model.name or "")
        verts = g.get("Vertices")
        if verts is None:
            continue
        w = m_mul(model.world, scene._geom_matrix(scene.byid.get(mid)))
        v = verts.props[0]
        nverts = len(v) // 3
        step = max(1, nverts // 200)
        for i in range(0, nverts, step):
            p = m_point(w, v[i * 3:i * 3 + 3])
            all_z += p[2]
            all_n += 1
            left = p[0] if left is None else min(left, p[0])
            right = p[0] if right is None else max(right, p[0])
            if is_face:
                face_z += p[2]
                face_n += 1
    if face_n < 8 or all_n < 8:
        return None, "face meshes found=%d (too few)" % face_n
    dz = face_z / face_n - all_z / all_n
    width = (right - left) if (left is not None and right is not None) else 0.0
    if width > 0.0 and abs(dz) < width * 0.02:
        # 偏得太少就等于没偏，别硬判（正面对称的模型、裙摆很大的模型都可能这样）
        return None, "face meshes not clearly offset (dz=%.4f)" % dz
    need = dz < 0.0
    return need, ("face meshes offset dz=%.4f -> front %s"
                  % (dz, "+Z (no turn)" if not need else "-Z (needs 180)"))


def detect_forward_turn(scene, bones=None):
    """要不要额外转 180°（等价于 C# 的 Rot180Y），返回 (bool|None, 依据说明)。

    为什么必须判：FBX 里「角色朝哪边」完全由导出方决定，两种都很常见 ——
    Unity / 游戏引擎导出的角色多朝 +Z，一部分 DCC 预设导出的朝 -Z。
    MMD 的正面固定是 -Z，而本转换的 Z 取反本来就会把 +Z 翻成 -Z，
    **也就是说朝 +Z 的文件只需要 Z 取反、不能再转 180°**；朝 -Z 的才要补一次。

    判定在「未 FlipZ 的 FBX 原始朝向坐标系」里做（也就是本函数里 scene 给出来的
    原始坐标），结论很直接：正面朝 +Z → 不用转；朝 -Z → 要转。

    判据按可靠性排序，取第一个能用的；都拿不到就返回 None（交给用户的设置）。
    """
    if bones is None:
        bones = [m for m in scene.models.values() if m.cls == "LimbNode"]
    need, why = _detect_forward_by_toe(scene, bones)
    if need is not None:
        return need, why
    need2, why2 = _detect_forward_by_face_meshes(scene)
    return need2, why + " / " + why2


# ------------------------------------------------------------------ math ----

def m_ident():
    return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]


def m_mul(a, b):
    o = [0.0] * 16
    for i in range(4):
        ai = i * 4
        for j in range(4):
            o[ai + j] = (a[ai] * b[j] + a[ai + 1] * b[4 + j] +
                         a[ai + 2] * b[8 + j] + a[ai + 3] * b[12 + j])
    return o


def m_point(m, p):
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11])


def m_dir(m, p):
    x, y, z = p
    return (m[0] * x + m[1] * y + m[2] * z,
            m[4] * x + m[5] * y + m[6] * z,
            m[8] * x + m[9] * y + m[10] * z)


def m_translation(m):
    return (m[3], m[7], m[11])


# FBX 的 RotationOrder（EFbxRotationOrder）：字母是「先转哪个轴」，矩阵按相反顺序相乘。
# 0 XYZ（默认）→ Rz·Ry·Rx；6 是 SphericXYZ，按 XYZ 处理。
_ROT_ORDER = {0: "zyx", 1: "yzx", 2: "xzy", 3: "zxy", 4: "yxz", 5: "xyz", 6: "zyx"}


def m_trs(t, rot_deg, s, order=0):
    rx, ry, rz = (math.radians(v) for v in rot_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    mats = {"x": [1, 0, 0, 0, 0, cx, -sx, 0, 0, sx, cx, 0, 0, 0, 0, 1],
            "y": [cy, 0, sy, 0, 0, 1, 0, 0, -sy, 0, cy, 0, 0, 0, 0, 1],
            "z": [cz, -sz, 0, 0, sz, cz, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]}
    a, b, c = _ROT_ORDER.get(order, "zyx")
    r = m_mul(mats[a], m_mul(mats[b], mats[c]))
    for i in range(3):
        for j in range(3):
            r[i * 4 + j] *= s[j]
    r[3], r[7], r[11] = t
    return r


def parse_matrix_str(s):
    vals = [float(x) for x in s.strip().strip("[]").split(",")]
    return vals if len(vals) == 16 else m_ident()


def m_rot(rot_deg, order=0):
    """欧拉角（度）→ 纯旋转矩阵，与 m_trs 同一套约定。默认 XYZ 顺序（Rz·Ry·Rx）。

    注意默认不要改成 Rx·Ry·Rz：实测同一份 FBX 下两种顺序的贴合度差距极大
    （Rz·Ry·Rx 中位 5.67cm vs Rx·Ry·Rz 中位 18.09cm）。节点自己写了 RotationOrder 时
    才按它来（只作用于 Lcl Rotation；Pre / PostRotation 按 FBX SDK 的规定总是 XYZ）。
    """
    return m_trs([0.0, 0.0, 0.0], rot_deg, [1.0, 1.0, 1.0], order)


def m_rot_inv(rot_deg):
    """纯旋转矩阵的逆（3×3 转置）。FBX 里的 Rpost⁻¹ 需要它。"""
    r = m_rot(rot_deg)
    o = list(r)
    for i in range(3):
        for j in range(3):
            o[j * 4 + i] = r[i * 4 + j]
    return o


def m_translate(v):
    m = m_ident()
    m[3], m[7], m[11] = v
    return m


def m_scale(s):
    m = m_ident()
    m[0], m[5], m[10] = s
    return m


# ------------------------------------------------------------- fbx scene ----

class Model:
    """A FBX Model node (mesh, bone, root ...)."""

    __slots__ = ("id", "name", "cls", "local", "world", "parent", "children")

    def __init__(self, nid, name, cls):
        self.id = nid
        self.name = name
        self.cls = cls
        self.local = m_ident()
        self.world = m_ident()
        self.parent = None
        self.children = []


def _clean_name(s):
    return s.split("\x00")[0]


def _ns_strip(s):
    """剥掉 FBX 的 `Class::name` 命名空间前缀。

    Autodesk 自家的导出器一律写 `"SubDeformer::Smile"` / `"Shape::Smile"`，
    而 FBX SDK 的 GetName() 会自动剥前缀、Blender 也会剥；本工程的解析器是
    直接读字段原文，所以这里补一次，免得表情名/材质名带一截 `Shape::`。
    """
    return s.split("::")[-1] if "::" in s else s


class Scene:
    def __init__(self, path):
        self.path = path
        self.ver, top = fbx.load(path)
        root = fbx.FbxNode("root", [], top)
        objs = root.get("Objects")
        self.objects = objs.children
        self.byid = {}
        for c in self.objects:
            if c.props and isinstance(c.props[0], int):
                self.byid[c.props[0]] = c

        # connections:  ['OO', child_id, parent_id]
        self.parents = {}
        # **按文件顺序**保留一份 (child, parent) 列表。
        #
        # 这一步不是可有可无：FBX 的 LayerElementMaterial 里的材质索引，对应的
        # 是 FBX SDK `FbxNode::GetMaterial(i)` 的顺序，也就是**该节点在
        # Connections 段里被连接的先后顺序**。
        #
        # 以前 _resolve_links() 直接遍历 self.parents（按 child 首次出现的顺序），
        # 当同一批材质对多个网格的连接顺序与「材质节点在 Objects 段里出现的顺序」
        # 不一致时就会串位。实测 NPC_Avatar_Lady_Bow_Anastasya：Cloth01 在文件里
        # 的顺序是 Hair(490) Body(491) **Dress(492)** Body01(493) Crystal(494)
        # Crystal01(495) Crystal02(496)，而父节点遍历给出的却是
        # Hair Body Body01 Crystal Crystal01 **Dress** Crystal02 —— Dress 被挪到
        # 了第 6 位，于是 7 个槽位里有 5 个配错贴图（一致性只有 19%），
        # 表现就是「有些部分的贴图不对 / 像被换了 UV」。
        self.conn_list = []
        self.conn_prop = {}             # (child, parent) -> 属性名（"OP" 连接，比如贴图接在 DiffuseColor 上）
        for c in root.get("Connections").children:
            if len(c.props) >= 3:
                self.parents.setdefault(c.props[1], []).append(c.props[2])
                self.conn_list.append((c.props[1], c.props[2]))
                if len(c.props) >= 4 and c.props[0] == "OP":
                    self.conn_prop[(c.props[1], c.props[2])] = str(c.props[3])

        self._build_models()
        self._resolve_links()
        self._build_textures()
        self._build_morphs()

    # -- helpers
    def _props70(self, node):
        out = {}
        p70 = node.get("Properties70")
        if p70:
            for pr in p70.children:
                if len(pr.props) >= 5:
                    out[pr.props[0]] = pr.props[4:]
        return out

    def _local_matrix(self, node):
        """节点局部矩阵，按 FBX SDK 的 EvaluateLocalTransform 完整合成：

            T · Roff · Rp · Rpre · R · Rpost⁻¹ · Rp⁻¹ · Soff · Sp · S · Sp⁻¹

        这里最容易漏掉的是 **PreRotation**，而且漏了不会立刻露馅：节点自身的
        平移在这个式子最左边，不受自己 PreRotation 影响，所以单看一根骨骼的
        位置是正常的；但它的**所有后代**都活在少了一个 Rpre 的坐标系里，整条
        骨链会歪掉/翻转。实测 "Crimson Rumor" 这个包 385 个节点里有 185 个带
        非零 PreRotation（不少正好是 180°），漏掉它会让 83% 的骨骼偏位（最大
        106cm）——衣服/发饰的骨链会从"垂在身侧"变成"横着翘到体外"，看起来就像
        骨骼被倒过来了。
        判据（顶点到其主骨骼线段的距离，越小越贴合）：
            只 T·R·S：中位 11.99cm / 平均 20.92cm
            带 PreRotation：中位  5.67cm / 平均  6.12cm
        """
        d = self._props70(node)

        def v3(key):
            v = d.get(key)
            return [float(x) for x in v[:3]] if v else None

        t = v3("Lcl Translation") or [0.0, 0.0, 0.0]
        r = v3("Lcl Rotation") or [0.0, 0.0, 0.0]
        s = v3("Lcl Scaling") or [1.0, 1.0, 1.0]
        rp = v3("RotationPivot")
        pre = v3("PreRotation")
        post = v3("PostRotation")
        roff = v3("RotationOffset")
        sp = v3("ScalingPivot")
        soff = v3("ScalingOffset")
        ro = d.get("RotationOrder")
        order = int(ro[0]) if ro else 0

        def neg(v):
            return [-v[0], -v[1], -v[2]]

        # Rp / Sp 是**枢轴点**（平移），不是旋转：以前写成 m_rot(rp)，枢轴非零的节点会被转歪
        m = m_translate(t)
        if roff:
            m = m_mul(m, m_translate(roff))
        if rp:
            m = m_mul(m, m_translate(rp))
        if pre:
            m = m_mul(m, m_rot(pre))
        m = m_mul(m, m_rot(r, order))
        if post:
            m = m_mul(m, m_rot_inv(post))
        if rp:
            m = m_mul(m, m_translate(neg(rp)))
        if soff:
            m = m_mul(m, m_translate(soff))
        if sp:
            m = m_mul(m, m_translate(sp))
        m = m_mul(m, m_scale(s))
        if sp:
            m = m_mul(m, m_translate(neg(sp)))
        return m

    def _build_models(self):
        self.models = {}
        for c in self.objects:
            if c.name != "Model":
                continue
            nid = c.props[0]
            m = Model(nid, _clean_name(c.props[1]), c.props[2])
            m.local = self._local_matrix(c)
            self.models[nid] = m
        for nid, m in self.models.items():
            for pid in self.parents.get(nid, []):
                p = self.models.get(pid)
                if p is not None and m.parent is None:
                    m.parent = p
                    p.children.append(m)
        for m in self.models.values():
            if m.parent is None:
                self._calc_world(m, m_ident())

    def _calc_world(self, m, parent_world):
        m.world = m_mul(parent_world, m.local)
        for c in m.children:
            self._calc_world(c, m.world)

    def _resolve_links(self):
        """child_id -> parent_id lists turned into typed dictionaries.

        **必须按 Connections 的文件顺序遍历**（self.conn_list），不能用
        self.parents —— 后者是按 child 分组、以 child 首次出现的顺序展开的，
        会把「材质 → 网格」的连接顺序打乱。原因见 Scene.__init__ 里的长注释：
        LayerElementMaterial 的索引就是按连接的文件顺序来指的。
        """
        self.geometry_model = {}
        self.model_material = {}        # model_id -> first material id (legacy)
        self.model_materials = {}       # model_id -> [material id, ...] (ordered)
        self.geometry_materials = {}    # geometry_id -> [material id, ...]
        self.geometry_skin = {}
        self.cluster_skin = {}
        self.cluster_bone = {}
        self.texture_materials = {}     # material_id -> [texture id, ...] (ordered)
        self.texture_video = {}         # texture_id -> video_id (embedded name)
        self.texture_slot = {}          # (material_id, texture_id) -> 材质属性名（DiffuseColor / NormalMap …）

        for child, pid in self.conn_list:
            cn = self.byid.get(child)
            if cn is None:
                continue
            pn = self.byid.get(pid)
            if pn is None:
                continue
            if cn.name == "Geometry" and pn.name == "Model":
                self.geometry_model[child] = pid
            elif cn.name == "Material" and pn.name == "Model":
                self.model_material[pid] = child
                self.model_materials.setdefault(pid, []).append(child)
            elif cn.name == "Material" and pn.name == "Geometry":
                self.geometry_materials.setdefault(pid, []).append(child)
            elif cn.name == "Deformer" and pn.name == "Geometry":
                if len(cn.props) > 2 and cn.props[2] == "Skin":
                    self.geometry_skin[pid] = child
            elif cn.name == "Deformer" and pn.name == "Deformer":
                if len(cn.props) > 2 and cn.props[2] == "Cluster":
                    self.cluster_skin[child] = pid
            elif cn.name == "Deformer" and pn.name == "Model":
                # FBX SDK / Blender exporter: C:"OO", <Cluster>, <Node(bone)>
                if len(cn.props) > 2 and cn.props[2] == "Cluster":
                    self.cluster_bone[child] = pid
            elif cn.name == "Model" and pn.name == "Deformer":
                # a few exporters reverse it: C:"OO", <Node(bone)>, <Cluster>
                if len(pn.props) > 2 and pn.props[2] == "Cluster":
                    self.cluster_bone[pid] = child
            elif cn.name == "Texture" and pn.name == "Material":
                self.texture_materials.setdefault(pid, []).append(child)
                self.texture_slot[(pid, child)] = self.conn_prop.get((child, pid), "")
            elif cn.name == "Material" and pn.name == "Texture":
                self.texture_materials.setdefault(cn.props[0], []).append(pid)
            elif cn.name == "Texture" and pn.name == "Video":
                self.texture_video[child] = pid
            elif cn.name == "Video" and pn.name == "Texture":
                self.texture_video[pid] = child


# ----------------------------------------------------------------- pmx ------

    def _geom_matrix(self, node):
        """Geometric transform of a Model node (applied to geometry, not children)."""
        d = self._props70(node)
        t = d.get("GeometricTranslation", [0.0, 0.0, 0.0])
        r = d.get("GeometricRotation", [0.0, 0.0, 0.0])
        s = d.get("GeometricScaling", [1.0, 1.0, 1.0])
        return m_trs([float(x) for x in t[:3]],
                     [float(x) for x in r[:3]],
                     [float(x) for x in s[:3]])

    def _build_textures(self):
        """Collect Texture nodes and their file names.

        FBX stores the image either as an embedded Video (rare here) or, as in
        game exports, as an *external* file referenced by RelativeFilename /
        FileName on the Texture (or its linked Video) node.  We only need the
        basename - the actual file is copied at convert() time if it exists.
        """
        self.textures = {}          # texture_id -> {"name": basename, "rel": path}

        def file_of(node):
            # 文件名可能在 Properties70 里，也可能是 Texture / Video 节点的直接子节点
            # （Maya / Unity 导出的 FBX 7.x 是后者：FileName: "D:/…/body_01.tga"）
            d = self._props70(node)
            for key in ("RelativeFilename", "FileName", "Filename"):
                v = d.get(key)
                if v:
                    return v[0] if isinstance(v, list) else v
            for key in ("RelativeFilename", "FileName", "Filename"):
                for ch in node.find(key):
                    if ch.props and isinstance(ch.props[0], str) and ch.props[0].strip("\x00"):
                        return ch.props[0]
            return None

        for c in self.objects:
            if c.name != "Texture":
                continue
            tid = c.props[0]
            rel = file_of(c)
            if not rel:
                vid = self.texture_video.get(tid)
                vn = self.byid.get(vid) if vid is not None else None
                if vn is not None:
                    rel = file_of(vn)
            if rel:
                rel = rel.split("\x00")[0].replace("\\", "/")
            name = os.path.basename(rel) if rel else _clean_name(c.props[1])
            name = name.split("\x00")[0]
            self.textures[tid] = {"name": name, "rel": rel}

    def _build_morphs(self):
        """Collect FBX blend shapes into morph descriptors.

        FBX chain: Geometry -> BlendShape -> BlendShapeChannel -> Shape.
        Each Shape carries ``Indexes`` (the affected control points) and
        ``Vertices`` (relative position *deltas*, 3 floats per control point,
        in the same order as Indexes).  We verified against this file that the
        stored values are deltas (not absolute positions): the base eyebrow
        vertex sits at y~1.5 while a morph target lists y~-0.004, so subtracting
        nothing yields the intended small displacement.

        A morph is keyed by the owning geometry id so convert() can apply the
        same mesh transform used for the base vertices.
        """
        self.morphs = []          # [{name, geo_id, indexes, deltas}]
        bs_geo = {}               # blendshape_id -> geometry_id
        channels = {}             # blendshape_id -> [channel_id]
        chan_shape = {}           # channel_id -> shape_node_id

        for child, plist in self.parents.items():
            cn = self.byid.get(child)
            if cn is None:
                continue
            cls = (cn.props[2] if (cn.name in ("Deformer", "Geometry")
                                   and len(cn.props) > 2) else "")
            if cn.name == "Deformer" and cls == "BlendShape":
                for pid in plist:
                    if pid in self.geometry_model:   # only real mesh geometries
                        bs_geo[child] = pid
            elif cn.name == "Deformer" and cls == "BlendShapeChannel":
                for pid in plist:
                    pn = self.byid.get(pid)
                    if pn is not None and len(pn.props) > 2 \
                            and pn.props[2] == "BlendShape":
                        channels.setdefault(pid, []).append(child)
            elif cn.name == "Geometry" and cls == "Shape":
                for pid in plist:
                    pn = self.byid.get(pid)
                    if pn is not None and len(pn.props) > 2 \
                            and pn.props[2] == "BlendShapeChannel":
                        chan_shape[pid] = child      # channel_id -> shape_id

        for bsid, gid in bs_geo.items():
            for chid in channels.get(bsid, []):
                shid = chan_shape.get(chid)
                if shid is None:
                    continue
                sh = self.byid.get(shid)
                if sh is None:
                    continue
                idxn = sh.get("Indexes")
                vn = sh.get("Vertices")
                if idxn is None or vn is None:
                    continue
                indexes = idxn.props[0]
                deltas = vn.props[0]
                if not indexes or len(deltas) < 3 * len(indexes):
                    continue
                name = _ns_strip(_clean_name(sh.props[1])) \
                    or _ns_strip(_clean_name(self.byid[chid].props[1]))
                self.morphs.append({"name": name, "geo_id": gid,
                                    "indexes": indexes, "deltas": deltas})


class PmxWriter:
    #: 文本编码：0 = UTF-16LE（MMD 只认这一种），1 = UTF-8
    ENC = 0

    def __init__(self, indices=(2, 1, 2, 2, 1, 1)):
        self.buf = bytearray()
        self.vi, self.ti, self.mi, self.bi, self.moi, self.ri = indices

    # -- primitives
    def i32(self, v):
        self.buf += struct.pack("<i", v)

    def f32(self, v):
        self.buf += struct.pack("<f", v)

    def text(self, s):
        # MMD 不支持 UTF-8 的 PMX（原版提示：MMDではエンコード方式がUTF16の
        # PMXファイルしか読み込めません），所以这里必须写 UTF-16LE。
        b = s.encode("utf-16-le" if self.ENC == 0 else "utf-8")
        self.i32(len(b))
        self.buf += b

    def idx(self, v, size):
        if size == 1:
            self.buf += struct.pack("<b", v)
        elif size == 2:
            self.buf += struct.pack("<h", v)
        else:
            self.i32(v)

    def uidx(self, v, size):
        if size == 1:
            self.buf += struct.pack("<B", v)
        elif size == 2:
            self.buf += struct.pack("<H", v)
        else:
            self.buf += struct.pack("<I", v)

    def header(self, name, name_en, comment, comment_en):
        self.buf += b"PMX "
        self.f32(2.0)
        self.buf += bytes([8])
        # globals: [文本编码, 追加UV, 顶点/贴图/材质/骨骼/表情/刚体索引宽度]
        self.buf += bytes([self.ENC, 0, self.vi, self.ti, self.mi, self.bi,
                           self.moi, self.ri])
        self.text(name)
        self.text(name_en)
        self.text(comment)
        self.text(comment_en)


def pick_index_size(count, signed=True):
    if signed:
        if count <= 127:
            return 1
        if count <= 32767:
            return 2
        return 4
    if count <= 255:
        return 1
    if count <= 65535:
        return 2
    return 4


# ------------------------------------------------------------- converter ----

def _cp_index(pvi, i):
    """FBX PolygonVertexIndex -> actual control point index (last index is negative)."""
    v = pvi[i]
    return ~v if v < 0 else v


def sample_uv(node, pvi):
    """Return a per-corner list of (u, v) indexed by polygon-vertex position."""
    if node is None or pvi is None:
        return None
    uv = node.get("UV")
    if uv is None:
        return None
    table = uv.props[0]
    idn = node.get("UVIndex")
    mir = node.get("MappingInformationType")
    rir = node.get("ReferenceInformationType")
    mir_s = mir.props[0] if mir is not None else ""
    rir_s = rir.props[0] if rir is not None else ""
    n = len(pvi)
    if mir_s == "ByPolygonVertex" and rir_s == "IndexToDirect" and idn is not None:
        ids = idn.props[0]
        return [(table[i * 2], table[i * 2 + 1]) for i in ids]
    if mir_s == "ByPolygonVertex" and (rir_s == "Direct" or idn is None):
        return [(table[i * 2], table[i * 2 + 1]) for i in range(n)]
    # ByControlPoint / ByVertice: the per-corner UV comes from the control point of that corner
    if idn is not None and rir_s == "IndexToDirect":
        ids = idn.props[0]
        return [(table[ids[_cp_index(pvi, i)] * 2], table[ids[_cp_index(pvi, i)] * 2 + 1])
                for i in range(n)]
    return [(table[_cp_index(pvi, i) * 2], table[_cp_index(pvi, i) * 2 + 1])
            for i in range(n)]


def sample_normals(node, pvi):
    """Return a per-corner list of (nx, ny, nz) indexed by polygon-vertex position."""
    if node is None or pvi is None:
        return None
    nrm = node.get("Normals")
    if nrm is None:
        return None
    table = nrm.props[0]
    idn = node.get("NormalsIndex")
    mir = node.get("MappingInformationType")
    rir = node.get("ReferenceInformationType")
    mir_s = mir.props[0] if mir is not None else ""
    rir_s = rir.props[0] if rir is not None else ""
    n = len(pvi)
    if mir_s == "ByPolygonVertex" and idn is not None:
        ids = idn.props[0]
        return [(table[i * 3], table[i * 3 + 1], table[i * 3 + 2]) for i in ids]
    if mir_s == "ByPolygonVertex" and rir_s == "Direct":
        return [(table[i * 3], table[i * 3 + 1], table[i * 3 + 2]) for i in range(n)]
    if idn is not None and rir_s == "IndexToDirect":
        ids = idn.props[0]
        return [(table[ids[_cp_index(pvi, i)] * 3], table[ids[_cp_index(pvi, i)] * 3 + 1],
                 table[ids[_cp_index(pvi, i)] * 3 + 2]) for i in range(n)]
    return [(table[_cp_index(pvi, i) * 3], table[_cp_index(pvi, i) * 3 + 1],
             table[_cp_index(pvi, i) * 3 + 2]) for i in range(n)]


def triangulate(pvi):
    """FBX polygon list -> list of ((control_point, corner_position), x3).

    The second element matters: LayerElementUV / LayerElementNormal use
    ByPolygonVertex mapping, so their lookup index is the position inside the
    PolygonVertexIndex array, not the control-point index.
    """
    tris = []
    cur = []
    for pos, v in enumerate(pvi):
        idx = ~v if v < 0 else v
        cur.append((idx, pos))
        if v < 0:
            for i in range(1, len(cur) - 1):
                tris.append((cur[0], cur[i], cur[i + 1]))
            cur = []
    return tris


def polygon_starts(pvi):
    """Index into the pvi array where each polygon begins (for per-poly data)."""
    starts = []
    start = 0
    for i, v in enumerate(pvi):
        if v < 0:
            starts.append(start)
            start = i + 1
    return starts


def _mesh_materials(g, model, scene):
    """Ordered list of material node ids used by this geometry/model.

    FBX stores the material assignment either as Connections from the material
    to the model (or to the geometry), and optionally refines it per-polygon via
    a LayerElementMaterial.  The per-polygon indices reference this ordered list.
    """
    mats = list(scene.model_materials.get(model.id, []))
    if not mats:
        mats = list(scene.geometry_materials.get(g.props[0], []))
    if not mats:
        m = scene.model_material.get(model.id)
        if m is not None:
            mats = [m]
    if not mats:
        mats = [None]
    return mats


def _poly_material_index(g, pvi, nmats):
    """Per-triangle material index, or None if there is no LayerElementMaterial."""
    le = g.get("LayerElementMaterial")
    if le is None:
        return None
    midx = le.get("Materials")
    if midx is None:
        return None
    ids = midx.props[0]
    mir = le.get("MappingInformationType")
    rir = le.get("ReferenceInformationType")
    mir_s = mir.props[0] if mir is not None else ""
    rir_s = rir.props[0] if rir is not None else ""
    poly_lens = []
    cur = 0
    for v in pvi:
        cur += 1
        if v < 0:
            poly_lens.append(cur)
            cur = 0
    tri_mat = []
    if mir_s == "ByPolygon":
        for pi, pl in enumerate(poly_lens):
            m = ids[pi] if pi < len(ids) else 0
            for _ in range(max(0, pl - 2)):
                tri_mat.append(m)
    elif mir_s == "ByPolygonVertex":
        corner = 0
        for pl in poly_lens:
            m = ids[corner] if corner < len(ids) else 0
            for _ in range(max(0, pl - 2)):
                tri_mat.append(m)
            corner += pl
    else:
        return None
    nmats = max(1, nmats)
    return [max(0, min(nmats - 1, m)) for m in tri_mat]


def _find_file(root, basename, limit=60000):
    """Bounded recursive search for a file by basename under root (returns path).

    用 ``os.path.join(d, e.name)`` 而不是 ``e.path``：当 d 是超长路径时，
    longpath 会给传给 scandir 的路径加 ``\\\\?\\`` 前缀，而 DirEntry.path
    会把这个前缀带出来，泄漏到调用方。自己用 name 拼就不会。
    """
    count = 0
    stack = [root]
    while stack:
        d = stack.pop()
        try:
            entries = os.scandir(d)
        except OSError:
            continue
        dirs = []
        for e in entries:
            count += 1
            if count > limit:
                return None
            if e.is_dir():
                dirs.append(os.path.join(d, e.name))
            elif e.name == basename:
                return os.path.join(d, e.name)
        stack.extend(dirs)
    return None


# ----------------------------------------------------------------- alpha ----
# Remove the alpha channel from a texture image.  Returns one of:
#   "stripped"  - had alpha, now written as opaque RGB
#   "no_alpha"  - no alpha (copied verbatim)
#   False       - decode/encode failed; the caller copies the file as-is
# Pillow is preferred; a tiny built-in PNG/TGA reader is the fallback so the
# feature works even when Pillow is not installed (the GUI runs on plain Python).

def _decode_png_rgba(src):
    """纯标准库 PNG 解码 → (width, height, RGBA bytes)，失败返回 None。

    只处理 8bit、非隔行的 0/2/3/4/6 型（MMD 用得到的贴图都在这个范围内）。
    alpha 统计要走这里，是因为 GUI 可能跑在没有 Pillow 的解释器上。
    """
    import zlib
    try:
        with open(src, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    plte = None
    trns = None
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        cdata = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR" and len(cdata) >= 13:
            width, height, bit_depth, color_type, _c, _f, interlace = \
                struct.unpack(">IIBBBBB", cdata[:13])
        elif typ == b"IDAT":
            idat += cdata
        elif typ == b"PLTE":
            plte = cdata
        elif typ == b"tRNS":
            trns = cdata
        pos += 12 + ln
    if not width or not height or interlace or bit_depth != 8 \
            or color_type not in (0, 2, 3, 4, 6):
        return None
    try:
        raw = zlib.decompress(bytes(idat))
    except Exception:
        return None
    ch_in = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    stride = width * ch_in
    if len(raw) < height * (stride + 1):
        return None
    prev = bytearray(stride)
    rows = []
    p = 0
    for _ in range(height):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        _png_unfilter(ftype, line, prev, ch_in)
        rows.append(line)
        prev = line
    out = bytearray(width * height * 4)
    for y in range(height):
        line = rows[y]
        base = y * width * 4
        if color_type == 6:
            out[base:base + width * 4] = line
        elif color_type == 2:
            for x in range(width):
                out[base + x * 4:base + x * 4 + 3] = line[x * 3:x * 3 + 3]
                out[base + x * 4 + 3] = 255
        elif color_type == 4:
            for x in range(width):
                g = line[x * 2]
                out[base + x * 4] = g
                out[base + x * 4 + 1] = g
                out[base + x * 4 + 2] = g
                out[base + x * 4 + 3] = line[x * 2 + 1]
        elif color_type == 0:
            for x in range(width):
                g = line[x]
                out[base + x * 4] = g
                out[base + x * 4 + 1] = g
                out[base + x * 4 + 2] = g
                out[base + x * 4 + 3] = 255
        else:                       # 3 = palette
            if plte is None:
                return None
            alpha_by = {}
            if trns:
                for i, a in enumerate(trns):
                    alpha_by[i] = a
            for x in range(width):
                pi = line[x]
                o = pi * 3
                out[base + x * 4] = plte[o] if o + 2 < len(plte) else 0
                out[base + x * 4 + 1] = plte[o + 1] if o + 2 < len(plte) else 0
                out[base + x * 4 + 2] = plte[o + 2] if o + 2 < len(plte) else 0
                out[base + x * 4 + 3] = alpha_by.get(pi, 255)
    return width, height, bytes(out)


def _load_alpha(src):
    """→ ((宽, 高, alpha), png)：alpha 是每像素 1 字节的 alpha 通道（转成 RGBA 后的那个通道；
    没有 alpha 通道的图为 None = 全不透明），拿不到位图时整个三元组为 None；
    png 是用同一次解码算好的 vrmio.png_alpha_stats 结果（只有 PNG 能这样算），算不了为 NotImplemented。

    只取 alpha 通道：4K 贴图的 RGBA 有 64 MB，alpha 只有 16 MB（几张图会同时解，见 _copy_textures）。
    """
    try:
        from PIL import Image
    except ImportError:
        got = _decode_png_rgba(src)
        return (got and (got[0], got[1], bytes(got[2][3::4]))), NotImplemented
    try:
        im = Image.open(src)
        if im.mode in ("RGBA", "LA", "PA"):
            alpha = im.getchannel("A").tobytes()
        elif "transparency" in im.info:
            alpha = im.convert("RGBA").getchannel("A").tobytes()
        else:
            alpha = None                    # 转成 RGBA 后 alpha 全是 255
        got = (im.size[0], im.size[1], alpha)
    except Exception:  # noqa: BLE001
        return None, NotImplemented
    png = NotImplemented
    if im.format == "PNG":
        try:
            png = vrmio.png_alpha_stats_image(im)
        except Exception:  # noqa: BLE001  交给 PMX → VRM 自己再解
            pass
    return got, png


def _has_any_transparency(w, h, alpha):
    """整张图有没有非 255 的 alpha（count 在 C 里跑，不逐像素循环）。"""
    return alpha is not None and alpha.count(255) != len(alpha)


def _alpha_stats_whole(w, h, alpha):
    """整图 alpha 统计（4x4 网格降采样）→ (全透占比, 实心占比)。

    每行起点错开一点：固定步长遇到周期图案会一直踩到同一类像素，把统计系统性
    带偏（实测「周期 10、90% 全透」的合成图被算成 80%）。C# 那边没这个错位，
    但只影响取样点不影响判据，故选得更稳的一版。
    """
    ws = max(1, w // 64)
    hs = max(1, h // 64)
    n = t = o = 0
    row = 0
    for y in range(0, h, hs):
        base = y * w
        x0 = (row * 7) % ws if ws > 1 else 0
        for x in range(x0, w, ws):
            a = alpha[base + x]
            n += 1
            if a < ALPHA_TRANSPARENT_BELOW:
                t += 1
            elif a >= ALPHA_OPAQUE_ABOVE:
                o += 1
        row += 1
    if n == 0:
        return None
    return t / n, o / n


def _alpha_stats_uv(w, h, alpha, uvs):
    """按「材质实际用到的 UV 角点」定点采样 → (全透占比, 实心占比, 采样数)。

    uvs 里已经是 MMD 口径 (u, 1-v)（v=0 在最上一行），寻址按 MMD 的 wrap 来。
    """
    if not uvs:
        return None
    step = max(1, len(uvs) // ALPHA_MAX_SAMPLES)
    t = o = n = 0
    for i in range(0, len(uvs), step):
        u, v = uvs[i]
        x = int(math.floor(u * w)) % w if w else 0
        y = int(math.floor(v * h)) % h if h else 0
        a = alpha[y * w + x]
        n += 1
        if a < ALPHA_TRANSPARENT_BELOW:
            t += 1
        elif a >= ALPHA_OPAQUE_ABOVE:
            o += 1
    if n == 0:
        return None
    return t / n, o / n, n


def decide_alpha(src, uvs=None, got=None):
    """判定一张贴图的 alpha 是不是「真透明」。返回 (verdict, detail)。

    判据（两条统计**都要**满足才算「不是透明度」）：
      · 用到的 UV 区域：全透占比 >= 0.75 且实心占比 <= 0.05；
      · 整张图：同样两条。
    直觉：真透明贴图一定有「实心的衣服本体」和「镂空的花边」两部分
    （实测 Anastasya 丝袜 71% / 28%）。**整块区域几乎全透、且完全没有实心**
    只能是「这里的 alpha 根本不是管透明的」（被当成自发光 / 高光遮罩写进去了）。

    为什么两边互相印证：只用「用到的 UV」时，网格没有 UV 层（UV 全是 0）就会
    退化成「只看一个像素」，样本量 1 也敢下结论；只用整图时，图集式贴图的
    大片 padding 又会把统计带偏。要求两边都成立就把这两种情况挡在门外 ——
    代价是有时会少去一张（偏保守），而这正是要的方向：判不准就保留，
    用户还能手动选「全部去除」。
    """
    got = got or _load_alpha(src)[0]                # 调用方已经解过就不再解一遍
    if got is None:
        return ALPHA_KEEP, "cannot sample alpha"
    w, h, alpha = got
    if not _has_any_transparency(w, h, alpha):
        return ALPHA_KEEP, "no transparency at all"
    whole = _alpha_stats_whole(w, h, alpha)
    used = _alpha_stats_uv(w, h, alpha, uvs) if uvs else None
    if used is None or whole is None:
        if whole is None:
            return ALPHA_KEEP, "cannot sample alpha"
        # 没有 UV 时更保守：要求「几乎全空」才敢去
        verdict = (ALPHA_STRIP
                   if (whole[0] >= ALPHA_NOUV_EMPTY and whole[1] <= ALPHA_NOUV_SOLID)
                   else ALPHA_KEEP)
        detail = ("whole image transparent=%.1f%% opaque=%.1f%%"
                  % (whole[0] * 100, whole[1] * 100))
        return verdict, detail
    detail = ("uv samples=%d transparent=%.1f%% opaque=%.1f%% | whole "
              "transparent=%.1f%% opaque=%.1f%%"
              % (used[2], used[0] * 100, used[1] * 100,
                 whole[0] * 100, whole[1] * 100))
    used_junk = (used[0] >= ALPHA_MOSTLY_EMPTY and used[1] <= ALPHA_ALMOST_NO_SOLID)
    whole_junk = (whole[0] >= ALPHA_MOSTLY_EMPTY and whole[1] <= ALPHA_ALMOST_NO_SOLID)
    return (ALPHA_STRIP if (used_junk and whole_junk) else ALPHA_KEEP), detail


def _opaque_copy_name(basename):
    stem, ext = os.path.splitext(basename)
    return stem + ALPHA_NOALPHA_SUFFIX + ext


def _strip_alpha(src, dst):
    """Try Pillow first; fall back to the pure-stdlib reader."""
    try:
        from PIL import Image
        im = Image.open(src)
        has_alpha = (im.mode in ("RGBA", "LA", "PA")
                     or (im.mode == "P" and "transparency" in im.info))
        if has_alpha:
            im.convert("RGB").save(dst)
            return "stripped"
        shutil.copyfile(src, dst)
        return "no_alpha"
    except ImportError:
        pass
    except Exception:
        return False
    return _strip_alpha_pure(src, dst)


def _strip_alpha_pure(src, dst):
    """Pure-stdlib alpha strip for PNG and TGA (the formats MMD actually uses)."""
    try:
        with open(src, "rb") as f:
            head = f.read(18)
    except OSError:
        return False
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return _strip_png_alpha(src, dst)
    # TGA has no magic; recognise it by a plausible header.
    if len(head) >= 18 and head[2] in (1, 2, 3, 9, 10, 11) \
            and head[16] in (16, 24, 32):
        return _strip_tga_alpha(src, dst)
    # Unknown / unsupported format: keep the original, just relocate it.
    try:
        shutil.copyfile(src, dst)
        return "no_alpha"
    except OSError:
        return False


def _png_chunk(typ, data):
    import zlib
    return (struct.pack(">I", len(data)) + typ + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))


def _paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _png_unfilter(ftype, line, prev, bpp):
    """Undo a PNG scanline filter in place (assumes 8-bit)."""
    if ftype == 0:
        return
    n = len(line)
    for i in range(n):
        a = line[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ftype == 1:
            line[i] = (line[i] + a) & 0xff
        elif ftype == 2:
            line[i] = (line[i] + b) & 0xff
        elif ftype == 3:
            line[i] = (line[i] + ((a + b) >> 1)) & 0xff
        elif ftype == 4:
            line[i] = (line[i] + _paeth(a, b, c)) & 0xff


def _strip_png_alpha(src, dst):
    import zlib
    try:
        with open(src, "rb") as f:
            data = f.read()
    except OSError:
        return False
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    pos = 8
    width = height = bit_depth = color_type = interlace = None
    idat = bytearray()
    plte = None
    other = []
    while pos + 8 <= len(data):
        ln = struct.unpack(">I", data[pos:pos + 4])[0]
        typ = data[pos + 4:pos + 8]
        cdata = data[pos + 8:pos + 8 + ln]
        if typ == b"IHDR" and len(cdata) >= 13:
            width, height, bit_depth, color_type, _comp, _filt, interlace = \
                struct.unpack(">IIBBBBB", cdata[:13])
        elif typ == b"IDAT":
            idat += cdata
        elif typ == b"PLTE":
            plte = cdata
        elif typ == b"tRNS":
            pass  # drop transparency chunk
        elif typ == b"IEND":
            pass  # we append our own IEND last
        else:
            other.append((typ, cdata))
        pos += 12 + ln
    if width is None or interlace or bit_depth != 8 \
            or color_type not in (0, 2, 3, 4, 6):
        try:
            shutil.copyfile(src, dst)
        except OSError:
            return False
        return "no_alpha"
    raw = zlib.decompress(bytes(idat))
    ch_in = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    stride = width * ch_in
    prev = bytearray(stride)
    out = bytearray()
    p = 0
    for _ in range(height):
        ftype = raw[p]
        p += 1
        line = bytearray(raw[p:p + stride])
        p += stride
        _png_unfilter(ftype, line, prev, ch_in)
        out += line
        prev = line
    if color_type == 6:
        new_ct, ch_out = 2, 3
        new = bytearray()
        for y in range(height):
            base = y * stride
            for x in range(width):
                i = base + x * 4
                new += bytes((out[i], out[i + 1], out[i + 2]))
    elif color_type == 4:
        new_ct, ch_out = 0, 1
        new = bytearray()
        for y in range(height):
            base = y * stride
            for x in range(width):
                new += bytes((out[base + x * 2],))
    elif color_type == 3:
        if plte is None:
            try:
                shutil.copyfile(src, dst)
            except OSError:
                return False
            return "no_alpha"
        new_ct, ch_out = 2, 3
        pal = {i: (plte[i * 3], plte[i * 3 + 1], plte[i * 3 + 2])
               for i in range(len(plte) // 3)}
        new = bytearray()
        for y in range(height):
            base = y * stride
            for x in range(width):
                r, g, b = pal.get(out[base + x], (0, 0, 0))
                new += bytes((r, g, b))
    else:  # 0 or 2: already opaque
        try:
            shutil.copyfile(src, dst)
        except OSError:
            return False
        return "no_alpha"
    nstride = width * ch_out
    new_raw = bytearray()
    for y in range(height):
        new_raw.append(0)
        new_raw += new[y * nstride:(y + 1) * nstride]
    comp = zlib.compress(bytes(new_raw), 9)
    buf = bytearray(b"\x89PNG\r\n\x1a\n")
    buf += _png_chunk(b"IHDR",
                      struct.pack(">IIBBBBB", width, height, 8, new_ct, 0, 0, 0))
    for typ, cdata in other:
        buf += _png_chunk(typ, cdata)
    buf += _png_chunk(b"IDAT", comp)
    buf += _png_chunk(b"IEND", b"")
    try:
        with open(dst, "wb") as f:
            f.write(bytes(buf))
    except OSError:
        return False
    return "stripped"


def _tga_decode_rle(pix, width, height, depth):
    bpp = depth // 8
    out = bytearray()
    count = width * height
    i, n = 0, len(pix)
    while len(out) < count * bpp and i < n:
        h = pix[i]
        i += 1
        run = (h & 0x7f) + 1
        if h & 0x80:
            chunk = pix[i:i + bpp]
            i += bpp
            for _ in range(run):
                out += chunk
        else:
            for _ in range(run):
                out += pix[i:i + bpp]
                i += bpp
    return out


def _strip_tga_alpha(src, dst):
    import struct as _s
    try:
        with open(src, "rb") as f:
            data = f.read()
    except OSError:
        return False
    if len(data) < 18:
        return False
    id_len = data[0]
    cmap_type = data[1]
    img_type = data[2]
    if img_type not in (1, 2, 3, 9, 10, 11) or data[16] not in (16, 24, 32):
        try:
            shutil.copyfile(src, dst)
        except OSError:
            return False
        return "no_alpha"
    width, height = _s.unpack("<HH", data[12:16])
    depth = data[16]
    desc = data[17]
    if width <= 0 or height <= 0 or width > 8192 or height > 8192:
        try:
            shutil.copyfile(src, dst)
        except OSError:
            return False
        return "no_alpha"
    cmap_len = _s.unpack("<H", data[3:5])[0]
    cmap_entry = data[7]
    off = 18 + id_len
    if cmap_type:
        off += cmap_len * (cmap_entry // 8)
    pix = bytearray(data[off:])
    if img_type in (9, 10, 11):
        pix = _tga_decode_rle(pix, width, height, depth)
    bpp_in = depth // 8
    out = bytearray()
    n = width * height
    had_alpha = depth in (16, 32)
    for i in range(n):
        base = i * bpp_in
        if depth == 32:
            b, g, r, _a = pix[base], pix[base + 1], pix[base + 2], pix[base + 3]
        elif depth == 24:
            b, g, r = pix[base], pix[base + 1], pix[base + 2]
        else:  # 16-bit 1555/555 -> drop the alpha bit, scale to 8-bit
            v = pix[base] | (pix[base + 1] << 8)
            r = (v >> 10) & 0x1f
            g = (v >> 5) & 0x1f
            b = v & 0x1f
            r = (r * 255 + 15) // 31
            g = (g * 255 + 15) // 31
            b = (b * 255 + 15) // 31
        out += bytes((b, g, r))
    hdr = bytearray(18)
    hdr[2] = 2  # uncompressed truecolour
    hdr[12:14] = _s.pack("<H", width)
    hdr[14:16] = _s.pack("<H", height)
    hdr[16] = 24
    hdr[17] = desc  # preserve top/bottom origin bit
    try:
        with open(dst, "wb") as f:
            f.write(bytes(hdr))
            f.write(bytes(out))
    except OSError:
        return False
    return "stripped" if had_alpha else "no_alpha"


def _alternate_groups(records):
    """找「互为替换」的网格：顶点数、三角面数相同，包围盒几乎重合（同一个位置的几种形态）。

    Unity 资源常这样做手势 / 表情：Query 酱的 Finger_Nomal / Finger_Pa / Finger_Gu、
    Head_01_Normal / Head_02_MouthOpen，Unity 里一次只显示一个，FBX 里全都在。
    返回 [(主网格, [替换网格 …])]；主网格优先选名字里带 normal / default / base 的，否则第一个。
    """
    groups, used = [], set()
    for i, a in enumerate(records):
        if i in used or a["nv"] < 20:
            continue
        grp = [i]
        for j in range(i + 1, len(records)):
            b = records[j]
            if j in used or (b["nv"], b["nt"]) != (a["nv"], a["nt"]):
                continue
            ext = max(max(a["hi"][k] - a["lo"][k] for k in range(3)), 1e-6)
            if all(abs(a["lo"][k] - b["lo"][k]) <= 0.05 * ext and abs(a["hi"][k] - b["hi"][k]) <= 0.05 * ext
                   for k in range(3)):
                grp.append(j)
        if len(grp) < 2:
            continue
        used.update(grp)
        prim = next((k for k in grp if any(w in records[k]["name"].lower()
                                           for w in ("normal", "nomal", "default", "base", "neutral"))), grp[0])
        groups.append((records[prim], [records[k] for k in grp if k != prim]))
    return groups


_NOT_DIFFUSE = ("normal", "_nrm", "_nor", "_norm", "bump", "_spec", "specular", "_rough", "_metal",
                "_ao", "occlusion", "emiss", "_env", "env2", "matcap", "_mask", "_ramp")


def _pick_diffuse(scene, mat_id, tids):
    """一个材质挂了好几张贴图时选哪张当底色：

    1. 连接属性是 DiffuseColor / Diffuse / BaseColor 的；
    2. 贴图名里有 diffuse / albedo / basecolor 的；
    3. 名字不像法线 / 高光 / 环境图（_NRM、_SPEC、ENV …）的第一张；
    4. 都不行就第一张。
    """
    texs = [(tid, scene.textures.get(tid)) for tid in tids]
    texs = [(tid, t) for tid, t in texs if t]
    if not texs:
        return None
    slot = getattr(scene, "texture_slot", {})
    for tid, t in texs:
        if slot.get((mat_id, tid), "").lower() in ("diffusecolor", "diffuse", "basecolor", "maps|base_color_map",
                                                   "maya|diffusetexture", "3dsmax|parameters|base_color_map"):
            return t["name"]
    for tid, t in texs:
        if any(k in t["name"].lower() for k in ("diffuse", "albedo", "basecolor", "base_color")):
            return t["name"]
    for tid, t in texs:
        n = t["name"].lower()
        if not any(k in n for k in _NOT_DIFFUSE) and not slot.get((mat_id, tid), "").lower().startswith(
                ("normal", "bump", "specular", "reflection", "emissive", "transparent")):
            return t["name"]
    return texs[0][1]["name"]


def _copy_textures(tex_list, fbx_path, out_dir, verbose, alpha_mode=ALPHA_AUTO,
                   alpha_uvs=None, known=None, fallback=None, threads=1):
    """Locate each texture image on disk and copy it into ``out_dir/textures/``.

    Returns ``(out_names, stats)``:

    * ``out_names`` - ``{源文件名: 要在 PMX 里引用的文件名}``。走了 alpha 处理的
      贴图在这里被换成 ``<名>_noalpha.png``（**原始图片一律不动**）。
    * ``stats``     - 计数与逐张的判定说明，供日志 / 界面显示；``png_alpha`` 是
      ``{原样拷贝的 PNG 的 normcase 绝对路径: vrmio.png_alpha_stats 结果}``，
      判断透明时顺便算好，接着转 VRM 时不用再解一遍（pmx2vrm 的 tex_alpha）。

    Files referenced by the FBX but missing locally (the FBX usually stores an
    absolute game path like ``G:\\...``) are simply skipped - the PMX still
    references them so the user can drop the images into ``textures/`` later.

    threads：同时解几张图（Pillow 在 C 里解码、不占 GIL）。
    """
    stats = {"mode": alpha_mode, "copied": 0, "weighed": 0,
             "stripped": 0, "kept": 0, "opaque": 0, "failed": 0,
             "decisions": [], "missing": 0, "out_names": {}, "png_alpha": {}}
    if not tex_list:
        return stats["out_names"], stats
    import shutil
    fbx_dir = os.path.dirname(os.path.abspath(fbx_path))
    parent = os.path.dirname(fbx_dir)
    search_dirs = [fbx_dir,
                   os.path.join(fbx_dir, "textures"),
                   os.path.join(fbx_dir, "tex"),
                   parent]
    found = {}
    for bn, p in (known or {}).items():
        if bn in tex_list and os.path.isfile(p):
            found[bn] = p
    for bn in tex_list:
        if bn in found:
            continue
        for d in search_dirs:
            if not d:
                continue
            p = os.path.join(d, bn)
            if os.path.isfile(p):
                found[bn] = p
                break
        if bn in found:
            continue
        for d in (fbx_dir, parent):
            if not d or bn in found:
                continue
            hit = _find_file(d, bn)
            if hit:
                found[bn] = hit
                break
    # 还没找到：同名换扩展名（FBX 里写的是 face_00.psd，手上的是 face_00.tga），再查 Unity 材质
    for bn in tex_list:
        if bn in found:
            continue
        stem = os.path.splitext(bn)[0]
        for ext in (".png", ".tga", ".jpg", ".jpeg", ".bmp", ".dds"):
            alt = stem + ext
            if alt.lower() == bn.lower():
                continue
            for d in search_dirs:
                if d and os.path.isfile(os.path.join(d, alt)):
                    found[bn] = os.path.join(d, alt)
                    break
            if bn not in found:
                for d in (fbx_dir, parent):
                    hit = _find_file(d, alt) if d else None
                    if hit:
                        found[bn] = hit
                        break
            if bn in found:
                break
        if bn not in found and fallback is not None:
            p = fallback(bn)
            if p and os.path.isfile(p):
                found[bn] = p
    stats["missing"] = len(tex_list) - len(found)

    if found:
        tdir = os.path.join(out_dir, "textures")
        os.makedirs(tdir, exist_ok=True)
        items = list(found.items())

        def weigh(item):
            """先看看这张图到底有没有 alpha 可处理 → (有透明?, 判定, 说明, png_alpha_stats 结果)。
            在线程里跑；解出来的 alpha 用完就丢，不会几张图的一起攒在内存里。"""
            bn, src = item
            got, png = _load_alpha(src)
            if got is None or not _has_any_transparency(*got):
                return False, None, None, png
            if alpha_mode == ALPHA_STRIP:
                return True, ALPHA_STRIP, "StripAll", png
            return (True,) + decide_alpha(src, (alpha_uvs or {}).get(bn), got) + (png,)

        if alpha_mode == ALPHA_KEEP:
            weighed = [None] * len(items)
        elif threads > 1 and len(items) > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(min(threads, len(items))) as ex:
                weighed = list(ex.map(weigh, items))
        else:
            weighed = [weigh(it) for it in items]
        for (bn, src), w in zip(items, weighed):
            real = os.path.basename(src)        # 换了扩展名 / 来自 Unity 材质时用实际文件名
            ref = real
            out_bn = real
            wrote_copy = False          # 副本已经写过盘了，别再被原件覆盖回去
            png = NotImplemented
            if w is not None:
                has_a, verdict, detail, png = w
                if not has_a:
                    stats["opaque"] += 1
                elif alpha_mode != ALPHA_STRIP:
                    stats["weighed"] += 1
                if has_a:
                    stats["decisions"].append((bn, verdict, detail))
                    if verdict != ALPHA_STRIP:
                        stats["kept"] += 1
                    else:
                        out_bn = _opaque_copy_name(real)
                        dst = os.path.join(tdir, out_bn)
                        res = _strip_alpha(src, dst)
                        if res == "stripped":
                            stats["stripped"] += 1
                            ref = out_bn
                            wrote_copy = True
                        elif res is False:
                            stats["failed"] += 1
                            out_bn = real
                        else:
                            # 写不出副本（格式不支持等）：退回原图
                            out_bn = real
            dst = os.path.join(tdir, out_bn)
            if wrote_copy:
                stats["copied"] += 1
                stats["out_names"][bn] = ref
                continue
            try:
                if not (os.path.abspath(src) == os.path.abspath(dst)):
                    shutil.copyfile(src, dst)
                stats["copied"] += 1
            except OSError:
                continue
            stats["out_names"][bn] = ref
            if png is not NotImplemented:       # 原样拷贝：统计结果对拷贝出来的文件也成立
                stats["png_alpha"][os.path.normcase(os.path.abspath(dst))] = png

    if verbose and tex_list:
        if stats["copied"]:
            print("  textures: copied %d / %d image file(s) into textures/"
                  % (stats["copied"], len(tex_list)))
        if alpha_mode == ALPHA_KEEP:
            pass
        elif alpha_mode == ALPHA_STRIP:
            print("  textures: 去除透明通道 %d 张（已成不透明 RGB）"
                  % stats["stripped"])
        else:
            print("  textures: alpha 自动判定 —— 去掉 %d 张 / 保留 %d 张"
                  "（原本就不透明的 %d 张）"
                  % (stats["stripped"], stats["kept"], stats["opaque"]))
            if stats["failed"]:
                print("  textures: %d 张去 alpha 失败，已回退用原图" % stats["failed"])
        if stats["missing"]:
            print("  textures: %d referenced but not found on disk "
                  "(drop them into textures/ later)" % stats["missing"])
    return stats["out_names"], stats


def _mesh_has_no_material(g, model, scene, mats=None):
    """该网格是不是「一个材质都没挂」。

    游戏（Unity/HoYo 系）导出时常带一张名为 `EffectMesh` 的效果片：它没有
    材质节点、没有贴图，UV 铺满整张图集，几何是一大片横跨全身的薄板。按普通
    网格导出去，在 MMD 里就是一块不透明的灰白大板子盖在裙子上（贴图看着像被
    "翻错"了，其实是底色被压住了）。C# 插件侧的日志同样把它标成 `材质 '?'`
    / 无贴图，产出的 PMX 里也不含这张网格，所以这里直接跳过。
    """
    if mats is None:
        mats = _mesh_materials(g, model, scene)
    if not mats:
        return True
    return all(m is None for m in mats)


def convert(scene, out_path, scale_mode="mmd", flip_z=True, verbose=True, name=None,
            center=True, morphs=True, max_morphs=None, remove_alpha=None,
            alpha_mode=None, auto_facing=True, face_180=False,
            skip_untextured=True, threads=4):
    # threads：判断贴图透明时同时解几张图（见 _copy_textures）
    # ⚠ remove_alpha 的默认必须是 None（不是 False）：False 会被 alpha_mode_of
    #   当成「用户显式要 keep」，把「不指定就用 auto」的默认遮蔽掉。
    alpha_mode = alpha_mode_of(alpha_mode, remove_alpha)
    # 确保输出目录存在（CLI 直接调用时尤其容易漏建）
    _odir = os.path.dirname(os.path.abspath(out_path))
    try:
        os.makedirs(_odir, exist_ok=True)
    except OSError as e:
        raise OSError("无法创建输出目录 %s：%s" % (_odir, e))

    # ---- gather meshes in a stable order
    geo_ids = [nid for nid, n in scene.byid.items()
               if n.name == "Geometry" and nid in scene.geometry_model]
    geo_ids.sort(key=lambda i: scene.byid[i].props[1])

    # ---- bone order = model order for LimbNodes (parents first)
    bones = [m for m in scene.models.values() if m.cls == "LimbNode"]
    bones.sort(key=lambda m: _bone_depth(m))
    if not bones:
        # No skeletal nodes: synthesise a root so skinned/weighted vertices
        # still have a valid bone to bind to (otherwise the PMX is invalid).
        root = Model(0, "Root", "LimbNode")
        bones = [root]
    bone_index = {m.id: i for i, m in enumerate(bones)}

    # ---- 朝向：要不要在 Z 取反之外再转 180°（判据见 detect_forward_turn）
    if auto_facing:
        turn, facing_reason = detect_forward_turn(scene, bones)
        if turn is None:
            turn = bool(face_180)
            facing_reason += " -> manual setting used (%s)" % (
                "180" if turn else "none")
    else:
        turn = bool(face_180)
        facing_reason = "auto-detect off -> manual setting (%s)" % (
            "180" if turn else "none")
    if verbose:
        print("  facing: %s -> %s" % (facing_reason, "turn 180" if turn else "keep"))

    # ---- per-mesh data
    mesh_data = []
    skipped_untextured = []       # 无材质网格（效果片）——默认丢掉
    for gid in geo_ids:
        g = scene.byid[gid]
        mid = scene.geometry_model[gid]
        model = scene.models[mid]
        verts = g.get("Vertices").props[0]
        pvi = g.get("PolygonVertexIndex").props[0]
        nverts = len(verts) // 3
        tris = triangulate(pvi)
        mats = _mesh_materials(g, model, scene)
        if skip_untextured and _mesh_has_no_material(g, model, scene, mats):
            skipped_untextured.append((model.name, len(tris)))
            if verbose:
                print("  skip    %-22s tris=%-6d (no material / effect sheet)"
                      % (model.name, len(tris)))
            continue
        uv = sample_uv(g.get("LayerElementUV"), pvi)
        nrm = sample_normals(g.get("LayerElementNormal"), pvi)
        poly_mat = _poly_material_index(g, pvi, len(mats))
        mesh_data.append({
            "geo": g, "model": model, "verts": verts, "tris": tris,
            "uv": uv, "nrm": nrm, "nverts": nverts, "pvi": pvi,
            "mats": mats, "poly_mat": poly_mat,
            "geom": scene._geom_matrix(scene.byid.get(mid)),
        })

    # ---- scale: MMD units put a character at roughly 20 units tall
    if scale_mode in ("mmd", "auto", None, ""):
        # 用第一个顶点初始化，避免模型整体浮空（y 全为正）时把 ymin 误当 0 导致缩放算错
        first = True
        ymax = ymin = 0.0
        for md in mesh_data:
            w = md["model"].world
            v = md["verts"]
            for i in range(md["nverts"]):
                y = m_point(w, v[i * 3:i * 3 + 3])[1]
                if first:
                    ymax = ymin = y
                    first = False
                else:
                    ymax = max(ymax, y)
                    ymin = min(ymin, y)
        height = ymax - ymin
        scale = (20.0 / height) if height > 1e-6 else 1.0
    else:
        scale = float(scale_mode)

    # 换轴 / 换手性 / 转向统一走这里（方向用同一套，缩放只作用在位置上）。
    #
    # 为什么 turn 是「把镜像轴从 Z 换成 X」而不是「再绕 Y 转 180°」：
    # FBX→MMD 必须做一次单轴反射（行列式 -1）才能把右手系变左手系，默认反射的是
    # Z。而反射 Z 会把「朝 +Z 的模型」翻成朝 -Z（正好是 MMD 要的），
    # 同时把「本来就朝 -Z 的模型」翻成朝 +Z（背对镜头）。
    # 后一种要修，而「单轴反射由 Z 改成 X」与默认方案之间恰好差一个绕 Y 的 180°，
    # 行列式仍是 -1，手性转换不受影响，绕序照旧交给 detect_winding 投票。
    def xfd(d):
        x, y, z = d
        if turn:
            x = -x
            if not flip_z:
                z = -z
        elif flip_z:
            z = -z
        return (x, y, z)

    def xf(p):
        x, y, z = xfd(p)
        return (x * scale, y * scale, z * scale)

    # ---- build vertices
    pmx_verts = []          # (pos, nrm, uv, weights[(bone, w)])
    pmx_tris = []           # (a, b, c)
    segments = []           # (material_id, tri_start, tri_count, mesh_index)
    # (geometry_id, control_point) -> [pmx vertex indices]  (used for morphs)
    geo_cp_to_pmx = {}
    # geometry id -> full transform (world * geometric) for that mesh
    geo_w = {}
    uv_by_mat = {}          # 材质节点 id -> 该材质实际用到的 UV 角点（MMD 口径）

    weights_of_geo = {}
    if verbose:
        print("links: geo->model=%d geo->skin=%d cluster->skin=%d cluster->bone=%d"
              % (len(scene.geometry_model), len(scene.geometry_skin),
                 len(scene.cluster_skin), len(scene.cluster_bone)))
    for gid in geo_ids:
        skin = scene.geometry_skin.get(gid)
        acc = {}
        if skin is not None:
            for cid, sid in scene.cluster_skin.items():
                if sid != skin:
                    continue
                bind = scene.cluster_bone.get(cid)
                if bind is None or bind not in bone_index:
                    continue
                cl = scene.byid[cid]
                idn = cl.get("Indexes")
                wn = cl.get("Weights")
                if idn is None or wn is None:
                    continue
                for vi, wt in zip(idn.props[0], wn.props[0]):
                    if wt <= 0.0:
                        continue
                    acc.setdefault(vi, []).append((bind, float(wt)))
        weights_of_geo[gid] = acc

    vbase = 0
    mesh_records = []         # 每个网格：名字、顶点 / 三角面数、包围盒、PMX 材质段（找替换网格用）
    mat_entries = []          # material node id (or None) for each draw segment
    for md in mesh_data:
        gid = md["geo"].props[0]
        model = md["model"]
        # full transform for geometry-space vertices = world * geometric
        w = m_mul(model.world, md["geom"])
        geo_w[gid] = w
        v = md["verts"]
        uv = md["uv"]
        nrm = md["nrm"]
        pvi = md["pvi"]
        acc = weights_of_geo[gid]
        # 没有蒙皮的网格（Unity 酱的眼睛、嘴、眉毛就是挂在头骨下面的普通网格）：
        # 整个网格跟着它在层级里最近的那根祖先骨走。以前给的是 bone_index 里随便的第一根骨，
        # 结果脸部零件跟着手臂甩
        rigid_bone = None
        if not acc:
            p = model.parent
            while p is not None and rigid_bone is None:
                if p.id in bone_index:
                    rigid_bone = p.id
                p = p.parent
        mats = md["mats"]
        poly_mat = md["poly_mat"]

        # Build vertices (deduplicated across the whole mesh) and tag every
        # triangle with its material index.  Triangles are then emitted grouped
        # by material so that each PMX draw segment's faces stay contiguous in
        # the index buffer (required by the format).
        key_map = {}
        mesh_tris = []         # list of (mat_idx, (a, b, c))
        for ti, tri in enumerate(md["tris"]):
            mat_idx = poly_mat[ti] if poly_mat is not None else 0
            out = []
            for cp, corner in tri:
                nval = nrm[corner] if (nrm is not None and corner < len(nrm)) else None
                uval = uv[corner] if (uv is not None and corner < len(uv)) else (0.0, 0.0)
                # 记下每个材质实际用到的 UV 角点（给 alpha 判定定点采样用，MMD 口径 v 上下翻）
                _uvm = uv_by_mat.setdefault(mats[mat_idx] if mat_idx < len(mats) else None, [])
                if len(_uvm) < 20000:
                    _uvm.append((uval[0], 1.0 - uval[1]))
                k = (cp, round(uval[0], 5), round(uval[1], 5))
                if nval is not None:
                    k += (round(nval[0], 4), round(nval[1], 4), round(nval[2], 4))
                j = key_map.get(k)
                if j is None:
                    ppos = xf(m_point(w, v[cp * 3:cp * 3 + 3]))
                    if nval is not None:
                        d = m_dir(w, nval)
                        ln = math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) or 1.0
                        d = (d[0] / ln, d[1] / ln, d[2] / ln)
                        nd = xfd(d)
                    else:
                        nd = (0.0, 1.0, 0.0)
                    wl = sorted(acc.get(cp, []), key=lambda t: -t[1])[:4]
                    if not wl:
                        wl = [(rigid_bone if rigid_bone is not None else next(iter(bone_index)), 1.0)]
                    tot = sum(x[1] for x in wl) or 1.0
                    wl = [(b, wt / tot) for b, wt in wl]
                    j = len(pmx_verts)
                    key_map[k] = j
                    pmx_verts.append((ppos, nd, (uval[0], 1.0 - uval[1]), wl))
                    geo_cp_to_pmx.setdefault((gid, cp), []).append(j)
                out.append(j)
            mesh_tris.append((mat_idx, (out[0], out[1], out[2])))

        tri_before = len(pmx_tris)
        for mi, mat_id in enumerate(mats):
            grp = [t for (m, t) in mesh_tris if m == mi]
            if not grp:
                continue
            mi_idx = len(mat_entries)
            mat_entries.append(mat_id)
            start = len(pmx_tris)
            pmx_tris.extend(grp)
            segments.append((mi_idx, start, len(grp), model.name))
        if verbose:
            print("  %-22s verts=%-6d tris=%-6d mats=%d skin=%s"
                  % (model.name, len(pmx_verts) - vbase,
                     len(pmx_tris) - tri_before, len(mats),
                     "yes" if acc else "no"))
        if len(pmx_verts) > vbase:
            ps = [pv[0] for pv in pmx_verts[vbase:]]
            mesh_records.append({
                "name": model.name, "nv": len(pmx_verts) - vbase, "nt": len(pmx_tris) - tri_before,
                "lo": [min(p[k] for p in ps) for k in range(3)], "hi": [max(p[k] for p in ps) for k in range(3)],
                "segs": [sg[0] for sg in segments if sg[1] >= tri_before]})
        vbase = len(pmx_verts)

    # ---- 互为替换的网格：只显示主网格，其余默认隐藏，各做一个材质表情来切换
    alt_groups = _alternate_groups(mesh_records)
    hidden_segs = {s for _, alts in alt_groups for a in alts for s in a["segs"]}
    if verbose and alt_groups:
        for prim, alts in alt_groups:
            print("  alternates: %s 显示；%s 默认隐藏，做成切换表情"
                  % (prim["name"], "、".join(a["name"] for a in alts)))

    # ---- winding check: geometric face normal must agree with the vertex
    # normals we imported, otherwise MMD would render the model inside-out.
    agree = 0.0
    for a, b, c in pmx_tris[:8000]:
        pa, pb, pc = pmx_verts[a][0], pmx_verts[b][0], pmx_verts[c][0]
        ux, uy, uz = pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2]
        vx, vy, vz = pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2]
        fx, fy, fz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        fl = math.sqrt(fx * fx + fy * fy + fz * fz) or 1.0
        na = pmx_verts[a][1]
        agree += (fx * na[0] + fy * na[1] + fz * na[2]) / fl
    flipped = agree < 0
    if flipped:
        pmx_tris = [(a, c, b) for a, b, c in pmx_tris]
    if verbose:
        print("  winding: face/vertex normal agreement=%.1f -> %s"
              % (agree, "reversed" if flipped else "kept"))

    # ---- textures: collect one diffuse texture per material, build global list
    tex_list = []                 # ordered unique basenames
    tex_name_to_idx = {}
    tex_index_of_mat = {}         # material node id -> texture index (or None)
    # Unity 导出的 FBX 常常没有贴图文件名（或指向导出者电脑上的 .psd），贴图是 Unity 材质（.mat）
    # 按 GUID 指定的：在 Unity 工程 / 解开的 unitypackage 里按材质名找回来（formats/unityasset.py）
    unity_map = []
    tex_paths = {}                # basename -> 已知的完整路径（来自 Unity 材质）
    tex_fallback = {}             # basename -> 材质名（文件找不到时再查 Unity 材质）

    def unity_tex(mat_name):
        if not unity_map:
            try:
                unity_map.append(unityasset.material_textures(
                    os.path.dirname(os.path.abspath(scene.path))))
            except Exception:  # noqa: BLE001  找不到就算了
                unity_map.append({})
        return unity_map[0].get(mat_name)
    for si, (mi_idx, start, cnt, mname) in enumerate(segments):
        mat_node_id = mat_entries[si] if si < len(mat_entries) else None
        if mat_node_id is None:
            continue
        tids = scene.texture_materials.get(mat_node_id, [])
        basename = _pick_diffuse(scene, mat_node_id, tids)
        mnode = scene.byid.get(mat_node_id)
        mat_name = _ns_strip(_clean_name(mnode.props[1])) if mnode is not None and len(mnode.props) > 1 else ""
        if not basename or not basename.lower().endswith(unityasset.IMAGE_EXT):
            up = unity_tex(mat_name) if mat_name else None
            if up:
                basename = os.path.basename(up)
                tex_paths[basename] = up
        elif mat_name:
            tex_fallback.setdefault(basename, mat_name)
        if basename:
            if basename not in tex_name_to_idx:
                tex_name_to_idx[basename] = len(tex_list)
                tex_list.append(basename)
            tex_index_of_mat[mat_node_id] = tex_name_to_idx[basename]
    # 每张贴图对应「哪些材质用到了它」——alpha 自动判定按这些材质的 UV 定点采样，
    # 而不是扫整图（整图的空白 padding 会把统计带偏）。
    alpha_uvs = {}
    for mat_node_id, tix in tex_index_of_mat.items():
        if 0 <= tix < len(tex_list):
            bn = tex_list[tix]
            lst = uv_by_mat.get(mat_node_id)
            if lst:
                alpha_uvs.setdefault(bn, []).extend(lst)
    # copy image files next to the pmx when they can be found locally
    tex_out_names, tex_stats = _copy_textures(
        tex_list, scene.path, _odir, verbose, alpha_mode=alpha_mode,
        alpha_uvs=alpha_uvs, known=tex_paths,
        fallback=lambda bn: unity_tex(tex_fallback[bn]) if bn in tex_fallback else None,
        threads=threads)
    # 走了 alpha 处理的贴图在 PMX 里要引用 <名>_noalpha.png
    tex_list = [tex_out_names.get(bn, bn) for bn in tex_list]

    # ---- index widths depend on the final vertex / triangle / material / texture counts
    # (multi-material meshes raise the material count above the per-mesh guess)
    nv = len(pmx_verts)
    nt = len(pmx_tris)
    vi_size = pick_index_size(nv, signed=False)
    ti_size = pick_index_size(max(len(tex_list), 1) + 1, signed=True)
    mi_size = pick_index_size(max(len(segments), 1), signed=True)
    bi_size = pick_index_size(max(len(bones), 1) + 1, signed=True)

    pw = PmxWriter((vi_size, ti_size, mi_size, bi_size, 1, 1))

    # ---- bone names / positions are taken in the SAME pmx space
    bpos = {}
    for m in bones:
        node = scene.byid.get(m.id)
        bw = m_mul(m.world, scene._geom_matrix(node) if node else m_ident())
        bpos[m.id] = xf(m_translation(bw))

    # ---- optional centering / grounding (MMD convention: origin-centered, feet at y=0)
    if center and nv:
        xs = [p[0] for p, _, _, _ in pmx_verts]
        ys = [p[1] for p, _, _, _ in pmx_verts]
        zs = [p[2] for p, _, _, _ in pmx_verts]
        off = (-(min(xs) + max(xs)) / 2.0, -min(ys), -(min(zs) + max(zs)) / 2.0)
        pmx_verts = [((p[0] + off[0], p[1] + off[1], p[2] + off[2]), n, uv, wl)
                     for p, n, uv, wl in pmx_verts]
        for k in bpos:
            x, y, z = bpos[k]
            bpos[k] = (x + off[0], y + off[1], z + off[2])
        if verbose:
            print("  centered: offset=(%.2f, %.2f, %.2f)" % off)

    model_name = (name or os.path.splitext(os.path.basename(out_path))[0]).strip()
    if not model_name:
        model_name = "Model"
    pw.header(model_name, model_name,
              "Converted from FBX by fbx2pmx.py",
              "Converted from FBX by fbx2pmx.py")

    # vertices
    pw.i32(nv)
    for pos, nd, uv, wl in pmx_verts:
        pw.f32(pos[0]); pw.f32(pos[1]); pw.f32(pos[2])
        pw.f32(nd[0]); pw.f32(nd[1]); pw.f32(nd[2])
        pw.f32(uv[0]); pw.f32(uv[1])
        n = len(wl)
        if n == 1:
            pw.buf += bytes([0])
            pw.idx(bone_index[wl[0][0]], bi_size)
            wsum = 1.0
        elif n == 2:
            pw.buf += bytes([1])
            pw.idx(bone_index[wl[0][0]], bi_size)
            pw.idx(bone_index[wl[1][0]], bi_size)
            pw.f32(wl[0][1])
            wsum = 1.0
        else:
            while len(wl) < 4:
                wl.append((wl[-1][0], 0.0))     # pad with zero weight
            pw.buf += bytes([2])
            for b, _ in wl:
                pw.idx(bone_index[b], bi_size)
            for _, wt in wl:
                pw.f32(wt)
            wsum = 1.0
        # 顶点 edge scale：写 0 去除 MMD 的黑色轮廓线
        pw.f32(0.0)

    # faces
    pw.i32(nt * 3)
    for tri in pmx_tris:
        pw.uidx(tri[0], vi_size)
        pw.uidx(tri[1], vi_size)
        pw.uidx(tri[2], vi_size)

    # textures
    pw.i32(len(tex_list))
    for bn in tex_list:
        pw.text("textures\\" + bn)

    # materials (one PMX material per draw segment, in segment order)
    pw.i32(len(segments))
    for si, (mi_idx, start, cnt, mname) in enumerate(segments):
        mat_node_id = mat_entries[si] if si < len(mat_entries) else None
        mat = scene.byid.get(mat_node_id) if mat_node_id else None
        mlabel = _clean_name(mat.props[1]) if mat else (mname or ("Material%d" % si))
        diffuse = [1.0, 1.0, 1.0, 1.0]
        if mat is not None:
            dc = mat.get("DiffuseColor")
            if dc is not None and len(dc.props) >= 3:
                diffuse = [float(dc.props[0]), float(dc.props[1]),
                           float(dc.props[2]), 1.0]
        if mi_idx in hidden_segs:
            diffuse[3] = 0.0                            # 替换网格默认隐藏（切换表情把它显示出来）
        pw.text(mlabel); pw.text(mlabel)
        for v4 in diffuse:
            pw.f32(v4)
        pw.f32(0.0); pw.f32(0.0); pw.f32(0.0)          # specular
        # shininess：C# 参考实现 MaterialDefaults.UseFbxShininess = false
        # → 一律写默认值 25（不是 0）。specular 是 0 所以不影响观感，
        # 只是让材质和 FbxImporterPlugin 的产物逐字段一致。
        pw.f32(25.0)                                    # shininess
        pw.f32(0.5); pw.f32(0.5); pw.f32(0.5)          # ambient (按需求默认 0.5 0.5 0.5)
        # 描画フラグ：C# 参考实现是 BothDraw(true) | Shadow(true)、
        # SelfShadow(false) | SelfShadowMap(false) | Edge(false)
        # → 0x01 | 0x02 = 0x03。
        # 以前多写了 0x04（セルフシャドウマップに描画），与参考实现不一致：
        # 既然没开 0x08（接收自阴影），就不该往自阴影贴图里画。
        pw.buf += bytes([0x01 | 0x02])
        pw.f32(0.0); pw.f32(0.0); pw.f32(0.0); pw.f32(0.0)   # edge colour（alpha=0 彻底去黑边）
        pw.f32(0.0)                                     # edge size
        tex_idx = tex_index_of_mat.get(mat_node_id, -1) if mat_node_id else -1
        pw.idx(tex_idx, ti_size)                        # texture
        pw.idx(-1, ti_size)                             # sphere
        pw.buf += bytes([0])                            # sphere mode
        # toon：**不使用**。
        #
        # PMX 里 toon 字段的宽度由「共有Toonフラグ」决定，两个方向都别搞反
        # （PMX 规范 + mmd_tools 的 `is_shared_toon_texture` 一致）：
        #     flag = 1 → 共享/内建 toon。字段是 **1 字节编号**，引用 MMD 安装目录
        #                `Data/` 下的 toon01.bmp … toon10.bmp。
        #                ⚠ **编号 0 就是 toon01.bmp，不是「不使用」**：mmd_tools
        #                  源码里写死了这个 +1
        #                      toon_path = "toon%02d.bmp" % (shared + 1)
        #                  而 MMD 的 Data 目录里**没有 toon00.bmp**（只有 01..10）。
        #     flag = 0 → 本模型**纹理表**里的贴图，字段是**纹理索引宽度**，
        #                **-1 = なし（不使用）**。
        #
        # 2026-09-24 修：以前写的是 flag=1 + 编号 0，注释误以为「0 = toon00.bmp
        # = 不使用」，实际等于给每个材质硬套一层 toon01.bmp，用户反馈「PMX 还是
        # 自动上了 Toon 路径」。对照实测：用户本机那份公认正常的 Anastasya.pmx
        # 全部 13 个材质都是 flag=0 / -1。
        pw.buf += bytes([0])                            # toon: 引用本模型纹理表
        pw.idx(-1, ti_size)                             # -1 = 不使用 toon
        pw.text("")
        pw.i32(cnt * 3)

    # bones
    pw.i32(len(bones))
    for m in bones:
        pw.text(m.name); pw.text(m.name)
        p = bpos[m.id]
        pw.f32(p[0]); pw.f32(p[1]); pw.f32(p[2])
        par = m.parent
        while par is not None and par.id not in bone_index:
            par = par.parent
        pw.idx(bone_index[par.id] if par else -1, bi_size)
        pw.i32(0)                                       # deform layer
        child_bone = next((c for c in m.children if c.id in bone_index), None)
        bflag = 0x0002 | 0x0004 | 0x0008 | 0x0010       # rot/move/visible/operable
        if child_bone is not None:
            bflag |= 0x0001                             # tail stored as bone index
        pw.buf += struct.pack("<H", bflag)              # bone flags are u16
        if bflag & 0x0001:
            pw.idx(bone_index[child_bone.id], bi_size)
        else:
            pw.f32(0.0); pw.f32(0.5); pw.f32(0.0)
        # (no rotatable/movable axis flags, no IK block)

    # ---- morphs: FBX blend shapes -> PMX vertex morphs (表情)
    written_morphs = 0
    morph_list = []
    if morphs and getattr(scene, "morphs", None):
        merged = {}
        for m in scene.morphs:
            gid = m["geo_id"]
            w = geo_w.get(gid)
            if w is None:
                continue
            indexes = m["indexes"]
            deltas = m["deltas"]
            # 自动检测 Shape.Vertices 是「相对偏移」还是「绝对坐标」：不同导出器写法不同。
            # 相对偏移写法下 shape 值本身即位移（量级小）；绝对坐标写法下 shape 与 base 同量级（大），
            # 此时真正的位移应为 shape − base。实测 Kiana 为相对偏移写法。
            base_node = scene.byid.get(gid)
            base = base_node.get("Vertices").props[0] if base_node else None
            is_relative = True
            if base is not None and indexes:
                rel_sum = abs_sum = 0.0
                for k in range(min(len(indexes), 64)):
                    cp = indexes[k]
                    dx, dy, dz = deltas[k * 3], deltas[k * 3 + 1], deltas[k * 3 + 2]
                    rel_sum += abs(dx) + abs(dy) + abs(dz)
                    bx, by, bz = base[cp * 3], base[cp * 3 + 1], base[cp * 3 + 2]
                    abs_sum += abs(dx - bx) + abs(dy - by) + abs(dz - bz)
                is_relative = rel_sum <= abs_sum
            offsets = []
            for k in range(len(indexes)):
                cp = indexes[k]
                dx = deltas[k * 3]
                dy = deltas[k * 3 + 1]
                dz = deltas[k * 3 + 2]
                if not is_relative and base is not None:
                    bx, by, bz = base[cp * 3], base[cp * 3 + 1], base[cp * 3 + 2]
                    dx -= bx; dy -= by; dz -= bz
                off = xf(m_dir(w, (dx, dy, dz)))   # same linear transform as base
                for vidx in geo_cp_to_pmx.get((gid, cp), []):
                    offsets.append((vidx, off))
            if not offsets:
                continue
            nm = m["name"]
            low = nm.lower()
            # panel: which FACS tab the morph shows in (1=眉 2=目 3=口 4=その他)
            if low.startswith("eyebrow") or "brow" in low:
                panel = 1
            elif low.startswith("eye") or "eyeshape" in low:
                panel = 2
            elif low.startswith("mouth"):
                panel = 3
            else:
                panel = 4
            kind = 1                    # 1 = 頂点 (vertex) morph; PMX type, not FACS tab
            # 不同网格上的同名 blend shape 是同一个表情的几部分（Unity 酱的眼睛和睫毛各有一个
            # EYE_DEF_C，Unity 的动画也是按名字一起驱动）：合成一个 PMX 表情。以前各写一个，
            # VRM 里就成了 EYE_DEF_C 和 EYE_DEF_C_2，眨眼只闭一半
            same = merged.get(nm)
            if same is not None:
                same["offsets"].extend(offsets)
                continue
            merged[nm] = {"name": nm, "name_en": nm, "panel": panel, "kind": kind, "offsets": offsets}
            morph_list.append(merged[nm])
        if max_morphs is not None and len(morph_list) > max_morphs:
            if verbose:
                print("  morphs: capping %d -> %d (max_morphs)"
                      % (len(morph_list), max_morphs))
            morph_list = morph_list[:max_morphs]
        if verbose:
            print("  morphs: wrote %d vertex morph(s) from %d blend shape(s)"
                  % (len(morph_list), len(scene.morphs)))
    if morphs and alt_groups:
        # 材质表情（kind 8）：替换网格的 alpha +1（显示），主网格的 alpha ×0（隐藏）
        show = [0.0] * 3 + [1.0] + [0.0] * 24
        hide = [1.0] * 3 + [0.0] + [1.0] * 24
        used_names = {mo["name"] for mo in morph_list}
        for prim, alts in alt_groups:
            for a in alts:
                nm = a["name"]
                while nm in used_names:
                    nm += "_"
                used_names.add(nm)
                offs = [(s, 1, show) for s in a["segs"]] + [(s, 0, hide) for s in prim["segs"]]
                morph_list.append({"name": nm, "name_en": nm, "panel": 4, "kind": 8, "offsets": offs})
    written_morphs = len(morph_list)
    pw.i32(len(morph_list))
    for mo in morph_list:
        pw.text(mo["name"]); pw.text(mo["name_en"])
        pw.buf += bytes([mo["panel"]])
        pw.buf += bytes([mo["kind"]])
        pw.i32(len(mo["offsets"]))
        if mo["kind"] == 8:
            for mi, op, vals in mo["offsets"]:
                pw.idx(mi, mi_size)
                pw.buf += bytes([op])
                for x in vals:
                    pw.f32(x)
            continue
        for vidx, off in mo["offsets"]:
            pw.uidx(vidx, vi_size)
            pw.f32(off[0]); pw.f32(off[1]); pw.f32(off[2])

    # display frames: one, listing every bone
    pw.i32(1)
    pw.text("Root"); pw.text("Root")
    pw.buf += bytes([1])
    pw.i32(len(bones))
    for m in bones:
        pw.buf += bytes([0])            # 要素対象 0 = ボーン
        pw.idx(bone_index[m.id], bi_size)

    # rigid bodies / joints
    pw.i32(0)
    pw.i32(0)

    with open(out_path, "wb") as f:
        f.write(pw.buf)
    return {"verts": nv, "tris": nt, "bones": len(bones),
            "materials": len(segments), "bytes": len(pw.buf),
            "morphs": written_morphs, "scale": scale, "vi_size": vi_size,
            "face_turn": bool(turn), "facing_reason": facing_reason,
            "alpha_mode": alpha_mode, "alpha": tex_stats,
            "textures": list(tex_list),
            "skipped_untextured": skipped_untextured}


def _bone_depth(m):
    d = 0
    p = m.parent
    while p is not None:
        d += 1
        p = p.parent
    return (d, m.name)


# ------------------------------------------------------------------ cli -----

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fbx")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--scale", default="mmd",
                    help="'mmd' (auto, ~20 units tall), 'raw' (1.0), or a number")
    ap.add_argument("--no-flip-z", action="store_true",
                    help="keep FBX handedness (default converts to MMD)")
    ap.add_argument("--no-center", action="store_true",
                    help="do not re-center/ground the model (keep FBX world position)")
    ap.add_argument("--no-morphs", action="store_true",
                    help="do not export FBX blend shapes as PMX morphs (表情)")
    ap.add_argument("--alpha", choices=list(ALPHA_MODES), default=None,
                    help="texture alpha: keep | auto (default) | strip")
    ap.add_argument("--remove-alpha", action="store_true", default=None,
                    help="legacy alias for --alpha (maps to auto, NOT strip: "
                         "it only strips mask-like alpha, keeps real transparency)")
    ap.add_argument("--no-auto-facing", action="store_true",
                    help="disable per-file facing detection (toe / face mesh)")
    ap.add_argument("--face-180", action="store_true",
                    help="force an extra 180 degree turn (manual override)")
    ap.add_argument("--keep-untextured-meshes", action="store_true",
                    help="keep meshes that carry no material at all (default: "
                         "skip them; these are game 'effect sheets' that would "
                         "render as an opaque grey board over the model)")
    ap.add_argument("--max-morphs", type=int, default=None,
                    help="cap the number of exported morphs (default: no limit)")
    ap.add_argument("--info", action="store_true")
    a = ap.parse_args()

    scene = Scene(a.fbx)
    print("FBX version %d, %d objects" % (scene.ver, len(scene.objects)))
    print("models=%d (bones=%d)  geometry=%d  materials=%d"
          % (len(scene.models),
             sum(1 for m in scene.models.values() if m.cls == "LimbNode"),
             sum(1 for n in scene.objects if n.name == "Geometry"),
             sum(1 for n in scene.objects if n.name == "Material")))
    if a.info:
        return 0

    out = a.out or (os.path.splitext(a.fbx)[0] + ".pmx")
    scale = a.scale
    if scale not in ("mmd", "raw"):
        try:
            scale = float(scale)
        except ValueError:
            scale = "mmd"
    if scale == "raw":
        scale = 1.0
    st = convert(scene, out, scale_mode=scale, flip_z=not a.no_flip_z,
                 center=not a.no_center, morphs=not a.no_morphs,
                 max_morphs=a.max_morphs,
                 alpha_mode=a.alpha, remove_alpha=a.remove_alpha,
                 auto_facing=not a.no_auto_facing, face_180=a.face_180,
                 skip_untextured=not a.keep_untextured_meshes,
                 name=os.path.splitext(os.path.basename(a.fbx))[0])
    print("\nwrote %s" % out)
    print("  vertices=%d  triangles=%d  bones=%d  materials=%d  morphs=%d  scale=%.4f"
          % (st["verts"], st["tris"], st["bones"], st["materials"],
             st["morphs"], st["scale"]))
    if st["skipped_untextured"]:
        print("  skipped %d mesh(es) with no material: %s"
              % (len(st["skipped_untextured"]),
                 ", ".join("%s(%d tris)" % (n, t) for n, t in st["skipped_untextured"])))
    print("  size=%.2f MB  (vertex index width %d bytes)"
          % (st["bytes"] / 1048576, st["vi_size"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
