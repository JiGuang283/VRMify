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
    # 拇指按解剖位置对应：节号 0 = 掌根那节（1.0 Metacarpal / 0.x Proximal），1 = 1.0 Proximal /
    # 0.x Intermediate，2 = Distal。两个版本同一根骨对应同一节，套同一个手部动作时拇指才一致
    # （以前 0.x 把没有 親指０ 的两节拇指当成第一、二节，1.0 当成第二、三节）
    names = ("Metacarpal", "Proximal", "Distal") if is1 else ("Proximal", "Intermediate", "Distal")
    return {d: names[d] for d in (0, 1, 2) if d in digits}

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


def _append_aliases(bones, parents, eps, loose=0.5):
    """「付与回転 100%」跟随付与親的骨 → 付与親，返回 {骨: 付与親}。

    典型是 足D / ひざD / 足首D：网格权重全在 D 骨上，D 骨靠 PMX 的付与
    跟随 足 / ひざ / 足首 转动；而 humanoid 映射指向的是 足 / ひざ / 足首。
    glTF/VRM 没有付与机制，原样导出时转大腿网格完全不动。
    和付与親在静止姿势下重合（差不超过 eps）、旋转也 1:1 跟随的，把权重和子骨并到付与親上是等价的。
    不重合的（足首D 比 足首 靠后 2～3 cm 之类）也合并，条件是：
      * 两者跟着同一根 humanoid 骨移动：各自的父骨往上找到的第一根 humanoid 骨相同（D 骨的父骨
        并入后算付与親那边的）。中间隔着 腰キャンセル 之类在 VRM 里不动的骨不要紧；挂在别处
        （全ての親 下的道具骨）的不并，否则它会跟着付与親整体平移
      * 偏差不超过付与親那一节骨长（父骨→付与親、付与親→它的尾端，取长的）的 loose 倍
      * D 骨本身不是 humanoid 骨位（左目 跟随 両目 就不能并）
    合并后网格绕付与親的位置转，和 MMD 只差一个平移（偏差 2.5 cm、转 30° 时约 1 cm）；
    不合并的话它在 VRM 里完全不动，挂在付与親子骨上的部分（鞋尖）却跟着转，鞋子从中间裂开。
    付与率不是 1 的（ひざ補助 的 0.5 等）见 skeleton.split_partial；负的（腰キャンセル 的 -1）不动。
    """
    n = len(bones)
    alias, near = {}, {}
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
        if _is_ancestor(parents, i, src):        # 付与親在自己下面：并过去会成环
            continue
        d = math.dist(b["pos"], bones[src]["pos"])
        if d <= eps:
            alias[i] = src
        elif not MMD_TO_VRM.get(b["name"]):
            near[i] = (src, d)

    def resolve(i):
        seen = set()
        while i in alias and i not in seen:
            seen.add(i)
            i = alias[i]
        return i
    hset = set(_map_humanoid(bones, True, {}).values()) if near else set()

    def anchor(i):                   # 往上第一根 humanoid 骨（并入的骨按付与親算）
        seen = set()
        while i >= 0 and i not in seen:
            i = resolve(i)
            if i in hset:
                return i
            seen.add(i)
            i = parents[i]
        return -1
    grew = True
    while grew:                      # D 骨的父骨可能也是这样并进来的（ひざD → 足首D）
        grew = False
        for i, (src, d) in near.items():
            pi, ps = parents[i], parents[src]
            if i in alias or pi < 0 or ps < 0 or anchor(pi) != anchor(ps):
                continue
            if d <= loose * max(math.dist(bones[ps]["pos"], bones[src]["pos"]), _bone_len(bones, parents, src)):
                alias[i] = src
                grew = True
    return {i: resolve(i) for i in alias}


def _bone_len(bones, parents, i):
    """骨 i 到它尾端的长度：尾端指向的骨，或尾端偏移；都没有就取到最远子骨的距离。"""
    b = bones[i]
    t = b.get("tail")
    if b.get("tail_kind") == 1 and isinstance(t, int) and 0 <= t < len(bones) and t != i:
        return math.dist(b["pos"], bones[t]["pos"])
    if b.get("tail_kind") == 0 and t and not isinstance(t, int) and any(t):
        return math.hypot(*t)
    return max((math.dist(b["pos"], bones[c]["pos"]) for c, p in enumerate(parents) if p == i), default=0.0)


def _pick_toes(bones, human, alias, weighted):
    """humanoid 的脚趾：MMD 的 つま先 是足 IK 的末端点（鞋尖最前面），不是脚趾关节——动画转脚趾时
    绕着鞋尖最前面那一点转。有 足先EX（准标准骨，脚掌和脚趾之间的关节，鞋头的权重常在它上面）就用它；
    没有的话，つま先 没挂网格就留着（转了也没影响），挂了网格就不设脚趾（VRM 里可以不设，鞋子跟着脚整体转）。
    weighted：挂了网格的骨（PMX 骨索引）。只处理 MMD 名字的骨。"""
    names = {b["name"]: i for i, b in enumerate(bones) if i not in alias}
    for side, jp in (("left", "左"), ("right", "右")):
        slot = side + "Toes"
        cur = human.get(slot)
        if cur is not None and bones[cur]["name"] != jp + "つま先":
            continue
        ex = names.get(jp + "足先EX")
        if ex is not None:
            human[slot] = ex
        elif cur is not None and cur in weighted:
            del human[slot]


def _map_humanoid(bones, is1, alias):
    """PMX 骨 → VRM humanoid 骨位，返回 {骨位: PMX 骨索引}。"""
    human = {}
    fingers = {}                     # (side, finger) → {节号: 骨索引}
    alt = {}                         # PMX 英文名（name_en）对上的：骨位 → 骨索引，日文名都分完再补位
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
        if v:
            human.setdefault(v, i)
        else:
            # 英文名只补位：有的模型旁边另放一套控制骨（左肩調整 …），英文名和本体一样（L_Shoulder），
            # 排在前面的话会抢走 左肩 的骨位，肩膀跑到模型外面
            v = ALT_TO_VRM.get(_norm(b.get("name_en") or ""))
            if v:
                alt.setdefault(v, i)
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
    for k, i in alt.items():
        human.setdefault(k, i)
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
        if finger == "Thumb" and 0 not in digits and 1 in digits and _thumb_at_root(bones, human, fingers, side, digits[1]):
            digits = {d - 1: i for d, i in digits.items() if d >= 1}
        for d, slot in _finger_slots(finger, digits, is1).items():
            human.setdefault(side + finger + slot, digits[d])
    return human


def _thumb_at_root(bones, human, fingers, side, b):
    """只有 親指１、親指２ 的拇指：親指１ 是不是掌根那节（離手腕近）。

    带 親指０ 的模型里，按「手腕 → 中指根」的长度算，親指０ 离手腕 0.1～0.6、親指１ 在 0.7～1.0。
    老模型没有 親指０ 时 親指１ 一般还在 0.7 以上，是第二节；离手腕不到 0.65 的才当掌根那节。"""
    hd, mid = human.get(side + "Hand"), fingers.get((side, "Middle"), {}).get(1)
    if hd is None or mid is None:
        return False
    w = bones[hd]["pos"]
    ref = math.dist(w, bones[mid]["pos"])
    return ref > 1e-6 and math.dist(w, bones[b]["pos"]) < 0.65 * ref


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


_DIGIT_SEGS = ("Metacarpal", "Proximal", "Intermediate", "Distal")
_HAND_FINGERS = ("Thumb", "Index", "Middle", "Ring", "Little")


def _seg_end(bones, kids, b):
    """骨 b 这一节的末端位置（PMX 坐标）：PMX 的 tail（偏移或骨）→ 第一根子骨 → None。"""
    bb, pos = bones[b], bones[b]["pos"]
    tail = bb.get("tail", -1)
    if bb.get("tail_kind") == 0 and isinstance(tail, (list, tuple)):
        if sum(x * x for x in tail) > 1e-10:
            return tuple(pos[k] + tail[k] for k in range(3))
    elif isinstance(tail, int) and 0 <= tail < len(bones) and tail != b:
        q = bones[tail]["pos"]
        if sum((q[k] - pos[k]) ** 2 for k in range(3)) > 1e-10:
            return q
    for c in kids.get(b, ()):
        q = bones[c]["pos"]
        if sum((q[k] - pos[k]) ** 2 for k in range(3)) > 1e-10:
            return q
    return None


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _tpose_globals(bones, parents, order, human, log):
    """算出把模型摆成 VRM T-pose 的逐骨全局变换。

    VRM 规范（VRMC_vrm-1.0/tpose.md）要求静止姿势：上臂、前臂、手沿 ±X 水平伸直，掌心朝下；
    四指沿 ±X 伸直；拇指水平、指向 ±X 与正前方之间的 45°。动作数据（Unity 的 humanoid、
    VRMA、手部追踪）都是「从这个姿势开始转多少度」，静止姿势偏多少，套上任何动作后就一直
    偏多少。MMD 模型多是手臂下垂 30～40° 的 A-pose，手指微弯、拇指贴着手掌。

    在 PMX 坐标里算（左 +X、前 -Z）：
      * 上臂、前臂各转一次对齐到水平 ±X；
      * 手：先把「手腕 → 中指根」转到 ±X，再绕这个方向转，让掌心朝下（掌面横向取食指根 → 小指根）；
      * 四指每节转到 ±X、拇指每节转到水平 45°（指甲的侧向朝向从骨骼位置看不出来，只转方向）。
    父骨先转、子骨跟着走，再算自己还差多少；带付与回転的骨（腕補助 等）按付与率跟转。
    返回 {骨: (M, t)}，x' = M·x + t；没有需要转的返回 None。
    """
    pos = [b["pos"] for b in bones]
    kids = {}
    for i, p in enumerate(parents):
        kids.setdefault(p, []).append(i)
    local = {}
    sides = {}                                # side → ±1（左右手伸出去的方向）
    for side in ("left", "right"):
        ua = human.get(side + "UpperArm")
        la = human.get(side + "LowerArm")
        hd = human.get(side + "Hand")
        if hd is not None:
            ref = pos[ua] if ua is not None else (0.0, 0.0, 0.0)
            sides[side] = 1.0 if pos[hd][0] - ref[0] >= 0 else -1.0
        if ua is None or la is None:
            continue
        d1 = [pos[la][k] - pos[ua][k] for k in range(3)]
        tgt = (1.0 if d1[0] >= 0 else -1.0, 0.0, 0.0)
        sides[side] = tgt[0]
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

    # 手和手指：要转到的方向，在下面按父骨在前的顺序边走边算（要用父骨转过之后的朝向）
    aim = {}                                  # 骨 → (末端位置, 目标方向, 手的掌面 (食指根, 小指根) 或 None, side, 类别)
    for side, sx in sides.items():
        hd = human[side + "Hand"]
        chains = {}
        for f in _HAND_FINGERS:
            chains[f] = [human[side + f + s_] for s_ in _DIGIT_SEGS if side + f + s_ in human]
        mid, idx, lit = chains["Middle"][:1], chains["Index"][:1], chains["Little"][:1]
        if mid:
            aim[hd] = (pos[mid[0]], (sx, 0.0, 0.0),
                       (idx[0], lit[0]) if idx and lit else None, side, "hand")
        thumb = (sx * math.sqrt(0.5), 0.0, -math.sqrt(0.5))
        for f, ch in chains.items():
            for k, b in enumerate(ch):
                end = pos[ch[k + 1]] if k + 1 < len(ch) else _seg_end(bones, kids, b)
                if end is not None and k + 1 == len(ch) and k > 0:
                    # 末节的方向只能靠 tail / 子骨猜：FBX 转来的 PMX 末端骨 tail 是默认的朝上偏移，
                    # 和上一节差得远（> 60°）的不可信，这一节就跟着上一节转
                    prev = [pos[b][j] - pos[ch[k - 1]][j] for j in range(3)]
                    if _align(prev, [end[j] - pos[b][j] for j in range(3)])[1] > math.radians(60.0):
                        end = None
                if end is not None:
                    aim[b] = (end, thumb if f == "Thumb" else (sx, 0.0, 0.0), None, side,
                              "thumb" if f == "Thumb" else "finger")

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

    worst = {}                                # (side, 类别) → 最大转角（度）
    glob = {}
    for i in order:
        p = parents[i]
        pm, pt = glob.get(p, (_I3, (0.0, 0.0, 0.0)))
        lm = local_of(i)
        if i in aim:
            end, tgt, palm, side, kind = aim[i]
            base = pm if lm is None else _m3mul(pm, lm)
            fwd = [end[k] - pos[i][k] for k in range(3)]
            r, a = _align(_m3vec(base, fwd), tgt)
            roll = 0.0
            if palm is not None:
                # 掌面法线（掌心那一侧）：左手 前进方向 × (小指根 − 食指根)，右手反过来
                across = [pos[palm[1]][k] - pos[palm[0]][k] for k in range(3)]
                n = _m3vec(_m3mul(r, base), _cross(fwd, across))
                n = [x * tgt[0] for x in n]
                n = [n[k] - tgt[k] * sum(n[j] * tgt[j] for j in range(3)) for k in range(3)]
                if sum(x * x for x in n) > 1e-12:
                    r2, roll = _align(n, (0.0, -1.0, 0.0))
                    if roll > math.radians(1.0):
                        r = _m3mul(r2, r)
                    else:
                        roll = 0.0
            if a > math.radians(1.0) or roll:
                lm = _m3mul(_m3T(pm), _m3mul(r, base))
                local[i] = memo[i] = lm
                key = (side, kind)
                worst[key] = max(worst.get(key, 0.0), math.degrees(a))
                if roll:
                    worst[(side, "palm")] = math.degrees(roll)
        if lm is None:
            glob[i] = (pm, pt)
            continue
        # 绕骨骼自身位置旋转：x → R(x − p) + p，再叠父骨变换
        rp = _m3vec(lm, pos[i])
        lt = (pos[i][0] - rp[0], pos[i][1] - rp[1], pos[i][2] - rp[2])
        m = _m3mul(pm, lm)
        t = _m3vec(pm, lt)
        glob[i] = (m, (t[0] + pt[0], t[1] + pt[1], t[2] + pt[2]))
    for side in sides:
        w = [worst.get((side, k), 0.0) for k in ("hand", "palm", "finger", "thumb")]
        if any(w):
            log("T-pose：%s 手 %.1f° · 掌心 %.1f° · 四指最多 %.1f° · 拇指最多 %.1f°"
                % (("左" if side == "left" else "右",) + tuple(w)))
    if not local:
        return None
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
