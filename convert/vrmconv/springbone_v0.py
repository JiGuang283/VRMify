# -*- coding: utf-8 -*-
"""VRM 0.x 的 SpringBone（secondaryAnimation）：由 springbone._build_springs 算好的 1.0 结构换算。

参数换算、碰撞关系、裙摆处理只在 1.0 那边维护一份，这里只处理两种结构的差别：

  * 0.x 的 boneGroups.bones 只列根骨，**整棵子树都参与模拟**，每节的方向取它的第一个子节点
    （three-vrm 的 _v0Import、UniVRM、Blender 官方插件都是这样读的）。1.0 里有些骨只是跟着
    上一个关节刚性转动（抽稀掉的短节、链末端的配饰），照搬过去会全变成摆动骨。
    fold()：把这些骨的权重并到最近的摆动关节上、子节点改挂过去，再由 _prune_nodes 删掉——
    它们在 1.0 里本来就随那个关节刚性转动，效果完全一样。尾节点留下当不带权重的叶子。
    order_children()：每个关节的下一节排在第一个子节点。
  * 同一节点落在两个组的子树里会被模拟两次：从别的链上分叉出来的链并入上层链的组；
    组内的碰撞体按关节数多数决定。
  * 参数是每组一套：硬度 / 重力 / 阻尼取组内中位数（同一条链内本来就几乎一样）；碰撞半径也取
    中位数，静止时会和碰撞体重叠的，给这一组配一个缩小的碰撞体副本（缩不下去才取消这处碰撞）。
    取最小值的话，1.0 里为贴着腿、贴着衣服单独缩小过的那几节会让整条链都几乎不碰撞，
    反过来不缩小碰撞体的话飘带一开始模拟就被推开 3～4 cm。
  * 碰撞体只有球：胶囊沿轴线每隔一个半径放一个球（两球之间最窄处是半径的 0.87 倍；
    更密的球实测效果差不多，球数却翻倍）。offset 是 Unity 坐标，Z 和 glTF 相反。
  * 参数相同的链也不合并成一组：调整页按组调参数、开关，一组一条链才对得上。
  * 硬度的键名是规范里拼错的 stiffiness。
"""
import math
import statistics


def _joint_sets(spring_ext):
    """→ (模拟的关节节点, 尾节点, 关节 → 链上的下一节)。"""
    sim, tails, nxt = set(), set(), {}
    for sp in spring_ext["springs"]:
        js = [j["node"] for j in sp["joints"]]
        sim.update(js[:-1])
        tails.add(js[-1])
        for a, b in zip(js, js[1:]):
            nxt[a] = b
    return sim, tails, nxt


def _sim_ancestor(parent_of, sim, x):
    p = parent_of(x)
    seen = set()
    while p is not None and p not in sim and p not in seen:
        seen.add(p)
        p = parent_of(p)
    return p if p in sim else None


def fold(tree, bones, vweights, spring_ext, keep_nodes, log=None):
    """组根子树里不是摆动关节的节点：权重并到最近的摆动关节、子节点改挂过去。返回新的 vweights。

    在 _prune_nodes 之前调用（节点号是 tree 的）。keep_nodes：不动的节点（humanoid 骨）。
    """
    sim, tails, _ = _joint_sets(spring_ext)
    par = tree.parent
    kids = [[] for _ in par]
    for i, p in enumerate(par):
        if p is not None:
            kids[p].append(i)
    up = lambda x: par[x]                                  # noqa: E731
    roots = [sp["joints"][0]["node"] for sp in spring_ext["springs"]]
    tops = [r for r in roots if _sim_ancestor(up, sim, r) is None]
    sub, stack = set(), list(tops)
    while stack:
        x = stack.pop()
        if x not in sub:
            sub.add(x)
            stack.extend(kids[x])
    target = {}                                            # 节点 → 并到哪个关节
    for x in sub - sim - set(keep_nodes):
        a = _sim_ancestor(up, sim, x)
        if a is not None:
            target[x] = a
    remove = {x for x in target if x not in tails}

    bone_of = {}
    for b in range(len(bones)):
        n = tree.bone_node(b)
        if n is not None:
            bone_of.setdefault(n, b)
    bmap = {}
    for b in range(len(bones)):
        n = tree.bone_node(b)
        if n in target and target[n] in bone_of:
            bmap[b] = bone_of[target[n]]
    out, n_verts = [], 0
    for wl in vweights:
        if not any(b in bmap and w > 0 for b, w in wl):
            out.append(wl)
            continue
        n_verts += 1
        acc = {}
        for b, w in wl:
            b = bmap.get(b, b)
            acc[b] = acc.get(b, 0.0) + w
        out.append(sorted(acc.items(), key=lambda t: -t[1]))

    for x in range(len(par)):
        if par[x] in remove and x not in remove:
            tree.set_parent(x, target[par[x]])
    for c in spring_ext.get("colliders", []):
        if c["node"] in remove:
            a = target[c["node"]]
            d = [tree.wpos[c["node"]][k] - tree.wpos[a][k] for k in range(3)]
            for v in c["shape"].values():
                for f in ("offset", "tail"):
                    if f in v:
                        v[f] = [v[f][k] + d[k] for k in range(3)]
            c["node"] = a
    if log and (remove or bmap):
        log("SpringBone（0.x）：%d 根随关节刚性转动的骨（中间节、链末端的配饰）权重并到关节上（%d 个顶点），"
            "0.x 会让组里所有子骨都摆动" % (len(bmap), n_verts))
    return out


def order_children(nodes, spring_ext):
    """每个关节的下一节放到 children[0]：0.x 按第一个子节点定这一节的方向。"""
    _, _, nxt = _joint_sets(spring_ext)
    for a, b in nxt.items():
        ch = nodes[a].get("children") or []
        if b in ch and ch[0] != b:
            ch.remove(b)
            ch.insert(0, b)


def _world(nodes):
    parent = {}
    for i, nd in enumerate(nodes):
        for c in nd.get("children", ()):
            parent[c] = i
    memo = {}

    def pos(i):
        if i not in memo:
            t = nodes[i].get("translation") or [0.0, 0.0, 0.0]
            p = parent.get(i)
            memo[i] = tuple(t) if p is None else tuple(pos(p)[k] + t[k] for k in range(3))
        return memo[i]
    return pos, parent


def spheres(shape):
    """1.0 的碰撞形状 → [(offset, radius)]（glTF 局部坐标）。胶囊沿轴线每隔一个半径放一个球。"""
    if "sphere" in shape:
        s = shape["sphere"]
        return [(tuple(s["offset"]), s["radius"])]
    c = shape["capsule"]
    a, b, r = c["offset"], c["tail"], c["radius"]
    n = max(2, int(math.ceil(math.dist(a, b) / max(r, 1e-4))) + 1)
    return [(tuple(a[k] + (b[k] - a[k]) * i / (n - 1) for k in range(3)), r) for i in range(n)]


def _v3(v):
    """glTF → 0.x 的 {x, y, z}（Unity 坐标：Z 取反）。"""
    return {"x": round(v[0], 6), "y": round(v[1], 6), "z": round(-v[2], 6) or 0.0}


def to_secondary(spring_ext, nodes, center=None, log=None):
    """1.0 的 VRMC_springBone（节点号已是最终的）→ 0.x 的 secondaryAnimation。"""
    pos, parent = _world(nodes)
    sim, _, _ = _joint_sets(spring_ext)
    cols = spring_ext.get("colliders", [])
    cgs = spring_ext.get("colliderGroups", [])
    sph = [spheres(c["shape"]) for c in cols]

    groups = {}                                            # 顶层根 → [spring]
    for sp in spring_ext["springs"]:
        t = sp["joints"][0]["node"]
        while True:
            a = _sim_ancestor(parent.get, sim, t)
            if a is None:
                break
            t = a
        groups.setdefault(t, []).append(sp)

    v0_groups, v0_key = [], {}

    def collider_group(ci, radius=None):
        key = (ci, None if radius is None else round(radius, 5))
        if key not in v0_key:
            v0_key[key] = len(v0_groups)
            v0_groups.append({"node": cols[ci]["node"], "colliders": [
                {"offset": _v3(o), "radius": round(r if radius is None else radius, 5)}
                for o, r in sph[ci]]})
        return v0_key[key]

    def clearance(p, ci):
        base = pos(cols[ci]["node"])
        return min(math.dist(p, [base[k] + o[k] for k in range(3)]) - r for o, r in sph[ci])

    bone_groups = []
    n_shrunk = n_dropped = 0
    for top, sps in groups.items():
        js = [j for sp in sps for j in sp["joints"][:-1]]
        tips = [pos(j["node"]) for sp in sps for j in sp["joints"][1:]]

        def med(k, d):
            return statistics.median(j.get(k, d) for j in js)
        hit = med("hitRadius", 0.0)
        # 分叉链并进来以后整组只能共用一套碰撞体：按关节数多数决定（组内至少一半的关节在 1.0 里
        # 碰它才保留）。取并集的话，挂在长发上的一个小坠子（MMD 里和全身都碰）会让整条长发也去碰
        # 腿和手臂，实测摆幅只剩一半
        votes = {}
        for sp in sps:
            for ci in {ci for g in sp.get("colliderGroups", ()) for ci in cgs[g]["colliders"]}:
                votes[ci] = votes.get(ci, 0) + len(sp["joints"]) - 1
        ids = []
        for ci in sorted(votes):
            if votes[ci] * 2 < len(js):
                continue
            gap = min(clearance(p, ci) for p in tips)
            if gap >= hit:
                ids.append(collider_group(ci))
                continue
            r0 = sph[ci][0][1]
            r1 = r0 - (hit - gap) - 0.002
            if r1 >= max(0.01, r0 * 0.4):
                ids.append(collider_group(ci, r1))
                n_shrunk += 1
            else:
                n_dropped += 1
        gd = js[0].get("gravityDir", [0.0, -1.0, 0.0])
        bone_groups.append({
            "comment": sps[0].get("name") or "spring%d" % len(bone_groups),
            "stiffiness": round(med("stiffness", 1.0), 4),
            "gravityPower": round(med("gravityPower", 0.0), 4),
            "gravityDir": _v3(gd),
            "dragForce": round(med("dragForce", 0.5), 4),
            "center": -1 if center is None else center,
            "hitRadius": round(hit, 5),
            "bones": [top],
            "colliderGroups": sorted(set(ids)),
        })
    if log:
        n_nested = len(spring_ext["springs"]) - len(groups)
        log("SpringBone（0.x）：%d 条链 → %d 个摆动组%s · 碰撞组 %d（胶囊换成 %d 个球）"
            % (len(spring_ext["springs"]), len(bone_groups),
               "（%d 条分叉链并入上层链的组）" % n_nested if n_nested else "",
               len(v0_groups), sum(len(g["colliders"]) for g in v0_groups)))
        if n_shrunk or n_dropped:
            log("SpringBone（0.x）：每组只有一个碰撞半径，静止时会重叠的碰撞体缩小 %d 处、取消 %d 处"
                % (n_shrunk, n_dropped))
    return {"boneGroups": bone_groups, "colliderGroups": v0_groups}
