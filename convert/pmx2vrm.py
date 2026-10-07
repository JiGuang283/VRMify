# -*- coding: utf-8 -*-
"""pmx2vrm.py - 把 PMX 模型转成 VRM（glTF 2.0 / GLB 容器），纯 Python 标准库。

不需要 Blender、不需要 mmd_tools、不需要 Unity。

坐标约定（数值均指文件里存的原始数字）
--------
PMX（MMD）   正面 -Z，角色左手侧 +X，Y 向上（DirectX 左手系，逆时针缠绕）
VRM 0.x      正面 -Z，角色左手侧 -X，Y 向上（glTF 右手系、逆时针为正面）
VRM 1.0      正面 +Z，角色左手侧 +X，Y 向上

所以：
  * PMX（DirectX 左手系）与 glTF（右手系）手性相反：同一组数字直接
    解释是镜像人形，必须反射一个轴——转 0.x 取反 X；转 1.0 取反 Z。
  * 两种情况三角绕序都要反转（反射把「外面看逆时针」颠倒了）。
  * 尺寸：MMD 模型约 20 单位高，VRM 用米，默认按 1.6 m 归一。

用法：
    python pmx2vrm.py model.pmx -o model.vrm
    python pmx2vrm.py model.pmx --spec 0x --title "名字" --author "作者"
"""
import argparse
import math
import os
import sys

# 让 `python convert/pmx2vrm.py ...` 也能直接跑：脚本自己所在目录（convert/）和同级的
# formats/ 都要进 sys.path。以前这里只加了 convert/，于是 `import pmxio` 之类会
# ModuleNotFoundError —— 而 README 里给的命令行用法就是这么调的。
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "formats")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import pmxio
import vrmio

# --------------------------------------------------------- 骨骼名映射表 ----
MMD_TO_VRM = {
    "下半身": "hips",
    "上半身": "spine",
    "上半身2": "chest",
    "上半身3": "upperChest",
    "首": "neck",
    "頭": "head",
    # 「両目」是双眼共用的控制骨，不对应任何一只眼，不能映射成 leftEye
    # （以前这么写，导致左右眼都绑到両目上，而真正的 左目 被挤掉）。
    "左目": "leftEye", "右目": "rightEye",
    "左肩": "leftShoulder", "右肩": "rightShoulder",
    "左腕": "leftUpperArm", "右腕": "rightUpperArm",
    "左ひじ": "leftLowerArm", "右ひじ": "rightLowerArm",
    "左肘": "leftLowerArm", "右肘": "rightLowerArm",
    "左手首": "leftHand", "右手首": "rightHand",
    "左足": "leftUpperLeg", "右足": "rightUpperLeg",
    "左ひざ": "leftLowerLeg", "右ひざ": "rightLowerLeg",
    "左足首": "leftFoot", "右足首": "rightFoot",
    "左つま先": "leftToes", "右つま先": "rightToes",
}
# 手指不放进 MMD_TO_VRM：骨位名取决于规范版本和模型有哪几节，见 _finger_slots。
#   VRM 1.0：拇指 Metacarpal / Proximal / Distal，其余 Proximal / Intermediate / Distal
#   VRM 0.x：拇指也是 Proximal / Intermediate / Distal（0.x 没有 Metacarpal）
#   两个版本都没有「食指～小指的 Metacarpal」，所以 人指０ 之类不映射。
_FINGERS = {"親指": "Thumb", "人指": "Index", "中指": "Middle",
            "薬指": "Ring", "小指": "Little"}
_DIGITS = {"０": 0, "0": 0, "１": 1, "1": 1, "２": 2, "2": 2, "３": 3, "3": 3}


def _finger_key(name):
    """「左人指２」→ ("left", "Index", 2)；不是手指骨返回 None。"""
    if not name or len(name) != 4 or name[0] not in "左右":
        return None
    f = _FINGERS.get(name[1:3])
    d = _DIGITS.get(name[3])
    if f is None or d is None:
        return None
    return ("left" if name[0] == "左" else "right"), f, d


def _finger_slots(finger, digits, is1):
    """digits: 该手指在模型里出现的节号集合 → {节号: 骨位后缀}。"""
    if finger != "Thumb":
        return {d: s for d, s in ((1, "Proximal"), (2, "Intermediate"),
                                  (3, "Distal")) if d in digits}
    if is1:
        if 0 in digits:
            return {d: s for d, s in ((0, "Metacarpal"), (1, "Proximal"),
                                      (2, "Distal")) if d in digits}
        return {d: s for d, s in ((1, "Proximal"), (2, "Distal"))
                if d in digits}
    present = [d for d in (0, 1, 2) if d in digits]
    return dict(zip(present, ("Proximal", "Intermediate", "Distal")))

# 英文名兜底
ALT_TO_VRM = {
    "hips": "hips", "spine": "spine", "chest": "chest",
    "upperchest": "upperChest", "neck": "neck", "head": "head",
    "leye": "leftEye", "reye": "rightEye",
    "lowerbody": "hips", "upperbody": "spine", "upperbody2": "chest",
    "lshoulder": "leftShoulder", "rshoulder": "rightShoulder",
    "larm": "leftUpperArm", "rarm": "rightUpperArm",
    "lelbow": "leftLowerArm", "relbow": "rightLowerArm",
    "lwrist": "leftHand", "rwrist": "rightHand",
    "lleg": "leftUpperLeg", "rleg": "rightUpperLeg",
    "lknee": "leftLowerLeg", "rknee": "rightLowerLeg",
    "lankle": "leftFoot", "rankle": "rightFoot",
    "ltoe": "leftToes", "rtoe": "rightToes",
}

VRM_PARENT = {
    "spine": "hips", "chest": "spine", "upperChest": "chest",
    "neck": "upperChest", "head": "neck",
    "leftEye": "head", "rightEye": "head", "jaw": "head",
    "leftShoulder": "upperChest", "leftUpperArm": "leftShoulder",
    "leftLowerArm": "leftUpperArm", "leftHand": "leftLowerArm",
    "rightShoulder": "upperChest", "rightUpperArm": "rightShoulder",
    "rightLowerArm": "rightUpperArm", "rightHand": "rightLowerArm",
    "leftUpperLeg": "hips", "leftLowerLeg": "leftUpperLeg",
    "leftFoot": "leftLowerLeg", "leftToes": "leftFoot",
    "rightUpperLeg": "hips", "rightLowerLeg": "rightUpperLeg",
    "rightFoot": "rightLowerLeg", "rightToes": "rightFoot",
}
for _s in ("left", "right"):
    VRM_PARENT[_s + "ThumbMetacarpal"] = _s + "Hand"
    VRM_PARENT[_s + "ThumbProximal"] = _s + "ThumbMetacarpal"
    VRM_PARENT[_s + "ThumbIntermediate"] = _s + "ThumbProximal"
    VRM_PARENT[_s + "ThumbDistal"] = _s + "ThumbIntermediate"
    for _f in ("Index", "Middle", "Ring", "Little"):
        VRM_PARENT[_s + _f + "Proximal"] = _s + "Hand"
        VRM_PARENT[_s + _f + "Intermediate"] = _s + _f + "Proximal"
        VRM_PARENT[_s + _f + "Distal"] = _s + _f + "Intermediate"

REQUIRED_1_0 = ["hips", "spine", "head",
                "leftUpperArm", "leftLowerArm", "leftHand",
                "rightUpperArm", "rightLowerArm", "rightHand",
                "leftUpperLeg", "leftLowerLeg", "leftFoot",
                "rightUpperLeg", "rightLowerLeg", "rightFoot"]
REQUIRED_0X = REQUIRED_1_0 + ["chest", "neck"]

# 自动补齐时的相对偏移（左、上、前）
_SYNTH = {
    "spine": (0.0, 0.12, 0.0), "chest": (0.0, 0.12, 0.0),
    "upperChest": (0.0, 0.10, 0.0), "neck": (0.0, 0.10, 0.0),
    "head": (0.0, 0.12, 0.0),
    "leftShoulder": (0.05, 0.10, 0.0), "rightShoulder": (-0.05, 0.10, 0.0),
    "leftUpperArm": (0.12, 0.0, 0.0), "rightUpperArm": (-0.12, 0.0, 0.0),
    "leftLowerArm": (0.25, 0.0, 0.0), "rightLowerArm": (-0.25, 0.0, 0.0),
    "leftHand": (0.24, 0.0, 0.0), "rightHand": (-0.24, 0.0, 0.0),
    "leftUpperLeg": (0.09, -0.08, 0.0), "rightUpperLeg": (-0.09, -0.08, 0.0),
    "leftLowerLeg": (0.0, -0.40, 0.0), "rightLowerLeg": (0.0, -0.40, 0.0),
    "leftFoot": (0.0, -0.40, 0.06), "rightFoot": (0.0, -0.40, 0.06),
}
_HIPS_Y = 0.90

EXPR_1_0 = {
    "まばたき": "blink", "瞬き": "blink", "ウィンク": "blinkRight",
    "ウィンク２": "blinkLeft", "ウィンク右": "blinkRight",
    "ウィンク左": "blinkLeft", "ｳｨﾝｸ": "blinkRight", "ｳｨﾝｸ２": "blinkLeft",
    "びっくり": "surprised", "怒り": "angry", "怒る": "angry",
    "悲しみ": "sad", "困る": "sad", "真面目": "relaxed",
    "にこり": "happy", "笑い": "happy", "にやり": "happy",
    "あ": "aa", "あ２": "aa", "い": "ih", "う": "ou", "え": "ee", "お": "oh",
}
# 注意：MMD 标准表情里的「上」「下」是**眉毛**表情（眉パネル），不是视线，
# 所以不映射到 lookUp / lookDown（以前映射了，VRM 里一看上方眉毛就跳起来）。
# MMD 没有标准的视线表情，视线交给 lookAt（type=bone）。
EXPR_0X = {
    "まばたき": "Blink", "瞬き": "Blink", "ウィンク": "Blink_R",
    "ウィンク２": "Blink_L", "ウィンク右": "Blink_R", "ウィンク左": "Blink_L",
    "怒り": "Angry", "悲しみ": "Sorrow", "困る": "Sorrow",
    "にこり": "Joy", "笑い": "Joy",
    "あ": "A", "あ２": "A", "い": "I", "う": "U", "え": "E", "お": "O",
}


# ------------------------------------------------------------------ 工具 --
def _norm(s):
    return (s or "").strip().replace(" ", "").replace("_", "").lower()


def _clean_parents(bones):
    """PMX 父骨索引表，越界/指向自己的记成 -1。"""
    n = len(bones)
    out = []
    for i, b in enumerate(bones):
        p = b["parent"]
        out.append(p if (0 <= p < n and p != i) else -1)
    return out


def _is_ancestor(parents, a, b):
    """a 是否是 b 的祖先（含 a == b）。遇到环也会停。"""
    seen = set()
    while 0 <= b < len(parents) and b not in seen:
        if b == a:
            return True
        seen.add(b)
        b = parents[b]
    return False


def _reparent(parents, child, new_parent):
    """把 child 挂到 new_parent 下；会成环就拒绝，返回是否成功。"""
    if new_parent == child or _is_ancestor(parents, child, new_parent):
        return False
    parents[child] = new_parent
    return True


def _append_aliases(bones, parents, eps):
    """「付与回転 100%、和付与親同位置」的骨 → 付与親，返回 {骨: 付与親}。

    典型是 足D / ひざD / 足首D：网格权重全在 D 骨上，D 骨靠 PMX 的付与
    跟随 足 / ひざ / 足首 转动；而 humanoid 映射指向的是 足 / ひざ / 足首。
    glTF/VRM 没有付与机制，原样导出时转大腿网格完全不动。
    这种骨与付与親在静止姿势下完全重合、旋转也 1:1 跟随，把它的权重和
    子骨直接并到付与親上是等价的。
    付与率不是 1（腕捩1 的 0.25、腰キャンセル 的 -1 等）的不动。
    """
    n = len(bones)
    alias = {}
    for i, b in enumerate(bones):
        ir = b.get("inherit_rot")
        if not ir:
            continue
        src, rate = ir
        if not (0 <= src < n) or src == i or abs(rate - 1.0) > 1e-4:
            continue
        im = b.get("inherit_mov")
        if im and im[0] != src:
            continue
        p, q = b["pos"], bones[src]["pos"]
        if math.sqrt(sum((p[k] - q[k]) ** 2 for k in range(3))) > eps:
            continue
        if _is_ancestor(parents, i, src):        # 付与親在自己下面：并过去会成环
            continue
        alias[i] = src

    def resolve(i):
        seen = set()
        while i in alias and i not in seen:
            seen.add(i)
            i = alias[i]
        return i
    return {i: resolve(i) for i in alias}


def _map_humanoid(bones, is1, alias):
    """PMX 骨 → VRM humanoid 骨位，返回 {骨位: PMX 骨索引}。"""
    human = {}
    fingers = {}                     # (side, finger) → {节号: 骨索引}
    for i, b in enumerate(bones):
        if i in alias:               # 已并入付与親的骨不参与映射
            continue
        fk = _finger_key(b["name"])
        if fk:
            fingers.setdefault(fk[:2], {}).setdefault(fk[2], i)
            continue
        v = MMD_TO_VRM.get(b["name"])
        if not v:
            v = ALT_TO_VRM.get(_norm(b.get("name_en") or ""))
        if v and v not in human:
            human[v] = i
    for (side, finger), digits in fingers.items():
        for d, slot in _finger_slots(finger, digits, is1).items():
            human.setdefault(side + finger + slot, digits[d])
    return human


def _vrm_anchor(name, present):
    """沿 VRM_PARENT 往上找第一个在 present 里的骨位（跳过模型没有的可选骨位）。"""
    p = VRM_PARENT.get(name)
    while p and p not in present:
        p = VRM_PARENT.get(p)
    return p


def _vrm_depth(name):
    d = 0
    while name in VRM_PARENT:
        name = VRM_PARENT[name]
        d += 1
    return d


def _fix_humanoid_hierarchy(human, parents, bones, required, log):
    """保证每个 humanoid 骨都在它规范父骨位的子树里，必要时改挂父骨。

    最常见的是 MMD 的 上半身 / 下半身 是「腰」下的兄弟骨，而 VRM 要求
    spine 是 hips 的后代——不改的话转 hips（全身朝向）只有下半身跟着转。
    静止姿势下节点只有平移，改挂父骨不改变任何顶点位置。
    """
    for h in sorted(human, key=_vrm_depth):
        if h not in human:
            continue
        anc = _vrm_anchor(h, human)
        if anc is None:
            continue
        a, c = human[anc], human[h]
        if a == c:
            del human[h]
            log("提示：%s 与 %s 映射到同一根骨，已忽略 %s" % (h, anc, h), "warn")
            continue
        if _is_ancestor(parents, a, c):
            continue
        old = parents[c]
        if _reparent(parents, c, a):
            log("骨骼层级：%s（%s）不在 %s（%s）下面，已改挂过去（原父骨 %s）"
                % (h, bones[c]["name"], anc, bones[a]["name"],
                   bones[old]["name"] if old >= 0 else "无"))
        elif h not in required:
            del human[h]
            log("提示：%s（%s）的层级无法修正，已从 humanoid 中移除"
                % (h, bones[c]["name"]), "warn")
        else:
            log("警告：%s（%s）的层级无法修正，部分软件可能拒绝加载"
                % (h, bones[c]["name"]), "warn")


# ------------------------------------------------------- 3x3 矩阵小工具 --
_I3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _m3mul(a, b):
    return tuple(sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
                 for r in range(3) for c in range(3))


def _m3vec(m, v):
    return (m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
            m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
            m[6] * v[0] + m[7] * v[1] + m[8] * v[2])


def _m3T(m):
    return (m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8])


def _axis_angle(axis, ang):
    x, y, z = axis
    c, s = math.cos(ang), math.sin(ang)
    t = 1.0 - c
    return (t * x * x + c, t * x * y - s * z, t * x * z + s * y,
            t * x * y + s * z, t * y * y + c, t * y * z - s * x,
            t * x * z - s * y, t * y * z + s * x, t * z * z + c)


def _to_axis_angle(m):
    ang = math.acos(max(-1.0, min(1.0, (m[0] + m[4] + m[8] - 1.0) * 0.5)))
    if ang < 1e-9:
        return (1.0, 0.0, 0.0), 0.0
    ax = (m[7] - m[5], m[2] - m[6], m[3] - m[1])
    ln = math.sqrt(ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2)
    if ln < 1e-9:                    # 180°：取对角线最大的那列
        k = max(range(3), key=lambda i: m[i * 4])
        col = [(m[i * 3 + k] + (1.0 if i == k else 0.0)) for i in range(3)]
        ln = math.sqrt(sum(x * x for x in col)) or 1.0
        return tuple(x / ln for x in col), ang
    return (ax[0] / ln, ax[1] / ln, ax[2] / ln), ang


def _align(d, t):
    """把方向 d 转到方向 t 的最小旋转，返回 (矩阵, 角度)。"""
    ld = math.sqrt(sum(x * x for x in d))
    lt = math.sqrt(sum(x * x for x in t))
    if ld < 1e-9 or lt < 1e-9:
        return _I3, 0.0
    d = [x / ld for x in d]
    t = [x / lt for x in t]
    ax = (d[1] * t[2] - d[2] * t[1], d[2] * t[0] - d[0] * t[2],
          d[0] * t[1] - d[1] * t[0])
    s = math.sqrt(sum(x * x for x in ax))
    ang = math.atan2(s, sum(d[k] * t[k] for k in range(3)))
    if s < 1e-9:
        return _I3, 0.0
    return _axis_angle((ax[0] / s, ax[1] / s, ax[2] / s), ang), ang


def _tpose_globals(bones, parents, order, human, log):
    """算出把 A-pose 手臂抬成 T-pose 的逐骨全局变换。

    VRM 规范要求静止姿势是 T-pose（上臂、前臂沿 ±X 水平），MMD 模型多是
    手臂下垂 30～40° 的 A-pose；不改的话套 humanoid 动作手臂会整体偏一个角度。
    在 PMX 坐标里算：上臂、前臂各转一次对齐到水平 ±X，子骨跟着走；
    带付与回転的骨（腕補助 等）按付与率跟转。
    返回 {骨: (M, t)}，x' = M·x + t；没有需要转的返回 None。
    """
    pos = [b["pos"] for b in bones]
    local = {}
    for side in ("left", "right"):
        ua = human.get(side + "UpperArm")
        la = human.get(side + "LowerArm")
        hd = human.get(side + "Hand")
        if ua is None or la is None:
            continue
        d1 = [pos[la][k] - pos[ua][k] for k in range(3)]
        tgt = (1.0 if d1[0] >= 0 else -1.0, 0.0, 0.0)
        r1, a1 = _align(d1, tgt)
        if a1 > math.radians(1.0):
            local[ua] = r1
        else:
            r1, a1 = _I3, 0.0
        a2 = 0.0
        if hd is not None:
            d2 = _m3vec(r1, [pos[hd][k] - pos[la][k] for k in range(3)])
            r2w, a2 = _align(d2, tgt)
            if a2 > math.radians(1.0):
                # 局部旋转作用在父骨（上臂）旋转之前的空间里
                local[la] = _m3mul(_m3mul(_m3T(r1), r2w), r1)
            else:
                a2 = 0.0
        if a1 or a2:
            log("T-pose：%s 上臂 %.1f° · 前臂 %.1f°"
                % ("左" if side == "left" else "右",
                   math.degrees(a1), math.degrees(a2)))
    if not local:
        return None

    # 付与回転：按付与率缩放付与親的局部旋转（递归处理付与链）
    memo = {}

    def local_of(i, depth=0):
        if i in memo:
            return memo[i]
        m = local.get(i)
        ir = bones[i].get("inherit_rot")
        if ir and depth < 16 and 0 <= ir[0] < len(bones) and ir[0] != i:
            src = local_of(ir[0], depth + 1)
            if src is not None:
                ax, ang = _to_axis_angle(src)
                g = _axis_angle(ax, ang * ir[1])
                m = g if m is None else _m3mul(m, g)
        memo[i] = m
        return m

    glob = {}
    for i in order:
        p = parents[i]
        pm, pt = glob.get(p, (_I3, (0.0, 0.0, 0.0)))
        lm = local_of(i)
        if lm is None:
            glob[i] = (pm, pt)
            continue
        # 绕骨骼自身位置旋转：x → R(x − p) + p，再叠父骨变换
        rp = _m3vec(lm, pos[i])
        lt = (pos[i][0] - rp[0], pos[i][1] - rp[1], pos[i][2] - rp[2])
        m = _m3mul(pm, lm)
        t = _m3vec(pm, lt)
        glob[i] = (m, (t[0] + pt[0], t[1] + pt[1], t[2] + pt[2]))
    return glob


# ------------------------------------------------------ SpringBone（物理） --


_TORSO = {"hips", "spine", "chest", "upperChest"}
_ARM_PARTS = ("Shoulder", "Arm", "Hand", "Thumb", "Index", "Middle", "Ring", "Little")
_LEG_PARTS = ("Leg", "Foot", "Toes")


def _limb_of(role):
    """humanoid 骨位 → (侧, "arm"/"leg")；不是四肢返回 None。"""
    for side in ("left", "right"):
        if role and role.startswith(side):
            if any(k in role for k in _ARM_PARTS):
                return side, "arm"
            if any(k in role for k in _LEG_PARTS):
                return side, "leg"
    return None


def _euler_yxz(r):
    """PMX 刚体旋转（弧度，DirectX 的 Yaw-Pitch-Roll）→ 3x3 矩阵 Ry·Rx·Rz。"""
    cx, sx = math.cos(r[0]), math.sin(r[0])
    cy, sy = math.cos(r[1]), math.sin(r[1])
    cz, sz = math.cos(r[2]), math.sin(r[2])
    rx = (1.0, 0.0, 0.0, 0.0, cx, -sx, 0.0, sx, cx)
    ry = (cy, 0.0, sy, 0.0, 1.0, 0.0, -sy, 0.0, cy)
    rz = (cz, -sz, 0.0, sz, cz, 0.0, 0.0, 0.0, 1.0)
    return _m3mul(_m3mul(ry, rx), rz)


def _rigid_shape(rb, glob):
    """刚体 → (中心, 半径, 半长轴向量)，都在 PMX 坐标（T-pose 之后）。

    球：半长轴为 0；胶囊：沿局部 Y，size = (半径, 圆柱长度)；
    盒：近似成沿最长轴的胶囊，半径取另外两轴半长的平均。
    """
    rot = _euler_yxz(rb["rot"])
    sx, sy, sz = rb["size"]
    shape = rb["shape"]
    if shape == 0:
        r, half = sx, (0.0, 0.0, 0.0)
    elif shape == 2:
        ax = (rot[1], rot[4], rot[7])
        r = sx
        half = tuple(ax[k] * sy * 0.5 for k in range(3))
    else:
        ext = (sx, sy, sz)
        k = max(range(3), key=lambda i: ext[i])
        others = [ext[i] for i in range(3) if i != k]
        r = sum(others) / 2.0
        ax = (rot[k], rot[3 + k], rot[6 + k])
        hl = max(0.0, ext[k] - r)
        half = tuple(ax[j] * hl for j in range(3))
    c = rb["pos"]
    g = glob.get(rb["bone"]) if glob else None
    if g:
        m, t = g
        q = _m3vec(m, c)
        c = (q[0] + t[0], q[1] + t[1], q[2] + t[2])
        half = _m3vec(m, half)
    return c, r, half


def _build_springs(model, parents, alias, human_b, glob, bpos, scale, xf,
                   bone_node, node_pos, add_node, include_lower=True,
                   stiffness_mul=1.0, gravity=0.0, gravity_lower=0.1,
                   seg_min=0.12, seg_min_lower=0.05, omega=0.15, omega_lower=2.0,
                   drag=0.6, drag_lower=0.6, hit_min_lower=0.045,
                   hair_arm_collide=True, stiff_min=2.0, limb_collide=False,
                   gravity_limb=None, omega_max=None, min_chain_len=0.05,
                   drag_limb=None, omega_max_lower=None, limb_min_len=0.12,
                   stiff_max=10.0, shrink_leg_col=True, omega_limb=0.5,
                   set_parent=None, log=None):
    """PMX 刚体物理 → VRMC_springBone（VRM 1.0）。返回扩展 dict 或 None。

    目标是「表现和原生 VRM 一致」，参数按 VRoid / VRM 官方示例模型
    （AvatarSample_B、VRM1_Constraint_Twist_Sample、Seed-san、Alicia）
    在同一套动作下的实测表现校准（2026-10，见 Document/VRM互转_使用说明.md）。

      * 挂了物理刚体（mode 1/2）的骨 = 要模拟的骨；humanoid 骨和已并入付与親的
        骨不参与。从「自己要动、父骨不动」的骨开始沿子骨走成链；分叉处子树最深的
        一支继续，其余另起一条链。
      * **抽稀**：MMD 物理链切得很细（后发 1.2 m 分 24 节），原生 VRM 的发束是
        3～7 节、每节 7～15 cm。离上一个关节不到 seg_min（头发等 12 cm）的骨不做
        关节，刚性跟随上一个关节；相邻关节随后改挂成直接父子（set_parent）——
        规范虽允许隔代，three-vrm 却按「子关节相对直接父节点的位置」取静止方向，
        隔代的链在拐弯处第一帧就会被转开。
        裙摆（下半身）只并掉 seg_min_lower（5 cm）以内的：SpringBone 只在关节点
        检测碰撞，节太稀大腿会从关节之间穿过去。
      * **硬度按「每帧回正角度」换算**：stiffness 的单位是每秒移动的米数，不随节长
        缩放，同样的值在短节上力量大得多。stiffness = ω × 节长 × 60，
        头发等 ω=0.15（原生发束约 0.1～0.25 rad/帧），裙摆 ω=2.0（密集节需要更硬
        才能撑住长裙、少穿模）。以前「按节数放大硬度」让长发的回正力是原生的
        10～20 倍，每帧拉过头再被惯性甩回，就是肉眼看到的抖动（抖动/形变比约为
        原生的 2 倍）。
      * 硬度下限 stiff_min=2.0：原生模型的硬度基本是绝对值 0.5～4、和节长无关；
        只按角度换算的话 3 cm 的胸部物理骨硬度只有 0.25，走路时整个翻过去。
        上限 stiff_max=10：翅膀、长飘带的最后一节很长，按角度换算会到 50 以上。
      * dragForce 都是 0.6。重力：头发等 0（加了会把散开的发型拉垂）；
        裙摆 gravity_lower=0.1，抬腿时裙片落回腿上而不是悬在侧面。
      * 裙摆类 = 挂在 hips 上的链，以及挂在躯干上、垂到胯部以下的链。挂在腿/脚上的
        （鞋带、腿环）是四肢配饰，和手臂上的一样处理。
      * 四肢配饰：不足 limb_min_len（12 cm）的不做 SpringBone（四肢动得快，弹簧追不上，
        只会在末端颤）；不碰自己那条手臂/腿的碰撞体；手臂上的链完全不碰撞——T-pose 下
        它们跟着手臂转了，动画放下手臂后静止方向指向身体，碰撞会把它们卡在另一侧来回翻。
        手臂上的链（袖子）硬度单独按 omega_limb=0.5 换算：手臂是全身摆得最快的部位，
        按头发的 0.15 换算时长袖跑步时末端颤动是头发的 5～8 倍，0.5 时降到 1/3～1/4，
        仍有明显摆动（0.8 就几乎僵住了）。
      * center（在 convert 里设为模型根节点）：人物整体移动时弹簧不被甩。
      * 碰撞体 ← 跟随骨骼的刚体（mode 0）；按 PMX 碰撞组/掩码决定每条链和哪些碰撞体
        碰撞。头发也碰手臂（和 VRoid 一致，放下手臂时不会穿过头发）。
        hitRadius ← 刚体半径，裙摆不小于 hit_min_lower（4.5 cm）；静止时确实贴着
        碰撞体的那几节缩到刚好不重叠（可低于下限，否则开始模拟就被推开，
        长裙从根部被撬开）。某节尾端在静止时就落在碰撞体里的，整条链不与它碰撞；
        例外是裙摆和大腿：PMX 的大腿刚体常比腿粗，贴腿的短裙静止时就在里面，
        这时给该链另配一个缩小到刚好让开的同位置碰撞体，而不是让腿完全拦不住裙片。
      * PMX 里链与链之间的横向关节（裙摆常见）在 VRM 里无法表达，舍弃。
    """
    def _log(msg, tag=None):
        if log:
            log(msg, tag)

    bones = model["bones"]
    rbs = model.get("rigid_bodies") or []
    joints = model.get("joints") or []
    nb = len(bones)
    if not rbs or not nb:
        return None
    human_set = set(human_b.values())
    vrm_of = {v: k for k, v in human_b.items()}

    dyn = {}                                  # 骨 → 刚体序号
    for ri, rb in enumerate(rbs):
        b = rb["bone"]
        if rb["mode"] in (1, 2) and 0 <= b < nb and b not in alias \
                and b not in human_set and b not in dyn:
            dyn[b] = ri
    if not dyn:
        return None

    children = [[] for _ in range(nb)]
    for i, p in enumerate(parents):
        if 0 <= p < nb:
            children[p].append(i)

    def anchor(b):
        """最近的 humanoid 祖先骨位名。"""
        p = parents[b]
        seen = set()
        while p >= 0 and p not in seen:
            if p in vrm_of:
                return vrm_of[p]
            seen.add(p)
            p = parents[p]
        return None

    depth_memo = {}

    def dyn_depth(b):
        if b not in depth_memo:
            depth_memo[b] = 1 + max([dyn_depth(c) for c in children[b]
                                     if c in dyn] or [0])
        return depth_memo[b]

    # ---- 拆链
    hips_y = bpos[human_b["hips"]][1] if "hips" in human_b else None
    roots = [b for b in range(nb) if b in dyn and parents[b] not in dyn]
    pending = list(roots)
    chains = []                               # [(骨列表, 类别)]
    used = set()
    skipped_lower = 0
    while pending:
        r = pending.pop(0)
        if r in used:
            continue
        cat = anchor(r)
        # 裙摆类只算挂在 hips 上的链（以及下面挂在躯干上、垂到胯部以下的）。
        # 挂在腿/脚上的是配饰（鞋带、腿环、脚踝蝴蝶结），不能按裙摆的高硬度处理——
        # 它们跟着动得最快的腿脚走，实测开拓者的鞋带跑步时末端颤动是全身最高
        lower = cat == "hips"
        chain = [r]
        used.add(r)
        cur = r
        while True:
            kids = sorted([c for c in children[cur] if c in dyn and c not in used],
                          key=lambda c: -dyn_depth(c))
            if not kids:
                break
            pending.extend(kids[1:])
            cur = kids[0]
            chain.append(cur)
            used.add(cur)
        # 挂在躯干上、但垂到胯部以下的链（侧面裙片、腰后飘带）也按裙摆处理：
        # 它们和腿有碰撞关系，需要密集关节、重力和足够的碰撞半径。
        # 以前只看挂点，挂在上半身1 上的裙片被当成头发（稀、软、无重力），
        # 抬腿时被大腿横推出去悬在侧面
        if not lower and cat in _TORSO and hips_y is not None \
                and min(bpos[b][1] for b in chain) < hips_y:
            lower = True
        if lower and not include_lower:
            skipped_lower += 1
            continue
        chains.append((chain, lower))

    # ---- 关节参数
    joint_of = {}                             # 刚体 → 连接父刚体的 PMX 关节
    for j in joints:
        a, b = j["rb_a"], j["rb_b"]
        if 0 <= b < len(rbs) and 0 <= a < len(rbs):
            ba, bb = rbs[a]["bone"], rbs[b]["bone"]
            if 0 <= bb < nb and parents[bb] == ba:
                joint_of.setdefault(b, j)
    for j in joints:                          # 没有严格父子关系的就退而求其次
        b = j["rb_b"]
        if 0 <= b < len(rbs):
            joint_of.setdefault(b, j)

    def _stiff(w, length, lower=False):
        s_ = max(w * length * 60.0, stiff_min)
        cap = omega_max_lower if lower else omega_max
        if cap is not None:
            s_ = min(s_, cap * length * 60.0)
        # 绝对上限：裙摆的回正角度是按 5 cm 左右的密集短节定的，遇到很长的一节
        # （翅膀、长飘带的最后一节）会算出 50 以上，一样会每帧拉过头
        return min(s_, stiff_max)

    def seg_params(seg, length, lower, arm):
        """一节（seg = 这一节包含的骨，第一个是关节骨）的 SpringBone 参数。
        arm = 挂在手臂上的链（袖子、手肘吊坠），可单独设 omega/gravity/drag_limb。"""
        w = omega_lower if lower else omega
        if arm and omega_limb is not None:
            w = omega_limb
        g = gravity_lower if lower else gravity
        if arm and gravity_limb is not None:
            g = gravity_limb
        d = drag_lower if lower else drag
        if arm and drag_limb is not None:
            d = drag_limb
        rad = max(_rigid_shape(rbs[dyn[b]], None)[1] for b in seg) * scale
        rad = min(0.05, max(hit_min_lower if lower else 0.01, rad))
        return {"hitRadius": round(rad, 5),
                "stiffness": round(_stiff(w, length, lower) * stiffness_mul, 4),
                "gravityPower": g,
                "gravityDir": [0.0, -1.0, 0.0],
                "dragForce": d}

    def tail_pos(b):
        """链尾没有可用子骨时，造一个 tail 的位置（PMX 坐标）。"""
        bb = bones[b]
        lin = glob[b][0] if glob else _I3
        if bb["tail_kind"] == 0:
            off = bb["tail"]
            if sum(x * x for x in off) > 1e-8:
                o = _m3vec(lin, off)
                return tuple(bpos[b][k] + o[k] for k in range(3))
        elif 0 <= bb["tail"] < nb and bb["tail"] != b:
            q = bpos[bb["tail"]]
            if sum((q[k] - bpos[b][k]) ** 2 for k in range(3)) > 1e-8:
                return q
        p = parents[b]
        if p >= 0:
            d = [bpos[b][k] - bpos[p][k] for k in range(3)]
            if sum(x * x for x in d) > 1e-8:
                return tuple(bpos[b][k] + d[k] * 0.5 for k in range(3))
        return (bpos[b][0], bpos[b][1] - 0.5, bpos[b][2])

    # ---- 碰撞体（跟随骨骼的刚体）
    colliders = []
    col_meta = []                             # (group, mask)
    col_world = []                            # (端点 a, 端点 b 或 None, 半径)，VRM 坐标
    col_role = []                             # 碰撞体所在骨的 humanoid 骨位（没有为空）
    for rb in rbs:
        b = rb["bone"]
        if rb["mode"] != 0 or not (0 <= b < nb):
            continue
        b = alias.get(b, b)                   # 挂在 足D 上的刚体改挂到 足
        node = bone_node(b)
        if node is None:
            continue
        c, r, half = _rigid_shape(rb, glob)
        base = node_pos(node)
        if sum(x * x for x in half) < 1e-12:
            p0 = xf(c)
            shape = {"sphere": {"offset": [p0[k] - base[k] for k in range(3)],
                                "radius": r * scale}}
            col_world.append((p0, None, r * scale))
        else:
            p0 = xf(tuple(c[k] - half[k] for k in range(3)))
            p1 = xf(tuple(c[k] + half[k] for k in range(3)))
            shape = {"capsule": {"offset": [p0[k] - base[k] for k in range(3)],
                                 "radius": r * scale,
                                 "tail": [p1[k] - base[k] for k in range(3)]}}
            col_world.append((p0, p1, r * scale))
        colliders.append({"node": node, "shape": shape})
        col_meta.append((rb["group"], rb["mask"]))
        col_role.append(vrm_of.get(b, ""))

    group_cache = {}
    collider_groups = []

    n_pmx_cols = len(colliders)

    def mask_ids(rb):
        """按 PMX 碰撞组/掩码，该动态刚体能碰到的碰撞体（双方掩码都允许）。"""
        g, m = rb["group"], rb["mask"]
        return [i for i, (cg, cm) in enumerate(col_meta[:n_pmx_cols])
                if (m >> cg) & 1 and (cm >> g) & 1]

    shrunk_cache = {}

    def shrunk_collider(ci, r):
        """碰撞体 ci 的缩小半径副本（同节点、同位置），相近半径的共用一个。
        半径向下取到 5 mm 的整数倍：向上取整会吃掉让开静止裙片的余量。"""
        key = (ci, math.floor(r * 200.0) / 200.0)
        if key not in shrunk_cache:
            shape = {k: dict(v) for k, v in colliders[ci]["shape"].items()}
            for v in shape.values():
                v["radius"] = key[1]
            colliders.append({"node": colliders[ci]["node"], "shape": shape})
            col_meta.append(col_meta[ci])
            col_world.append(col_world[ci][:2] + (key[1],))
            col_role.append(col_role[ci])
            shrunk_cache[key] = len(colliders) - 1
        return shrunk_cache[key]

    def clearance(pt, ci):
        """点到碰撞体表面的距离（负数 = 在碰撞体内部）。"""
        a, b, r = col_world[ci]
        if b is None:
            q = a
        else:
            ab = [b[k] - a[k] for k in range(3)]
            l2 = sum(x * x for x in ab) or 1e-12
            t = sum((pt[k] - a[k]) * ab[k] for k in range(3)) / l2
            t = min(1.0, max(0.0, t))
            q = [a[k] + ab[k] * t for k in range(3)]
        return math.sqrt(sum((pt[k] - q[k]) ** 2 for k in range(3))) - r

    def groups_for(ids):
        """碰撞体集合 → colliderGroups 索引列表（相同集合共用一个组）。"""
        ids = tuple(sorted(ids))
        if not ids:
            return []
        if ids not in group_cache:
            group_cache[ids] = len(collider_groups)
            collider_groups.append({"name": "pmx_collision_%d" % len(collider_groups),
                                    "colliders": list(ids)})
        return [group_cache[ids]]

    # ---- 写 springs
    springs = []
    n_virtual = 0
    n_dropped = [0]
    n_shrunk = [0]
    n_tiny = [0]
    n_joints_mmd = n_joints_vrm = n_lower = 0
    for chain, lower in chains:
        last = chain[-1]
        real = [c for c in children[last]
                if c not in dyn and c not in human_set and c not in alias]
        tpos = node_pos(bone_node(real[0])) if real else xf(tail_pos(last))
        pos = {b: xf(bpos[b]) for b in chain}
        anc = anchor(chain[0]) or ""
        limb = _limb_of(anc)
        # 过短的链（3 cm 的胸部物理骨之类）只会高频颤动、看不出有意义的摆动，不做 SpringBone。
        # 挂在手臂/腿上的短配饰（手肘吊坠、鞋带、脚踝蝴蝶结）门槛更高：四肢动得快，
        # 弹簧追不上就在末端来回颤（实测是全身颤动最大的部位），不如跟着骨骼刚性移动
        length = math.dist(pos[last], tpos) + sum(
            math.dist(pos[a], pos[b]) for a, b in zip(chain, chain[1:]))
        if length < (max(min_chain_len, limb_min_len) if limb else min_chain_len):
            n_tiny[0] += 1
            continue
        if real:
            tnode = bone_node(real[0])
        else:
            tnode = add_node((bones[last]["name"] or "bone") + "_tail", bone_node(last), tpos)
            n_virtual += 1
        # 抽稀：离上一个关节不到 seg_min 的骨不做关节（刚性跟随上一个关节）
        smin = seg_min_lower if lower else seg_min
        kept = [chain[0]]
        for b in chain[1:]:
            if math.dist(pos[b], pos[kept[-1]]) >= smin:
                kept.append(b)
        if len(kept) > 1 and math.dist(pos[kept[-1]], tpos) < smin * 0.5:
            kept.pop()                        # 最后一节太短：并进上一节
        n_joints_mmd += len(chain)
        n_joints_vrm += len(kept)
        idx = [chain.index(b) for b in kept] + [len(chain)]
        arm = bool(limb) and limb[1] == "arm"
        pts = [pos[b] for b in kept] + [tpos]
        js = []
        for i, b in enumerate(kept):
            jt = seg_params(chain[idx[i]:idx[i + 1]], math.dist(pts[i], pts[i + 1]), lower, arm)
            jt["node"] = bone_node(b)
            js.append(jt)
        tj = dict(js[-1])
        tj["node"] = tnode
        js.append(tj)
        # 相邻关节改成直接父子（世界位置不变，被跳过的中间骨仍挂在原处、刚性跟随）。
        # 规范允许关节隔代，但 three-vrm 取「子关节相对它直接父节点的位置」当静止方向，
        # 隔代时链在拐弯处（前裙片转到侧裙片）第一帧就被转开，实测末端偏 24 cm
        if set_parent is not None:
            for a_, b_ in zip(js, js[1:]):
                set_parent(b_["node"], a_["node"])
        # 碰撞体：PMX 掩码允许的。挂在四肢上的链不碰同一条手臂/腿的碰撞体（本来就跟着它动，
        # 碰撞只会在手臂快速转动时——比如动画第一帧从 T-pose 放下——把链甩到碰撞体另一侧
        # 卡住，表现为配饰整个翻过去、一直抖）；手臂上的链默认完全不碰撞（limb_collide）。
        on_head = anc in ("head", "neck", "jaw", "leftEye", "rightEye")
        ids = []
        for ci in ([] if arm and not limb_collide else mask_ids(rbs[dyn[chain[0]]])):
            role = col_role[ci]
            if limb and _limb_of(role) == limb:
                continue
            if on_head and not hair_arm_collide and (_limb_of(role) or ("", ""))[1] == "arm":
                continue
            # 只看各节尾端（pts[1:]）：SpringBone 用「这一节的 hitRadius、在尾端位置」检测，
            # 链根钉在父骨上，埋在碰撞体里也不会被推
            minc = min(clearance(pt, ci) for pt in pts[1:])
            if minc > 0.0:
                ids.append(ci)
                continue
            # 贴腿的短裙：PMX 的大腿刚体常比腿粗，静止时裙片就在里面。整个舍弃的话
            # 这条腿完全拦不住裙片（实测穗穗走路时 4.2% 的裙摆关节进了腿里，缩小后 1.7%），
            # 所以给这条链另配一个缩小到刚好让开静止裙片的同位置碰撞体
            r0 = col_world[ci][2]
            r1 = r0 + minc - 0.005
            if shrink_leg_col and lower and "UpperLeg" in role and r1 >= max(0.02, r0 * 0.4):
                ids.append(shrunk_collider(ci, r1))
            else:
                n_dropped[0] += 1
        # 只缩静止时确实贴着碰撞体的那几节（可以低于下限）；其余节保持足够的
        # 碰撞半径。不能用「一律不低于下限」——贴着胯部的那几节会在开始模拟时
        # 被推开，长裙从根部被撬开，末端偏出 30 cm 以上
        for i, jt in enumerate(js[:-1]):
            for ci in ids:
                c = clearance(pts[i + 1], ci)
                if c < jt["hitRadius"]:
                    jt["hitRadius"] = round(max(0.002, c * 0.9), 5)
                    n_shrunk[0] += 1
        js[-1]["hitRadius"] = js[-2]["hitRadius"]
        n_lower += lower
        sp = {"name": bones[chain[0]]["name"] or ("spring%d" % len(springs)),
              "joints": js}
        cg = groups_for(ids)
        if cg:
            sp["colliderGroups"] = cg
        springs.append(sp)

    if not springs:
        if skipped_lower:
            _log("SpringBone：%d 条下半身物理链按设置跳过，没有可写的链"
                 % skipped_lower)
        return None
    _log("SpringBone：%d 条链（裙摆类 %d、头发等 %d）· 碰撞体 %d · 碰撞组 %d"
         % (len(springs), n_lower, len(springs) - n_lower, len(colliders),
            len(collider_groups)))
    if skipped_lower:
        _log("SpringBone：按设置跳过 %d 条下半身物理链（裙摆等）" % skipped_lower)
    if n_tiny[0]:
        _log("SpringBone：%d 条短链不转换（不足 %.0f cm，四肢上的不足 %.0f cm）：只会高频颤动"
             % (n_tiny[0], min_chain_len * 100, limb_min_len * 100))
    _log("SpringBone：MMD 物理骨 %d 根 → SpringBone 关节 %d 个（头发等每节至少 %.0f cm、"
         "裙摆 %.0f cm，中间的骨刚性跟随）"
         % (n_joints_mmd, n_joints_vrm, seg_min * 100, seg_min_lower * 100))
    if n_virtual:
        _log("SpringBone：%d 条链尾没有子骨，已补 tail 节点" % n_virtual)
    if n_dropped[0] or n_shrunk[0]:
        _log("SpringBone：静止姿势下与碰撞体重叠——%d 处取消该链与碰撞体的碰撞、"
             "%d 个节点缩小碰撞半径" % (n_dropped[0], n_shrunk[0]))
    cross = 0
    for j in joints:
        a, b = j["rb_a"], j["rb_b"]
        if 0 <= a < len(rbs) and 0 <= b < len(rbs):
            ba, bb = rbs[a]["bone"], rbs[b]["bone"]
            if ba in dyn and bb in dyn and 0 <= ba < nb and 0 <= bb < nb \
                    and parents[bb] != ba and parents[ba] != bb:
                cross += 1
    if cross:
        _log("提示：%d 个链间横向关节（裙摆等）在 SpringBone 里无法表达，已舍弃"
             % cross, "warn")
    ext = {"specVersion": "1.0", "springs": springs}
    if colliders:
        ext["colliders"] = colliders
    if collider_groups:
        ext["colliderGroups"] = collider_groups
    return ext


def _topo_bones(parents):
    """把骨骼重排成「父在前」，返回 (新序号→原序号, 原序号→新序号)。

    用「祖先链深度」排序：父节点的深度永远比子节点小 1，排序后父必在前。
    比递归/DFS 稳，遇到坏数据（成环）也不会死循环。
    """
    n = len(parents)
    depth = [0] * n
    for i in range(n):
        seen = set()
        d = 0
        k = i
        while d <= n + 5:
            p = parents[k]
            if not (0 <= p < n) or p == k or p in seen:
                break
            seen.add(p)
            d += 1
            k = p
        depth[i] = d
    order = sorted(range(n), key=lambda i: (depth[i], i))
    remap = {old: new for new, old in enumerate(order)}
    return order, remap


def _flatten_morphs(morphs):
    """把顶点表情和材质表情（含グループ展开）整理成
    [(名字, {顶点: (dx,dy,dz)}, [(材质, 运算, 28 个值, 比率)])]。

    材质表情（kind 8）在 MMD 里常用来显示/隐藏部件（脸红、换道具），
    VRM 对应 expression 的 materialColorBinds。材质号 -1 表示所有材质。
    """
    def resolve(i, seen):
        if i in seen:
            return {}, []
        seen = seen | {i}
        mo = morphs[i]
        if mo["kind"] == 1:
            d = {}
            for vi, off in mo["offsets"]:
                if abs(off[0]) + abs(off[1]) + abs(off[2]) > 1e-9:
                    d[vi] = (off[0], off[1], off[2])
            return d, []
        if mo["kind"] == 8:
            return {}, [(mi, op, vals, 1.0) for mi, op, vals in mo["offsets"]]
        if mo["kind"] == 0:
            out, mats = {}, []
            for sub, rate in mo["offsets"]:
                if not (0 <= sub < len(morphs)) or abs(rate) < 1e-9:
                    continue
                sd, sm = resolve(sub, seen)
                for vi, d in sd.items():
                    ox, oy, oz = out.get(vi, (0.0, 0.0, 0.0))
                    out[vi] = (ox + d[0] * rate, oy + d[1] * rate,
                               oz + d[2] * rate)
                mats.extend((mi, op, vals, r * rate) for mi, op, vals, r in sm)
            return out, mats
        return {}, []

    out = []
    for i, mo in enumerate(morphs):
        if mo["kind"] not in (0, 1, 8):
            continue
        d, m = resolve(i, frozenset())
        if d or m:
            out.append((mo["name"], d, m))
    return out


def _lin(c):
    """sRGB（gamma）分量 → 线性分量。

    MMD 在 gamma 空间里算颜色（材质色、toon 贴图都是 sRGB 值，直接相乘）；glTF 的
    baseColorFactor 和 VRM 1.0 MToon 的各个颜色因子规定是线性值，引擎会按线性来乘
    贴图。以前把 gamma 值原样写进去：阴影色 0.8 实际显示成 sRGB 0.9，只暗 10%，
    在游戏的环境光下基本看不见（原生 VRoid 皮肤的阴影色 0.x 写 (0.97, 0.81, 0.86)，
    读进来是线性 (0.93, 0.63, 0.72)）。gamma 空间的乘法 a·b 对应线性的 lin(a)·lin(b)，
    所以换算后效果和 MMD 一致。VRM 0.x 的 materialProperties 本来就是 gamma 值，不用转。
    """
    c = max(0.0, min(1.0, c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lin_rgb(rgb):
    """颜色列表的 RGB 分量转线性，第 4 个（alpha）原样保留。"""
    return [round(_lin(v), 5) if i < 3 else v for i, v in enumerate(rgb)]


def _mmd_color(diffuse, ambient):
    """MMD 实际显示的材质颜色（RGBA）。

    MMD 默认着色器：颜色 = saturate(漫反射 × 光源色 + 环境色)，光源色默认
    154/255 ≈ 0.6；alpha 取漫反射 alpha。常见的「漫反射 1、环境色 0.5」算出来
    就是白色，但有的材质靠环境色调色（比如脸红：漫反射白、环境色偏红），
    只用漫反射会把颜色丢掉。
    """
    return [min(1.0, max(0.0, diffuse[k] * 0.6 + ambient[k]))
            for k in range(3)] + [diffuse[3]]


# MMD 共享 toon（toon01～toon06.bmp）的渐变：上半白、v=0.5 处硬切到下半的颜色。
# 取自 three.js MMDLoader 内嵌的 DEFAULT_TOON_TEXTURES；它的 toon07～10 是全白的
# 占位图，不可信，这几张当作「不知道」走默认阴影
_SHARED_TOON = {
    0: ((255, 255, 255), (205, 205, 205)),
    1: ((255, 255, 255), (245, 225, 225)),
    2: ((255, 255, 255), (154, 154, 154)),
    3: ((255, 255, 255), (248, 239, 235)),
    4: ((255, 255, 255), (254, 232, 223)),
    5: ((255, 237, 97), (195, 172, 3)),
}


def _toon_column(rows, w, h):
    """toon 贴图 → 从上（v=0，正对光）到下（v=1，背光 / 自阴影）的平均颜色（0～1）。"""
    xs = sorted({0, w // 4, w // 2, (3 * w) // 4, w - 1})
    return [tuple(sum(rows[y][x][k] for x in xs) / (255.0 * len(xs)) for k in range(3))
            for y in range(h)]


def _toon_shading(col):
    """MMD toon 渐变 → MToon 1.0 的 (亮部色, 阴影色, shadingShift, shadingToony)。

    MMD：颜色 ×= toon(v)，v = 0.5 − 0.5·N·L；自阴影（投影）里取最下端 toon(1)。
    MToon 1.0：shading = linearstep(−1+toony, 1−toony, N·L + shift) × 投影，
    颜色 = lerp(阴影色, 亮部色, shading)。所以
      亮部色 = toon 最上端，阴影色 = 最下端（投影里正好也是它），
      明暗交界在亮度降到一半的 v_mid 处：N·L = 1 − 2·v_mid → shift = 2·v_mid − 1，
      过渡宽度 Δv（亮度 10%→90%）对应 N·L 的 2·Δv → toony = 1 − Δv。
    星铁 / 鸣潮的皮肤 toon 在 v≈0.75 以后才变暗，换算出来 shift≈+0.6：脸上基本全亮、
    只有投影是粉色——和原生 VRoid 皮肤（0.x ShadeShift −1 / Toony 1）的效果一致。
    渐变几乎没有明暗差（纯白 toon）时阴影色 = 亮部色：MMD 里这种材质本来就没有阴影。
    """
    if not col:
        return None
    lum = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in col]
    top, bot = lum[0], lum[-1]
    if top - bot < 0.02:
        return col[0], col[0], 0.0, 0.9
    n = len(col)

    def first_below(level):
        for i, v in enumerate(lum):
            if v <= level:
                return i / max(1, n - 1)
        return 1.0

    v_mid = first_below(top - 0.5 * (top - bot))
    dv = first_below(top - 0.9 * (top - bot)) - first_below(top - 0.1 * (top - bot))
    shift = max(-1.0, min(1.0, 2.0 * v_mid - 1.0))
    toony = max(0.0, min(1.0, 1.0 - dv))
    return col[0], col[-1], shift, toony


def _morph_material(mat, ops, mi):
    """把材质表情作用到材质 mi 上，返回权重为 1 时的 (漫反射 RGBA, 环境色 RGB)。

    PMX 材质表情：运算 0 = 乘算（基础 × 值），1 = 加算（基础 + 值）；
    28 个值里 [0:4] 是漫反射 RGBA、[8:11] 是环境色。グループ里的比率按
    「基础 → 目标」线性插值（VRM 的 materialColorBinds 也是按权重插值）。
    没有作用到这个材质返回 None。
    """
    dif = list(mat["diffuse"])
    amb = list(mat["ambient"])
    hit = False
    for m, op, vals, rate in ops:
        if m != mi and m != -1:
            continue
        hit = True
        for cur, off, n in ((dif, 0, 4), (amb, 8, 3)):
            for k in range(n):
                v = vals[off + k]
                tgt = cur[k] * v if op == 0 else cur[k] + v
                cur[k] = min(1.0, max(0.0, cur[k] + (tgt - cur[k]) * rate))
    return (dif, amb) if hit else None


def _degmap(xr, yr):
    return {"curve": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 1.0, 0.0],
            "xRange": xr, "yRange": yr}


# ---------------------------------------------------------------- 主流程 --
def convert(pmx_path, vrm_path, spec="1.0", scale_mode="auto",
            rotate="auto",             reverse_winding="auto", meta=None,
            max_morphs=None, log=None, force_double_sided=False,
            tpose=True, outline=False, sparse_morphs=True,
            spring=True, spring_skirt=True, spring_stiffness=1.0,
            spring_gravity=0.0, spring_gravity_skirt=0.1, spring_center=True,
            spring_opts=None):
    # 确保输出目录存在（CLI 直接调用时尤其容易漏建）
    _odir = os.path.dirname(os.path.abspath(vrm_path))
    try:
        os.makedirs(_odir, exist_ok=True)
    except OSError as e:
        raise OSError("无法创建输出目录 %s：%s" % (_odir, e))

    """返回统计 dict。log(msg, tag=None) 用于输出进度。

    reverse_winding:
        "auto"（默认）——用「几何面法线 vs 顶点法线」投票自动判定；
        True / False   ——手动强制反转 / 不反转。

    手性说明（2026-09-18 重写）：PMX（MMD / DirectX 左手系）数值上
    脸朝 -Z、左手在 +X；同一组数字按 glTF 右手系直接解释是镜像人形，
    必须做一次镜面反射（0.x 取反 x；1.0 取反 z）。反射会把
    「从外面看逆时针」颠倒成顺时针，所以 auto 绕序判定（用反射后的
    数据投票）正常会判「反转」。以前不做反射（或用 y180 纯旋转，
    旋转改不了手性）会让转出的模型左右翻转。
    """

    def _log(msg, tag=None):
        if log is not None:
            try:
                log(msg, tag)
            except TypeError:
                log(msg)

    model = pmxio.read_pmx(pmx_path)
    base_dir = os.path.dirname(os.path.abspath(pmx_path))
    verts = model["vertices"]
    bones = model["bones"]
    faces = model["faces"]
    materials = model["materials"]
    is1 = str(spec).startswith("1")

    # ---- 缩放
    if scale_mode in ("auto", "mmd", None, ""):
        ys = [v["pos"][1] for v in verts] or [0.0]
        height = max(ys) - min(ys)
        scale = (1.6 / height) if height > 1e-6 else 0.08
    else:
        scale = float(scale_mode)

    # ---- 手性修正（2026-09-18 重写，别改回「绕 Y 旋转 180°」）
    # PMX（左手系）数值：脸 -Z、左手 +X（实测小食Taberu：
    # 両目 Z=-0.75，左腕 X=+1.34 / 右腕 X=-1.34）。
    # glTF（右手系）里合法人形：脸 -Z ⇒ 左手 -X；脸 +Z ⇒ 左手 +X。
    # 所以必须反射一个轴：0.x（脸 -Z）取反 x；1.0（脸 +Z）取反 z。
    # y180 是纯旋转，改不了手性 → 转出的 VRM 左右翻转（实测 Blender 可见）。
    # rotate="none" 仅用于调试（原样输出，左右是反的）。
    if rotate in ("auto", "y180", None, ""):      # y180 为旧值，视同 auto
        mx, mz = (1.0, -1.0) if is1 else (-1.0, 1.0)
    else:                                          # "none"
        mx, mz = 1.0, 1.0

    def xf(p):
        x, y, z = p
        return (x * mx * scale, y * scale, z * mz * scale)

    def xf_dir(p):
        x, y, z = p
        return (x * mx, y, z * mz)

    def unit(left_amount, up_amount, front_amount):
        if is1:                       # VRM 1.0：左 +X、前 +Z
            return (left_amount, up_amount, front_amount)
        return (-left_amount, up_amount, -front_amount)

    _log("PMX：顶点 %d · 三角面 %d · 骨骼 %d · 材质 %d · 表情 %d"
         % (len(verts), len(faces) // 3, len(bones), len(materials),
            len(model["morphs"])))

    # ---- 骨骼预处理（都在 PMX 坐标里做，最后统一过 xf）
    # 1) 付与 100% 的同位置骨（足D 等）并入付与親：权重与子骨都转过去
    # 2) humanoid 映射 + 层级修正（上半身 挂到 下半身 下等）
    # 3) A-pose → T-pose：重摆手臂骨与顶点
    nb = len(bones)
    parents = _clean_parents(bones)
    alias = _append_aliases(bones, parents, eps=0.01)
    if alias:
        for i, dst in alias.items():
            for c in range(nb):
                if parents[c] == i:
                    _reparent(parents, c, dst)
        _log("付与骨合并：%d 根（%s）→ 权重并入付与親"
             % (len(alias), "、".join(bones[i]["name"] for i in
                                     list(alias)[:6]) +
                ("…" if len(alias) > 6 else "")))
    required = REQUIRED_1_0 if is1 else REQUIRED_0X
    human_b = _map_humanoid(bones, is1, alias)
    _fix_humanoid_hierarchy(human_b, parents, bones, set(required), _log)
    order, remap = _topo_bones(parents)

    # 逐顶点权重：[(PMX 骨, 权重)]，已归一、已按别名合并、最多 4 个
    def vertex_weights(v):
        wt = v["wtype"]
        bs = [x for x in v["wbones"] if 0 <= x < nb]
        if not bs:
            bs = [0]
        if wt == 0:
            wl = [(bs[0], 1.0)]
        elif wt in (1, 3):           # BDEF2 / SDEF（SDEF 退化成线性混合）
            w0 = v["wweights"][0] if v["wweights"] else 1.0
            second = bs[1] if len(bs) > 1 else bs[0]
            wl = [(bs[0], w0), (second, 1.0 - w0)]
        else:                        # BDEF4 / QDEF
            ws = list(v["wweights"]) + [0.0] * 4
            wl = [(bs[k] if k < len(bs) else bs[0], ws[k]) for k in range(4)]
            if sum(x[1] for x in wl) <= 1e-9:
                wl = [(bs[0], 1.0)]
        acc = {}
        for b_, w in wl:
            b_ = alias.get(b_, b_)
            acc[b_] = acc.get(b_, 0.0) + max(0.0, w)
        wl = sorted(acc.items(), key=lambda x: -x[1])[:4]
        tot = sum(w for _, w in wl)
        if tot <= 1e-9:
            return [(wl[0][0] if wl else bs[0], 1.0)]
        return [(b_, w / tot) for b_, w in wl]

    vweights = [vertex_weights(v) for v in verts] if nb else \
        [[(0, 1.0)] for _ in verts]

    vpos = [v["pos"] for v in verts]
    vnrm = [v["normal"] for v in verts]
    vmat = None                      # 逐顶点混合旋转（T-pose 后给表情偏移用）
    bpos = [b["pos"] for b in bones]
    glob = _tpose_globals(bones, parents, order, human_b, _log) \
        if (tpose and nb) else None
    if glob:
        bpos = []
        for i in range(nb):
            m, t = glob[i]
            q = _m3vec(m, bones[i]["pos"])
            bpos.append((q[0] + t[0], q[1] + t[1], q[2] + t[2]))
        vpos, vnrm, vmat = [], [], [None] * len(verts)
        for vi, v in enumerate(verts):
            wl = vweights[vi]
            if all(glob[b_][0] is _I3 for b_, _ in wl):
                vpos.append(v["pos"])
                vnrm.append(v["normal"])
                continue
            m = [0.0] * 9
            t = [0.0, 0.0, 0.0]
            for b_, w in wl:
                gm, gt = glob[b_]
                for k in range(9):
                    m[k] += gm[k] * w
                for k in range(3):
                    t[k] += gt[k] * w
            q = _m3vec(m, v["pos"])
            vpos.append((q[0] + t[0], q[1] + t[1], q[2] + t[2]))
            vnrm.append(_m3vec(m, v["normal"]))
            vmat[vi] = m

    # ---- 缠绕顺序自动判定：必须用「镜像后」的数据投票。
    # 镜像反射会把「从外面看逆时针」颠倒成顺时针，所以正常情况下
    # auto 在这里会判定为「需要反转」——这不是回到老 bug，
    # 老 bug 是「没有反射时硬编码反转」。
    if reverse_winding in ("auto", None, ""):
        flip, ag, dis = pmxio.detect_winding(
            [xf(p) for p in vpos], [xf_dir(n) for n in vnrm], faces)
        reverse_winding = bool(flip)
        _log("绕序自动判定（手性修正后）：与法线同向 %d 面 / 反向 %d 面 → %s"
             % (ag, dis, "需要反转" if flip else "保持不变"))
    else:
        reverse_winding = bool(reverse_winding)

    _log("缩放 %.5f（MMD 单位 → 米）· 手性修正 %s · 绕序%s"
         % (scale,
            ("z 取反" if is1 else "x 取反") if (mx, mz) != (1.0, 1.0) else "无",
            "反转" if reverse_winding else "保持不变"))

    # ---- 骨骼 → glTF 节点
    # 先只记「父节点 + 世界坐标」，最后统一算 children / translation，
    # 这样补占位骨时可以随意改挂节点。
    node_names = []
    node_parent = []
    wpos = []
    node_of_bone = [0] * nb
    for new_i, old_i in enumerate(order):
        par = parents[old_i]
        node_of_bone[new_i] = len(node_names)
        node_names.append(bones[old_i]["name"] or ("bone%d" % old_i))
        node_parent.append(node_of_bone[remap[par]]
                           if (par >= 0 and remap[par] < new_i) else None)
        wpos.append(xf(bpos[old_i]))

    def bone_node(pmx_index):
        if 0 <= pmx_index < nb:
            return node_of_bone[remap[pmx_index]]
        return None

    if not bones:                                   # 极端情况：没有骨骼
        node_names.append("root_bone")
        node_parent.append(None)
        wpos.append((0.0, 0.0, 0.0))

    def node_is_ancestor(a, b):
        seen = set()
        while b is not None and b not in seen:
            if b == a:
                return True
            seen.add(b)
            b = node_parent[b]
        return False

    # ---- humanoid（骨位 → 节点）+ 缺失必需骨位补占位
    human = {k: bone_node(v) for k, v in human_b.items()}

    def ensure(name):
        if name in human:
            return human[name]
        req = set(required)
        pname = _vrm_anchor(name, set(human) | req)
        pnode = ensure(pname) if pname else None
        # 已有的、规范上应挂在本骨位下的 humanoid 骨（比如缺 spine 时的 chest）
        kids = [human[h] for h in list(human)
                if _vrm_anchor(h, set(human) | {name}) == name
                and not (pnode is not None and
                         node_is_ancestor(human[h], pnode))]
        if kids:
            kc = [sum(wpos[k][j] for k in kids) / len(kids) for j in range(3)]
            if pnode is not None:
                pos = tuple((wpos[pnode][j] + kc[j]) * 0.5 for j in range(3))
            else:
                pos = tuple(kc)
        else:
            off = _SYNTH.get(name, (0.0, 0.05, 0.0))
            d = unit(off[0], off[1], off[2])
            if pnode is None:
                pos = (0.0, _HIPS_Y, 0.0) if name == "hips" else d
            else:
                pos = tuple(wpos[pnode][j] + d[j] for j in range(3))
        idx = len(node_names)
        node_names.append("vrm_" + name)
        node_parent.append(pnode)
        wpos.append(pos)
        for k in kids:
            node_parent[k] = idx
        human[name] = idx
        return idx

    for r in required:
        if r not in human:
            ensure(r)
            _log("提示：模型缺少 %s，已自动生成占位骨骼" % r, "warn")

    # ---- PMX 物理 → SpringBone（只写 VRM 1.0 的 VRMC_springBone）
    spring_ext = None
    if spring and is1:
        name_set = set(node_names)

        def add_node(name, parent, pos):
            nm, k = name, 2
            while nm in name_set:
                nm = "%s_%d" % (name, k)
                k += 1
            name_set.add(nm)
            node_names.append(nm)
            node_parent.append(parent)
            wpos.append(pos)
            return len(node_names) - 1

        def set_parent(node, parent):
            node_parent[node] = parent

        spring_ext = _build_springs(
            model, parents, alias, human_b, glob, bpos, scale, xf,
            bone_node, lambda n: wpos[n], add_node,
            include_lower=spring_skirt, stiffness_mul=spring_stiffness,
            gravity=spring_gravity, gravity_lower=spring_gravity_skirt,
            set_parent=set_parent, log=_log,
            **(spring_opts or {}))
    elif spring and model.get("rigid_bodies"):
        _log("提示：VRM 0.x 暂不转换 SpringBone（物理），头发/裙摆不会摆动", "warn")

    nodes = []
    top_nodes = []
    for i, nm in enumerate(node_names):
        p = node_parent[i]
        base = wpos[p] if p is not None else (0.0, 0.0, 0.0)
        nodes.append({"name": nm,
                      "translation": [wpos[i][j] - base[j] for j in range(3)]})
    for i, p in enumerate(node_parent):
        if p is not None:
            nodes[p].setdefault("children", []).append(i)
        else:
            top_nodes.append(i)

    # ---- 逐材质构建网格
    gb = vrmio.GLBBuilder()
    gb.samplers.append(vrmio.default_sampler())
    img_cache = {}
    tex_alpha = {}                   # PMX 贴图 → png_alpha_stats 结果
    mat_gidx = {}                    # PMX 材质 → glTF 材质索引（空材质会被跳过）
    gltf_materials = []
    vrm_material_props = []
    prims = []

    def resolve_tex(idx):
        if idx is None or idx < 0 or idx >= len(model["textures"]):
            return -1
        if idx in img_cache:
            return img_cache[idx]
        rel = model["textures"][idx].replace("\\", "/")
        data = None
        for c in (os.path.join(base_dir, rel),
                  os.path.join(base_dir, os.path.basename(rel))):
            if os.path.isfile(c):
                data, mime = vrmio.load_texture(c)
                if data:
                    break
        if not data:
            img_cache[idx] = -1
            return -1
        ti = gb.add_texture(gb.add_image(data, mime))
        img_cache[idx] = ti
        tex_alpha[idx] = vrmio.png_alpha_stats(data)
        return ti

    toon_cache = {}

    def toon_params(mat):
        """材质的 toon 渐变 → (亮部色, 阴影色, shift, toony)（见 _toon_shading）；
        没有 toon、共享 toon07～10、贴图找不到或解不了时返回 None（走默认阴影）。"""
        if mat["toon_flag"]:
            key = ("shared", mat["toon"])
        elif 0 <= mat["toon"] < len(model["textures"]):
            key = ("tex", mat["toon"])
        else:
            return None
        if key not in toon_cache:
            r = None
            if key[0] == "shared":
                st = _SHARED_TOON.get(key[1])
                if st:
                    t, b = [tuple(c / 255.0 for c in x) for x in st]
                    r = _toon_shading([t] * 16 + [b] * 16)
            else:
                rel = model["textures"][key[1]].replace("\\", "/")
                for c in (os.path.join(base_dir, rel),
                          os.path.join(base_dir, os.path.basename(rel))):
                    if os.path.isfile(c):
                        with open(c, "rb") as f:
                            img = vrmio.decode_rgba(f.read())
                        if img:
                            r = _toon_shading(_toon_column(img[2], img[0], img[1]))
                        break
            toon_cache[key] = r
        return toon_cache[key]

    def lit_shade(mat, color):
        """MMD 显示色（_mmd_color）→ MToon 的 (亮部 RGBA, 阴影 RGB, shift, toony)。"""
        tp = toon_params(mat)
        if tp is None:
            # 没有可用的 toon：实际几乎都是眼睛、高光、眉睫、嘴、表情贴片。按二次元的
            # 惯例（原生 VRoid 的眼睛、眉毛阴影色都是白）不画阴影，不然一进投影眼睛就发黑
            return list(color), list(color[:3]), 0.0, 0.9
        lit, dark, shift, toony = tp
        return ([color[k] * lit[k] for k in range(3)] + [color[3]],
                [color[k] * dark[k] for k in range(3)], shift, toony)

    # ---- 表情预处理（材质表情会影响材质的 alphaMode，要先算）
    flat = _flatten_morphs(model["morphs"])
    # max_morphs: None = 全部；0 = 不导出；n = 只取前 n 个
    if max_morphs is not None and max_morphs >= 0:
        flat = flat[:int(max_morphs)]
    morph_min_alpha = {}             # PMX 材质 → 材质表情能把 alpha 压到多低
    for _, _, ops in flat:
        for mi_, mat in enumerate(materials):
            r = _morph_material(mat, ops, mi_)
            if r is not None:
                morph_min_alpha[mi_] = min(morph_min_alpha.get(mi_, 1.0),
                                           r[0][3])

    def alpha_mode(mi_, mat):
        """OPAQUE / MASK / BLEND。

        以前只看漫反射 alpha，贴图自带透明通道（头发高光层、瞳孔、
        镂空裙摆）的材质全被写成 OPAQUE，透明区域画成实心——
        实测头发被一层黑色盖住、眼睛一片黑。现在还看贴图的 alpha 分布：
          * 半透明像素占可见像素的比例低（只有抗锯齿边缘）→ MASK（裁剪，
            不需要排序，Unity 里最稳）
          * 比例高（柔和的叠加层、薄纱）→ BLEND
        材质表情会把它隐藏（alpha → 0）的不透明材质用 MASK，才能真的隐藏。
        """
        if mat["diffuse"][3] < 0.999:
            return "BLEND"
        st = tex_alpha.get(mat["tex"])
        if st and st[0] and st[1] >= st[0] * 0.001:
            n, trans, mid = st
            visible = n - trans + mid
            if visible <= 0 or mid >= visible * 0.3:
                return "BLEND"
            return "MASK"
        if morph_min_alpha.get(mi_, 1.0) < 0.5:
            return "MASK"
        return "OPAQUE"

    blend_rank = [0]
    sph_skipped = [0]

    def make_material(mi_, mat):
        """生成 glTF 材质（1.0 附带 VRMC_materials_mtoon；0.x 另写 MToon 属性）。"""
        ti = resolve_tex(mat["tex"])
        # 亮部 / 阴影色和明暗交界按 PMX 的 toon 渐变换算（MMD 的暗部和自阴影都取
        # toon 最下端的颜色）。以前一律「阴影 = 亮部 × 0.8」：灰色的阴影叠在浅色皮肤上
        # 几乎看不出来，星铁、鸣潮的脸又白又平；原生 VRoid 皮肤的阴影是粉色的
        diff, shade, shift, toony = lit_shade(mat, _mmd_color(mat["diffuse"], mat["ambient"]))
        mode = alpha_mode(mi_, mat)
        double = True if force_double_sided else bool(mat["flag"] & 0x01)
        # 加算球面贴图（sph_mode 2）≈ MToon 的 matcap（同样是加算）；
        # 乘算（1）没有对应项，跳过
        si = -1
        if mat["sph_mode"] == 2:
            si = resolve_tex(mat["sph"])
        elif mat["sph_mode"] == 1 and 0 <= mat["sph"] < len(model["textures"]):
            sph_skipped[0] += 1
        rq = 0
        if mode == "BLEND":         # 半透明按 PMX 材质顺序排绘制顺序
            rq = min(9, blend_rank[0])
            blend_rank[0] += 1
        edge = outline and bool(mat["flag"] & 0x10) and mat["edge_size"] > 0
        gmat = {
            "name": mat["name"] or ("mat%d" % mi_),
            "pbrMetallicRoughness": {
                "baseColorFactor": _lin_rgb(diff),
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9,
            },
            "doubleSided": double,
            "emissiveFactor": [0.0, 0.0, 0.0],
            "alphaMode": mode,
        }
        if mode == "MASK":
            gmat["alphaCutoff"] = 0.5
        if ti >= 0:
            gmat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": ti}

        if is1:
            mt = {
                "specVersion": "1.0",
                "transparentWithZWrite": False,
                "renderQueueOffsetNumber": rq,
                "shadeColorFactor": _lin_rgb(shade),
                "shadingShiftFactor": round(shift, 4),
                "shadingToonyFactor": round(toony, 4),
                "giEqualizationFactor": 0.9,
                "matcapFactor": [1.0, 1.0, 1.0] if si >= 0 else [0.0, 0.0, 0.0],
                "parametricRimColorFactor": [0.0, 0.0, 0.0],
                "parametricRimFresnelPowerFactor": 5.0,
                "parametricRimLiftFactor": 0.0,
                "rimLightingMixFactor": 1.0,
                "outlineWidthMode": "worldCoordinates" if edge else "none",
                "outlineWidthFactor": (mat["edge_size"] * 0.001) if edge else 0.0,
                "outlineColorFactor": _lin_rgb(mat["edge_color"][:3]),
                "outlineLightingMixFactor": 1.0,
            }
            if ti >= 0:
                mt["shadeMultiplyTexture"] = {"index": ti}
            if si >= 0:
                mt["matcapTexture"] = {"index": si}
            gmat["extensions"] = {"VRMC_materials_mtoon": mt}
        else:
            blend = mode == "BLEND"
            mask = mode == "MASK"
            tex_props = {}
            if ti >= 0:
                tex_props["_MainTex"] = ti
                tex_props["_ShadeTexture"] = ti   # 以前没写，暗部只剩一块纯色
            if si >= 0:
                tex_props["_SphereAdd"] = si
            vrm_material_props.append({
                "name": gmat["name"],
                "shader": "VRM/MToon",
                "renderQueue": (3000 + rq) if blend else (2450 if mask else 2000),
                "floatProperties": {
                    "_Cutoff": 0.5, "_BumpScale": 1.0,
                    # 0.x：smoothstep(shift, shift + 1 − toony, N·L)，换算成同样的交界和宽度
                    "_ReceiveShadowRate": 1.0,
                    "_ShadeShift": round(max(-1.0, min(1.0, -shift - (1.0 - toony))), 4),
                    "_ShadeToony": round(max(0.0, 2.0 * toony - 1.0), 4),
                    "_LightColorAttenuation": 0.0,
                    "_IndirectLightIntensity": 0.1, "_RimLightingMix": 0.0,
                    "_RimFresnelPower": 1.0, "_RimLift": 0.0,
                    # 0.x 世界坐标模式的线宽单位是厘米
                    "_OutlineWidth": (mat["edge_size"] * 0.1) if edge else 0.0,
                    "_OutlineScaledMaxDistance": 1.0,
                    "_OutlineLightingMix": 1.0, "_UvAnimScrollX": 0.0,
                    "_UvAnimScrollY": 0.0, "_UvAnimRotation": 0.0,
                    "_MToonVersion": 38.0, "_DebugMode": 0.0,
                    "_BlendMode": 2.0 if blend else (1.0 if mask else 0.0),
                    "_OutlineWidthMode": 1.0 if edge else 0.0,
                    "_OutlineColorMode": 0.0,
                    # Unity CullMode：0 Off / 1 Front / 2 Back。以前单面写成 1，
                    # 剔除的是正面
                    "_CullMode": 0.0 if double else 2.0,
                    "_OutlineCullMode": 1.0,
                    # 半透明：SrcAlpha / OneMinusSrcAlpha、不写深度。
                    # 以前透明材质也写 1/0/ZWrite=1，等于不透明
                    "_SrcBlend": 5.0 if blend else 1.0,
                    "_DstBlend": 10.0 if blend else 0.0,
                    "_ZWrite": 0.0 if blend else 1.0,
                    "_AlphaToMask": 0.0,
                },
                "vectorProperties": {
                    "_Color": [diff[0], diff[1], diff[2], diff[3]],
                    "_ShadeColor": shade + [1.0],
                    "_EmissionColor": [0.0, 0.0, 0.0, 1.0],
                    "_OutlineColor": list(mat["edge_color"][:3]) + [1.0],
                    "_RimColor": [0.0, 0.0, 0.0, 1.0],
                },
                "textureProperties": tex_props,
                "keywordMap": {"_NORMALMAP": False, "_ALPHATEST_ON": mask,
                               "_ALPHABLEND_ON": blend,
                               "_ALPHAPREMULTIPLY_ON": False,
                               "MTOON_OUTLINE_WIDTH_WORLD": edge,
                               "MTOON_OUTLINE_COLOR_FIXED": edge},
                "tagMap": {"RenderType": ("Transparent" if blend else
                                          "TransparentCutout" if mask
                                          else "Opaque")},
            })
        gltf_materials.append(gmat)
        return len(gltf_materials) - 1

    cursor = 0
    for mi_, mat in enumerate(materials):
        cnt = mat["faces"]
        tri_count = cnt // 3
        positions, normals, uvs, joints, weights = [], [], [], [], []
        local = {}
        idxs = []

        def get_local(vi):
            j = local.get(vi)
            if j is not None:
                return j
            v = verts[vi]
            p = xf(vpos[vi])
            n = xf_dir(vnrm[vi])
            ln = math.sqrt(n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) or 1.0
            n = (n[0] / ln, n[1] / ln, n[2] / ln)
            j = len(positions) // 3
            local[vi] = j
            positions.extend(p)
            normals.extend(n)
            uvs.extend(v["uv"])
            # JOINTS_0 的值是「skin.joints 数组的下标」，不是 glTF 节点号！
            # 我们的 joints 数组按 PMX 骨序排（joints[i] = 第 i 根骨的节点），
            # 所以这里直接写 PMX 骨索引 b。以前错写成节点号 bone_node(b)，
            # 拓扑排序一旦重排骨骼（remap 非恒等）权重就整体串位——
            # 症状是 Blender 里动某根骨头、别处的顶点跟着跑。
            wl = list(vweights[vi])
            while len(wl) < 4:
                wl.append((wl[0][0], 0.0))
            joints.extend([b for b, _ in wl[:4]])
            weights.extend([w for _, w in wl[:4]])
            return j

        for t in range(tri_count):
            a = faces[cursor + t * 3]
            b = faces[cursor + t * 3 + 1]
            c = faces[cursor + t * 3 + 2]
            ia, ib, ic = get_local(a), get_local(b), get_local(c)
            if reverse_winding:
                ia, ic = ic, ia
            idxs.extend([ia, ib, ic])
        cursor += cnt

        if not idxs:
            continue
        # 以前 primitive 直接用 PMX 材质号，PMX 里有 0 面材质时
        # 后面的材质会整体错一位；现在用实际写出的 glTF 材质号
        mat_gidx[mi_] = make_material(mi_, mat)
        prims.append({"local": local, "vcount": len(local),
                      "idxs": idxs, "pos": positions, "nrm": normals,
                      "uv": uvs, "jn": joints, "wt": weights,
                      "mat": mat_gidx[mi_], "targets": []})

    _log("网格：%d 个 primitive（材质）" % len(prims))
    modes = [m["alphaMode"] for m in gltf_materials]
    _log("材质透明模式：不透明 %d · 裁剪(MASK) %d · 半透明(BLEND) %d"
         % (modes.count("OPAQUE"), modes.count("MASK"), modes.count("BLEND")))
    if is1:
        nmc = sum(1 for m in gltf_materials if "matcapTexture" in
                  m["extensions"]["VRMC_materials_mtoon"])
        _log("材质：MToon（VRMC_materials_mtoon）· 加算球面贴图 → matcap %d 个"
             % nmc)
    if sph_skipped[0]:
        _log("提示：%d 个材质的乘算球面贴图（.sph）在 MToon 里没有对应项，已忽略"
             % sph_skipped[0], "warn")
    if not prims:
        raise RuntimeError("这个 PMX 没有可用的面数据")

    # ---- 写 accessor
    for pr in prims:
        pr["acc"] = {
            "POSITION": gb.add_accessor(pr["pos"], vrmio.FLOAT, "VEC3", 34962),
            "NORMAL": gb.add_accessor(pr["nrm"], vrmio.FLOAT, "VEC3", 34962),
            "TEXCOORD_0": gb.add_accessor(pr["uv"], vrmio.FLOAT, "VEC2", 34962),
            "JOINTS_0": gb.add_accessor(pr["jn"], vrmio.UNSIGNED_SHORT,
                                        "VEC4", 34962),
            "WEIGHTS_0": gb.add_accessor(pr["wt"], vrmio.FLOAT, "VEC4", 34962),
        }
        pr["indices"] = gb.add_accessor(pr["idxs"], vrmio.UNSIGNED_INT,
                                        "SCALAR", 34963, minmax=False)

    # ---- 表情 → morph target + VRM expression
    target_names = []
    target_set = set()
    expr_preset = {}
    expr_custom = {}
    blend_groups = []
    table = EXPR_1_0 if is1 else EXPR_0X
    n_mat_expr = 0

    def uniq_name(name, used):
        if name not in used:
            return name
        k = 2
        while "%s_%d" % (name, k) in used:
            k += 1
        return "%s_%d" % (name, k)

    for mname, deltas, ops in flat:
        slot = None
        if deltas:
            slot = len(target_names)
            for pr in prims:
                loc = pr["local"]
                items = []
                for vi, d in deltas.items():
                    j = loc.get(vi)
                    if j is None:
                        continue
                    if vmat is not None and vmat[vi] is not None:
                        d = _m3vec(vmat[vi], d)
                    # 偏移量也要乘缩放（xf 是纯线性变换，正好适用于偏移）。
                    # 以前用 xf_dir 只翻了轴没缩放，偏移大了 1/scale≈12 倍，
                    # 表情一开脸就拉成长刺。
                    items.append((j, xf(d)))
                if sparse_morphs:
                    # 稀疏存储：一个表情通常只动几百个顶点，密集存储每个表情
                    # 每个 primitive 都要「顶点数 × 12 字节」
                    items.sort()
                    acc = gb.add_sparse_accessor(
                        pr["vcount"], [j for j, _ in items],
                        [c for _, d in items for c in d], "VEC3")
                else:
                    arr = [0.0] * (pr["vcount"] * 3)
                    for j, d in items:
                        arr[j * 3:j * 3 + 3] = d
                    acc = gb.add_accessor(arr, vrmio.FLOAT, "VEC3", 34962)
                pr["targets"].append({"POSITION": acc})
            # Unity 的 BlendShape 名在一个网格内必须唯一，重名时 UniVRM 导入直接抛
            # 「Blend shape name already exists」中断（开拓者 PMX 里有两个「手前」）
            target_names.append(uniq_name(mname, target_set))
            target_set.add(target_names[-1])

        # 材质表情 → 目标颜色（权重 1 时的漫反射 RGBA）
        colors = []
        for mi_, mat in enumerate(materials):
            if mi_ not in mat_gidx:
                continue
            r = _morph_material(mat, ops, mi_)
            if r is None:
                continue
            base = _mmd_color(mat["diffuse"], mat["ambient"])
            c = _mmd_color(*r)
            if max(abs(c[k] - base[k]) for k in range(4)) < 1e-6:
                continue
            lit_c, shade_c, _, _ = lit_shade(mat, c)
            colors.append((mi_, lit_c, shade_c, max(abs(c[k] - base[k])
                                                    for k in range(3)) > 1e-6))
        if slot is None and not colors:
            continue
        if colors:
            n_mat_expr += 1

        preset = table.get(mname)
        if is1:
            obj = {"morphTargetBinds": ([{"node": 0, "index": slot,
                                          "weight": 1.0}]
                                        if slot is not None else []),
                   "materialColorBinds": [
                       b for mi_, c, sc, rgb in colors for b in (
                           [{"material": mat_gidx[mi_], "type": "color",
                             "targetValue": _lin_rgb(c)}] +
                           # targetValue 规范要求正好 4 个分量：UniVRM 直接读 [3]，
                           # 只写 RGB 会在导入时 IndexOutOfRange 中断
                           ([{"material": mat_gidx[mi_], "type": "shadeColor",
                              "targetValue": _lin_rgb(sc) + [1.0]}]
                            if rgb else []))],
                   "isBinary": False}
            # 规范要求这些数组非空（minItems 1）：没有就不写
            for k_ in ("morphTargetBinds", "materialColorBinds"):
                if not obj[k_]:
                    del obj[k_]
            if preset and preset not in expr_preset:
                expr_preset[preset] = obj
            else:
                # 自定义表情名必须唯一（PMX 里同名表情会互相覆盖）
                expr_custom[uniq_name(mname, expr_custom)] = obj
        else:
            blend_groups.append({
                "name": mname,
                "presetName": preset or "Unknown",
                "binds": ([{"mesh": 0, "index": slot, "weight": 100}]
                          if slot is not None else []),
                "materialValues": [
                    v for mi_, c, sc, rgb in colors for v in (
                        [{"materialName": gltf_materials[mat_gidx[mi_]]["name"],
                          "propertyName": "_Color", "targetValue": c}] +
                        ([{"materialName":
                           gltf_materials[mat_gidx[mi_]]["name"],
                           "propertyName": "_ShadeColor",
                           "targetValue": sc + [1.0]}]
                         if rgb else []))],
                "isBinary": False})
    if target_names:
        _log("表情：导出 %d 个 morph target（%s存储）"
             % (len(target_names), "稀疏" if sparse_morphs else "密集"))
    if n_mat_expr:
        _log("表情：%d 个含材质变化（材质表情 → materialColorBinds）"
             % n_mat_expr)

    # ---- 收尾节点
    gltf_mesh = {"name": model["name"] or "body", "primitives": []}
    for pr in prims:
        p = {"attributes": pr["acc"], "indices": pr["indices"],
             "material": pr["mat"]}
        if pr["targets"]:
            p["targets"] = pr["targets"]
        gltf_mesh["primitives"].append(p)
    if target_names:
        gltf_mesh["extras"] = {"targetNames": target_names}

    inv = []
    for i in range(len(bones)):
        p = xf(bpos[i])
        inv.extend([1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    -p[0], -p[1], -p[2], 1.0])
    if not inv:
        inv = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    ibm = gb.add_accessor(inv, vrmio.FLOAT, "MAT4", minmax=False)

    mesh_node_index = len(nodes)
    nodes.append({"name": "mesh", "mesh": 0, "skin": 0})
    root_index = len(nodes)
    nodes.append({"name": "root",
                  "children": list(top_nodes) + [mesh_node_index]})

    joints = [node_of_bone[remap[i]] for i in range(len(bones))]
    if not joints:
        joints = [0]

    gltf = {
        "asset": {"version": "2.0", "generator": "pmx2vrm.py (pure python)"},
        "scene": 0,
        "scenes": [{"nodes": [root_index]}],
        "nodes": nodes,
        "meshes": [gltf_mesh],
        "skins": [{"joints": joints, "inverseBindMatrices": ibm,
                   "skeleton": root_index}],
        "materials": gltf_materials,
        "accessors": gb.accessors,
        "bufferViews": gb.bufferViews,
        "buffers": [{"byteLength": len(gb.bin)}],
    }
    if gb.images:
        gltf["images"] = gb.images
        gltf["textures"] = gb.textures
        gltf["samplers"] = gb.samplers

    # morphTargetBinds 的 node 指向 mesh 节点；0.x 的 mesh 指向 mesh 索引
    if is1:
        for grp in (expr_preset, expr_custom):
            for obj in grp.values():
                for b in obj.get("morphTargetBinds", []):
                    b["node"] = mesh_node_index
    else:
        for g in blend_groups:
            for b in g["binds"]:
                b["mesh"] = 0

    # ---- VRM 扩展
    meta = meta or {}
    title = meta.get("title") or model["name"] or "PMX Model"
    author = meta.get("author") or ""
    if is1:
        vrm_ext = {
            "specVersion": "1.0",
            "humanoid": {"humanBones": {k: {"node": v}
                                        for k, v in human.items()}},
            "meta": {
                "name": title,
                "version": meta.get("version", "1"),
                "authors": [author] if author else ["unknown"],
                "copyrightInformation": meta.get("copyright", ""),
                "contactInformation": meta.get("contact", ""),
                "thirdPartyLicenses": "",
                # thumbnailImage 规范要求是图片索引（整数）；没有缩略图就不写，
                # 写 null 过不了 VRM 1.0 schema 校验。
                "licenseUrl": meta.get("license_url",
                                       "https://vrm.dev/licenses/1.0/"),
                "avatarPermission": meta.get("avatar_permission", "everyone"),
                "allowExcessivelyViolentUsage": False,
                "allowExcessivelySexualUsage": False,
                "commercialUsage": meta.get("commercial_usage",
                                            "personalNonProfit"),
                "allowPoliticalOrReligiousUsage": False,
                "allowAntisocialOrHateUsage": False,
                "creditNotation": "required",
                "allowRedistribution": False,
                "modification": "prohibited",
            },
            "firstPerson": {"meshAnnotations": [{"node": mesh_node_index,
                                                 "type": "auto"}]},
        }
        if expr_preset or expr_custom:
            vrm_ext["expressions"] = {}
            if expr_preset:
                vrm_ext["expressions"]["preset"] = expr_preset
            if expr_custom:
                vrm_ext["expressions"]["custom"] = expr_custom
        if "leftEye" in human or "rightEye" in human:
            vrm_ext["lookAt"] = {
                "type": "bone",
                "offsetFromHeadBone": [0.0, 0.06, 0.0],
                "rangeMapHorizontalInner": {"inputMaxValue": 90.0,
                                            "outputScale": 10.0},
                "rangeMapHorizontalOuter": {"inputMaxValue": 90.0,
                                            "outputScale": 10.0},
                "rangeMapVerticalDown": {"inputMaxValue": 90.0,
                                         "outputScale": 10.0},
                "rangeMapVerticalUp": {"inputMaxValue": 90.0,
                                       "outputScale": 10.0},
            }
        gltf["extensionsUsed"] = ["VRMC_vrm", "VRMC_materials_mtoon"]
        gltf["extensions"] = {"VRMC_vrm": vrm_ext}
        if spring_ext:
            if spring_center:
                # center = 根节点：在模型自身的空间里模拟。应用里人物整体移动（走路的
                # 根运动、平移）时，头发/裙摆不会因惯性被甩、往后拖压到腿上，只对身体本身
                # 的动作有反应。实测走路时头发摆幅 0.73→0.15、胸部配饰 0.28→0.05、
                # 裙摆穿进大腿的顶点 10→3。规范要求 center 是链首关节或其祖先，根节点满足
                for sp in spring_ext["springs"]:
                    sp["center"] = root_index
            gltf["extensionsUsed"].append("VRMC_springBone")
            gltf["extensions"]["VRMC_springBone"] = spring_ext
    else:
        vrm_ext = {
            "exporterVersion": "pmx2vrm.py",
            "specVersion": "0.0",
            "meta": {
                "title": title,
                "version": meta.get("version", "1"),
                "author": author,
                "contactInformation": meta.get("contact", ""),
                "reference": "",
                "allowedUserName": "Everyone",
                "violentUssageName": "Disallow",
                "sexualUssageName": "Disallow",
                "commercialUssageName": meta.get("commercial_0x", "Disallow"),
                "otherPermissionUrl": "",
                "licenseName": "Other",
                "otherLicenseUrl": meta.get("license_url", ""),
            },
            "humanoid": {
                "humanBones": [{"bone": k, "node": v, "useDefaultValues": True}
                               for k, v in human.items()],
                "armStretch": 0.05, "legStretch": 0.05,
                "upperArmTwist": 0.5, "lowerArmTwist": 0.5,
                "upperLegTwist": 0.5, "lowerLegTwist": 0.5,
                "feetSpacing": 0.0, "hasTranslationDoF": False,
            },
            "firstPerson": {
                "firstPersonBone": human.get("head", 0),
                "firstPersonBoneOffset": {"x": 0.0, "y": 0.06, "z": 0.0},
                "meshAnnotations": [{"firstPersonFlag": "Auto", "mesh": 0}],
                "lookAtTypeName": ("Bone" if ("leftEye" in human or
                                              "rightEye" in human)
                                   else "BlendShape"),
                "lookAtHorizontalInner": _degmap(90, 10),
                "lookAtHorizontalOuter": _degmap(90, 10),
                "lookAtVerticalDown": _degmap(90, 10),
                "lookAtVerticalUp": _degmap(90, 10),
            },
            "blendShapeMaster": {"blendShapeGroups": blend_groups},
            "secondaryAnimation": {"boneGroups": []},
            "materialProperties": vrm_material_props,
        }
        gltf["extensionsUsed"] = ["VRM"]
        gltf["extensions"] = {"VRM": vrm_ext}

    size = vrmio.write_glb(gltf, bytes(gb.bin), vrm_path)
    n_expr = (len(expr_preset) + len(expr_custom)) if is1 else len(blend_groups)
    _log("已写出 %s（%.2f MB）· 节点 %d · morph target %d · 表情 %d"
         % (os.path.basename(vrm_path), size / 1048576.0, len(nodes),
            len(target_names), n_expr), "ok")
    return {"nodes": len(nodes), "primitives": len(prims),
            "morphs": len(target_names), "bytes": size, "scale": scale,
            "vertices": sum(p["vcount"] for p in prims)}


# -------------------------------------------------------------------- cli --
def main(argv=None):
    ap = argparse.ArgumentParser(description="PMX → VRM（纯 Python）")
    ap.add_argument("pmx")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--spec", default="1.0", choices=["1.0", "0x"])
    ap.add_argument("--scale", default="auto",
                    help="auto（归一到 1.6 m）或具体倍率")
    ap.add_argument("--rotate", default="auto", choices=["auto", "none", "y180"])
    ap.add_argument("--flip-winding", action="store_true",
                    help="强制反转三角形缠绕顺序（默认自动判定）")
    ap.add_argument("--force-double-sided", action="store_true",
                    help="所有材质强制双面（模型仍有镂空时的兜底手段）")
    ap.add_argument("--keep-pose", action="store_true",
                    help="保留原始姿势（默认把 A-pose 手臂抬成 VRM 要求的 T-pose）")
    ap.add_argument("--no-spring", action="store_true",
                    help="不把 PMX 物理转成 SpringBone（默认转换，仅 VRM 1.0）")
    ap.add_argument("--no-spring-skirt", action="store_true",
                    help="跳过下半身（hips / 腿）下的物理链，通常是裙摆")
    ap.add_argument("--spring-stiffness", type=float, default=1.0,
                    help="SpringBone 硬度整体倍率（默认 1.0，越大越硬）")
    ap.add_argument("--spring-gravity", type=float, default=0.0,
                    help="SpringBone 重力（头发等，默认 0：保持 PMX 原造型；"
                         "斜着散开的长发加重力会被拉垂）")
    ap.add_argument("--spring-gravity-skirt", type=float, default=0.1,
                    help="下半身（裙摆）SpringBone 重力（默认 0.1，抬腿时裙片落回腿上）")
    ap.add_argument("--no-spring-center", action="store_true",
                    help="SpringBone 不设 center（默认以模型根节点为 center，人物整体移动时"
                         "头发/裙摆不会被甩）")
    ap.add_argument("--outline", action="store_true",
                    help="按 PMX 的轮廓线设置写出 MToon 描边（默认不描边）")
    ap.add_argument("--dense-morphs", action="store_true",
                    help="表情用密集存储（默认稀疏存储，文件小很多）")
    ap.add_argument("--max-morphs", type=int, default=None,
                    help="只导出前 N 个表情（0 = 不导出，默认全部）")
    ap.add_argument("--title", default=None)
    ap.add_argument("--author", default=None)
    a = ap.parse_args(argv)

    out = a.out or (os.path.splitext(a.pmx)[0] + ".vrm")
    meta = {}
    if a.title:
        meta["title"] = a.title
    if a.author:
        meta["author"] = a.author
    st = convert(a.pmx, out, spec=("1.0" if a.spec == "1.0" else "0.x"),
                 scale_mode=a.scale, rotate=a.rotate,
                 reverse_winding=(True if a.flip_winding else "auto"),
                 meta=meta, max_morphs=a.max_morphs,
                 force_double_sided=a.force_double_sided,
                 tpose=not a.keep_pose, outline=a.outline,
                 spring=not a.no_spring, spring_skirt=not a.no_spring_skirt,
                 spring_stiffness=a.spring_stiffness,
                 spring_gravity=a.spring_gravity,
                 spring_gravity_skirt=a.spring_gravity_skirt,
                 spring_center=not a.no_spring_center,
                 sparse_morphs=not a.dense_morphs,
                 log=lambda msg, tag=None: print(msg))
    print("完成：%s（%.2f MB）" % (out, st["bytes"] / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
