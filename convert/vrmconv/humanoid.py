# -*- coding: utf-8 -*-
"""骨骼：MMD 骨骼名 → VRM humanoid 骨位、付与骨合并、层级修正、A-pose → T-pose。"""
import math
import re

from .mathutil import _I3, _align, _axis_angle, _m3mul, _m3T, _m3vec, _to_axis_angle


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

# 英文骨名（FBX 转来的 PMX 保留原名）：去掉前缀和左右标记后**整段**匹配，
# 不会误伤 J_L_HairTail_00、skirt_L_01 这类非人形骨。
#   HumanIK / Mixamo / Unity：Character1_LeftUpLeg、mixamorig:LeftForeArm、Chr_LeftHandIndex1
#   VRoid：J_Bip_L_UpperArm、J_Bip_C_Chest      Blender：thigh.L、upper_arm.R
#   3ds Max Biped：Bip001 L Thigh、Bip001 Spine1
_EN_PREFIX = re.compile(r"^(?:character\d*|chr|bip0*1|jbipc|jbip|j|def|org|mch|armature|skeleton|root|rig|"
                        r"cc_base|valvebiped_bip01|bip01)(?=[a-z])")
_EN_SIDE = re.compile(r"(?:^|[^a-z])(l|r)(?:[^a-z]|$)|left|right")
_EN_BONES = {
    "hips": "hips", "hip": "hips", "pelvis": "hips",
    "spine": "spine", "spine0": "spine", "spine1": "chest", "spine01": "chest", "spine2": "upperChest",
    "spine02": "upperChest", "chest": "chest", "upperchest": "upperChest",
    "neck": "neck", "neck1": "neck", "head": "head",
}
_EN_LIMBS = {
    "eye": "Eye", "shoulder": "Shoulder", "clavicle": "Shoulder",
    "arm": "UpperArm", "upperarm": "UpperArm",
    "forearm": "LowerArm", "lowerarm": "LowerArm", "elbow": "LowerArm",
    "hand": "Hand", "wrist": "Hand",
    "upleg": "UpperLeg", "upperleg": "UpperLeg", "thigh": "UpperLeg", "hip": "UpperLeg",
    "leg": "LowerLeg", "lowerleg": "LowerLeg", "calf": "LowerLeg", "shin": "LowerLeg", "knee": "LowerLeg",
    "foot": "Foot", "ankle": "Foot",
    "toebase": "Toes", "toe": "Toes", "toes": "Toes", "toe0": "Toes",
}
_EN_FINGERS = {"thumb": "Thumb", "index": "Index", "middle": "Middle", "ring": "Ring",
               "pinky": "Little", "little": "Little"}


def _en_bone(name, thumb_metacarpal=False):
    """英文骨名 → ("slot", 骨位) / ("finger", (side, finger, 节号 0～3)) / None。

    节号按 MMD 的口径：拇指 0～2（VRM 1.0 的 Metacarpal / Proximal / Distal），其余手指 1～3；
    HumanIK 的拇指是 Thumb1～3、指尖是 4，所以拇指减 1、第 4 节（指尖）丢掉。
    Unity / VRChat 的「Index Proximal.L」这类节名：其余手指 Proximal / Intermediate / Distal = 1～3；
    拇指在 Unity 和 VRM 0.x 里叫 Proximal / Intermediate / Distal（第一节在掌根）= 0～2，
    VRM 1.0 风格叫 Metacarpal / Proximal / Distal——同一个 Proximal 指的节不一样，
    所以由调用方看整副骨架里有没有 ThumbMetacarpal（thumb_metacarpal）。
    """
    s = (name or "").split(":")[-1].split("|")[-1].lower()
    m = _EN_SIDE.search(s.replace(".", "_").replace(" ", "_"))
    side = None
    if m:
        side = "left" if (m.group(1) or m.group(0)).strip("_. ").startswith("l") else "right"
    base = re.sub(r"left|right", "", s)
    base = re.sub(r"(?:^|[_. ])[lr](?=[_. ]|$)", "_", base)       # _L / .L / " L " / J_Bip_L_
    base = re.sub(r"[^a-z0-9]", "", base)
    base = _EN_PREFIX.sub("", base)
    if not base:
        return None
    if side is None:
        v = _EN_BONES.get(base)
        return ("slot", v) if v else None
    if base.startswith("hand") and base[4:] and not base[4:].isdigit():
        base = base[4:]                                              # HumanIK: LeftHandIndex1
    m = re.fullmatch(r"(thumb|index|middle|ring|pinky|little)(\d)", base)
    if m:
        f, d = _EN_FINGERS[m.group(1)], int(m.group(2))
        if f == "Thumb":
            d -= 1
        return ("finger", (side, f, d)) if 0 <= d <= 3 and not (f != "Thumb" and d == 0) else None
    m = re.fullmatch(r"(thumb|index|middle|ring|pinky|little)(metacarpal|proximal|intermediate|distal)", base)
    if m:
        f, seg = _EN_FINGERS[m.group(1)], m.group(2)
        if f == "Thumb":
            segs = {"metacarpal": 0, "proximal": 1, "distal": 2} if thumb_metacarpal else \
                {"proximal": 0, "intermediate": 1, "distal": 2}
        else:
            segs = {"proximal": 1, "intermediate": 2, "distal": 3}
        return ("finger", (side, f, segs[seg])) if seg in segs else None
    v = _EN_LIMBS.get(base)
    return ("slot", side + v) if v else None


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
    en = {}                          # 英文骨名兜底：骨位 → 骨索引
    # HumanIK 的 LeftLeg 是小腿（大腿叫 LeftUpLeg）；只有 Leg、没有 UpLeg / Thigh 时 Leg 才是大腿
    has_upleg = any(re.search(r"upleg|upperleg|thigh", (b["name"] or "").lower()) for b in bones)
    thumb_mc = any(re.search(r"thumb.?metacarpal", b["name"] or "", re.I) for b in bones)
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
        if not v:
            r = _en_bone(b["name"], thumb_mc)
            if r and r[0] == "finger":
                en.setdefault(("finger",) + r[1][:2], {}).setdefault(r[1][2], i)
            elif r:
                slot = r[1]
                if not has_upleg and slot.endswith("LowerLeg") and "leg" in _norm(b["name"]) \
                        and not re.search(r"lower|calf|shin|knee", b["name"].lower()):
                    slot = slot.replace("LowerLeg", "UpperLeg")
                en.setdefault(slot, i)
    # 两种叫法的冲突（UE / Maya 常见）：
    #   L_Clavicle + L_Shoulder：Clavicle 是肩、Shoulder 其实是上臂
    #   Spine1～Spine4 而没有 Spine：第一节当 spine、最后一节当 upperChest、中间当 chest
    pairs = []                       # (肩骨位, 上臂骨位, 锁骨, 「肩」骨)
    for side in ("left", "right"):
        sh = [i for i, b in enumerate(bones) if i not in alias
              and re.search(r"clavicle|shoulder", b["name"] or "", re.I)
              and (_en_bone(b["name"]) or ("", ""))[1] == side + "Shoulder"]
        if len(sh) == 2 and side + "UpperArm" not in en:
            par = bones[sh[1]].get("parent")
            a, b = (sh[0], sh[1]) if par == sh[0] else (sh[1], sh[0]) if bones[sh[0]].get("parent") == sh[1] else (None, None)
            if a is not None:
                en[side + "Shoulder"], en[side + "UpperArm"] = a, b
                pairs.append((side + "Shoulder", side + "UpperArm", a, b))
    if "spine" not in en and "spine" not in human:
        nums = sorted((int(m.group(1)), i) for i, b in enumerate(bones)
                      for m in [re.fullmatch(r"(?:.*[_:. ])?spine_?0*(\d+)", (b["name"] or "").lower())] if m)
        if len(nums) >= 2:
            seq = [i for _, i in nums]
            en["spine"] = seq[0]
            if len(seq) >= 3:
                en["chest"] = seq[len(seq) // 2] if len(seq) >= 4 else seq[1]
                en["upperChest"] = seq[-1]
            else:
                en["chest"] = seq[1]
                en.pop("upperChest", None)
    # 英文名只在 MMD 名没对上时补位（同一个模型两套都有时以 MMD 名为准）
    for k, i in en.items():
        if k[0] == "finger":
            if (k[1], k[2]) not in fingers:
                fingers[(k[1], k[2])] = i
        elif k not in human:
            human[k] = i
    for s_slot, u_slot, a, b in pairs:   # 旧的英文名表把 L_Shoulder 当成了肩：以配对结果为准
        if human.get(s_slot) in (a, b) or human.get(u_slot) in (a, b, None):
            human[s_slot], human[u_slot] = a, b
    # 没有叫「下半身 / Hips」的骨：取腿和脊椎的最近公共祖先当 hips。XPS 的 root hips 被转成
    # センター（腿和上半身都直接挂在它下面），以前这时只能补一根占位骨
    if "hips" not in human and "spine" in human and ("leftUpperLeg" in human or "rightUpperLeg" in human):
        def chain(i):
            out, seen = [], set()
            while 0 <= i < len(bones) and i not in seen:
                out.append(i)
                seen.add(i)
                i = bones[i].get("parent", -1)
            return out
        leg = human.get("leftUpperLeg", human.get("rightUpperLeg"))
        spine_up = set(chain(bones[human["spine"]].get("parent", -1)))
        for a in chain(bones[leg].get("parent", -1)):
            if a in spine_up and a not in alias and a not in human.values():
                human["hips"] = a
                break
    # 上半身2 / 上半身3 的名字和层级反着的模型（鸣潮的娜波摩、秧秧：上半身 → 上半身3 → 上半身2 → 首）：
    # 按实际的父子顺序分，下面那根是 chest、上面那根是 upperChest。按名字分的话 upperChest
    # 在 chest 上面，层级改不过来只能把它从 humanoid 里去掉，躯干少一节
    if "chest" in human and "upperChest" in human:
        parents = [b.get("parent", -1) for b in bones]
        if _is_ancestor(parents, human["upperChest"], human["chest"]):
            human["chest"], human["upperChest"] = human["upperChest"], human["chest"]
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
            log("骨骼：%s 与 %s 对应同一根骨骼，已忽略 %s" % (h, anc, h), "warn")
            continue
        if _is_ancestor(parents, a, c):
            continue
        old = parents[c]
        if _reparent(parents, c, a):
            log("骨骼层级：%s（%s）不在 %s（%s）之下，已移至其下（原父骨骼 %s）"
                % (h, bones[c]["name"], anc, bones[a]["name"],
                   bones[old]["name"] if old >= 0 else "无"))
        elif h not in required:
            del human[h]
            log("骨骼层级：%s（%s）无法修正，已从 humanoid 中移除"
                % (h, bones[c]["name"]), "warn")
        else:
            log("骨骼层级：%s（%s）无法修正，部分软件可能无法加载"
                % (h, bones[c]["name"]), "warn")


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
