# -*- coding: utf-8 -*-
"""PMX 刚体物理 → VRMC_springBone（VRM 1.0）。"""
import math

from .mathutil import _I3, _m3mul, _m3vec


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
