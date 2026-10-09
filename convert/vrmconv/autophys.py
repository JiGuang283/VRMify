# -*- coding: utf-8 -*-
"""没有物理的模型（FBX / psk / xps 转来的 PMX 常见）：按骨骼名和骨架结构自动补一套 MMD 刚体，
再交给 springbone._build_springs 按同一套规则转成 VRM SpringBone。

* 摆动链（动态刚体，mode 1）：名字像头发 / 裙摆 / 袖子 / 飘带 / 尾巴的非人形骨；
  另外挂在头骨下面、不像五官和头饰的非人形骨也算头发（Query 酱的头发骨叫 H01_Front、H02_L）。
  胸部骨、扭转骨、IK、辅助骨不动。
* 碰撞体（跟随骨骼的刚体，mode 0）：头（球）、胸 / 腰（球）、上下臂和大小腿（胶囊），
  尺寸按身高比例给。
裙摆 / 头发的区分、抽稀、硬度、碰撞的取舍都沿用 _build_springs（按挂点和高度自动判断）。
"""
import math
import re

_SWING = re.compile(
    r"hair|kami|髪|ahoge|bang|fringe|pony|twin|braid|"
    r"skirt|スカート|suso|裾|dress|"
    r"sode|sleeve|袖|"
    r"ribbon|ribon|リボン|necktie|cape|cloak|mantle|scarf|マフラー|tassel|frill|フリル|"
    r"tail|尻尾|shippo|しっぽ|coat|jacket_?(?:l|r|b|f)", re.I)
_NEVER = re.compile(
    r"mune|bust|breast|chichi|oppai|胸|pai_|^pai|twist|twst|捩|roll|(?:^|_)ik|ik(?:$|_)|helper|ctrl|"
    r"control|adj|root|dummy|ダミー|先$|tip$|nub$|ndl|offset|target|master|aim|socket|attach|"
    r"weapon|gun|holster|pocket|pouch|buckle|shell|prop|point", re.I)
_FACE = re.compile(
    r"eye|目|jaw|顎|tongue|舌|teeth|tooth|歯|mouth|口|brow|眉|lid|瞼|cheek|頬|nose|鼻|face|顔|"
    r"lip|唇|chin|smile|crease|corner|nostril|temple|forehead|lash|pupil|iris|dimple|"
    r"hat|cap|crown|glass|megane|眼鏡|horn|角|acc|ear|耳|head_?end|headtop|top$", re.I)

_ARM_SEG = (("leftUpperArm", "leftLowerArm"), ("leftLowerArm", "leftHand"),
            ("rightUpperArm", "rightLowerArm"), ("rightLowerArm", "rightHand"))
_LEG_SEG = (("leftUpperLeg", "leftLowerLeg"), ("leftLowerLeg", "leftFoot"),
            ("rightUpperLeg", "rightLowerLeg"), ("rightLowerLeg", "rightFoot"))


def _rot_for_dir(d):
    """胶囊沿局部 Y：求 PMX 刚体旋转（Ry·Rx·Rz，取 ry=0）使 Y 轴指向 d。"""
    n = math.sqrt(sum(x * x for x in d)) or 1.0
    dx, dy, dz = (x / n for x in d)
    rz = math.asin(max(-1.0, min(1.0, -dx)))
    rx = math.atan2(dz, dy)
    return (rx, 0.0, rz)


def _rb(name, bone, pos, mode, radius, height=0.0, rot=(0.0, 0.0, 0.0)):
    return {"name": name, "name_en": name, "bone": bone, "group": 1 if mode else 0, "mask": 0xFFFF,
            "shape": 2 if height > 0 else 0, "size": (radius, height, 0.0), "pos": tuple(pos),
            "rot": tuple(rot), "mass": 1.0, "move_damp": 0.5, "rot_damp": 0.5, "repulsion": 0.0,
            "friction": 0.5, "mode": mode}


def _leaf_tails(model, dyn, children):
    """链尾骨没有子骨时（FBX 里的单根发束骨很常见），按它带动的顶点估计长度：
    以这根骨为主权重的顶点里离骨头最远的那个，取 90% 当 tail。
    不估的话 tail 只有默认的一小截，整条链被当成「过短」不做物理。"""
    bones = model["bones"]
    dset = set(dyn)
    leaves = [b for b in dyn if not any(c in dset for c in children[b])
              and not any(c not in dset for c in children[b])]
    if not leaves:
        return
    far = {b: (0.0, None) for b in leaves}
    for v in model.get("vertices") or []:
        ws = v.get("wweights") or [1.0]
        bs = v.get("wbones") or []
        if not bs:
            continue
        k = max(range(min(len(bs), len(ws)) or 1), key=lambda i: ws[i] if i < len(ws) else 0)
        b = bs[k]
        if b in far:
            p, q = v["pos"], bones[b]["pos"]
            d = sum((p[i] - q[i]) ** 2 for i in range(3))
            if d > far[b][0]:
                far[b] = (d, p)
    for b, (d, p) in far.items():
        if p is None:
            continue
        q = bones[b]["pos"]
        off = tuple((p[i] - q[i]) * 0.9 for i in range(3))
        cur = bones[b].get("tail")
        cur_len = math.sqrt(sum(x * x for x in cur)) if bones[b].get("tail_kind") == 0 and cur else 0.0
        if math.sqrt(d) * 0.9 > cur_len:
            bones[b]["tail_kind"] = 0
            bones[b]["tail"] = off


def needed(model):
    """模型里没有任何会动的刚体（mode 1/2）时才自动补。"""
    return not any(rb.get("mode") in (1, 2) for rb in model.get("rigid_bodies") or [])


def synthesize(model, parents, alias, human_b, log=None):
    """往 model["rigid_bodies"] 里追加自动生成的刚体。返回 (摆动骨数, 碰撞体数)。"""
    bones = model["bones"]
    nb = len(bones)
    human = set(human_b.values())
    head = human_b.get("head")
    ys = [v["pos"][1] for v in model.get("vertices") or []] or [b["pos"][1] for b in bones] or [0.0]
    H = max(1e-6, max(ys) - min(ys))           # 模型身高（PMX 单位）
    children = [[] for _ in range(nb)]
    for i, p in enumerate(parents):
        if 0 <= p < nb:
            children[p].append(i)

    def under(b, anc):
        p = parents[b]
        seen = set()
        while 0 <= p < nb and p not in seen:
            if p == anc:
                return True
            if p in human:                    # 中间隔着别的人形骨：不算直接挂在 anc 下面
                return False
            seen.add(p)
            p = parents[p]
        return False

    def swing(b):
        if b in human or b in alias:
            return False
        nm = bones[b]["name"] or ""
        if _NEVER.search(nm):
            return False
        if _SWING.search(nm):
            return True
        # 名字看不出来、挂在头下面的：只有模型里一根名字像头发的骨都没有时才当头发
        # （Query 酱的 H01_Front）。有表情骨架的模型头下面是几十根嘴唇 / 眼睑骨，不能动
        return by_struct and head is not None and under(b, head) and not _FACE.search(nm)

    by_struct = not any(re.search(r"hair|kami|髪|bang|fringe|pony|twin|braid", bones[b]["name"] or "", re.I)
                        for b in range(nb) if b not in human)
    dyn = [b for b in range(nb) if swing(b)]
    _leaf_tails(model, dyn, children)
    rbs = model.setdefault("rigid_bodies", [])
    for b in dyn:
        nm = (bones[b]["name"] or "").lower()
        lower = bool(re.search(r"skirt|スカート|suso|裾|dress", nm))
        rbs.append(_rb("auto_" + (bones[b]["name"] or str(b)), b, bones[b]["pos"], 1,
                       H * (0.02 if lower else 0.012)))

    # ---- 碰撞体
    n_col = 0
    pos = {k: bones[v]["pos"] for k, v in human_b.items()}
    if "head" in human_b:
        c = pos["head"]
        rbs.append(_rb("auto_col_head", human_b["head"], (c[0], c[1] + H * 0.045, c[2]), 0, H * 0.055))
        n_col += 1
    for k, r in (("upperChest", 0.065), ("chest", 0.065), ("spine", 0.06), ("hips", 0.06)):
        if k in human_b and not (k == "chest" and "upperChest" in human_b):
            rbs.append(_rb("auto_col_" + k, human_b[k], pos[k], 0, H * r))
            n_col += 1
    for a, b in _ARM_SEG + _LEG_SEG:
        if a not in human_b or b not in human_b:
            continue
        p0, p1 = pos[a], pos[b]
        d = [p1[k] - p0[k] for k in range(3)]
        ln = math.sqrt(sum(x * x for x in d))
        if ln < 1e-6:
            continue
        r = H * (0.04 if "UpperLeg" in a else 0.03 if "LowerLeg" in a else 0.028)
        mid = [(p0[k] + p1[k]) / 2 for k in range(3)]
        rbs.append(_rb("auto_col_" + a, human_b[a], mid, 0, r, max(0.0, ln - 2 * r) or ln * 0.5,
                       _rot_for_dir(d)))
        n_col += 1
    if log and dyn:
        log("物理：模型不含物理刚体，已按骨骼名和骨架结构自动添加 %d 根摆动骨（头发、裙摆、袖子、飘带等）"
            "和 %d 个身体碰撞体" % (len(dyn), n_col))
    return len(dyn), n_col
