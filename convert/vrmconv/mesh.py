# -*- coding: utf-8 -*-
"""网格：顶点权重整理、按材质切成 glTF primitive、写 accessor、按表情分成几个网格。"""
import heapq
import math

import vrmio


def _weights_of(v, nb, alias):
    """一个顶点的 [(PMX 骨, 权重)]（见 vertex_weights）。"""
    wt = v["wtype"]
    bs = [x for x in v["wbones"] if 0 <= x < nb]
    if not bs:
        bs = [0]
    if wt == 0:
        return [(alias.get(bs[0], bs[0]), 1.0)]
    if wt in (1, 3):                 # BDEF2 / SDEF（SDEF 退化成线性混合）
        # 和下面的通用算法结果完全相同，只是不建字典、不排序（顶点大多是这种）
        w0 = v["wweights"][0] if v["wweights"] else 1.0
        a = alias.get(bs[0], bs[0])
        b = bs[1] if len(bs) > 1 else bs[0]
        b = alias.get(b, b)
        wa, wb = max(0.0, w0), max(0.0, 1.0 - w0)
        if a == b:
            tot = wa + wb
            return [(a, 1.0 if tot <= 1e-9 else tot / tot)]
        if wb > wa:
            a, b, wa, wb = b, a, wb, wa
        tot = wa + wb
        if tot <= 1e-9:
            return [(a, 1.0)]
        return [(a, wa / tot), (b, wb / tot)]
    # BDEF4 / QDEF
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


def vertex_weights(verts, nb, alias, base=None):
    """逐顶点 [(PMX 骨, 权重)]：已归一、已按付与别名合并、最多 4 个。

    base：同一批顶点不带别名（alias={}）的结果。给了就只重算挂在别名骨上的顶点——网格要带别名的、
    骨骼表情烘焙要不带的，两份不用各从头算一遍。
    """
    if not nb:
        return [[(0, 1.0)] for _ in verts]
    if base is not None and 0 not in alias:          # 没有有效骨的顶点算到骨 0 上
        if not alias:
            return base
        keys = set(alias)
        out = list(base)
        for i, v in enumerate(verts):
            if not keys.isdisjoint(v["wbones"]):
                out[i] = _weights_of(v, nb, alias)
        return out
    return [_weights_of(v, nb, alias) for v in verts]


LIMB_PART = {"Shoulder": "肩", "UpperArm": "上臂", "LowerArm": "前臂", "Hand": "手",
             "UpperLeg": "大腿", "LowerLeg": "小腿", "Foot": "脚", "Toes": "脚趾"}


def mirror_mismatches(verts, raw_weights, vweights, human, min_count=20):
    """左右对称位置的顶点绑在四肢的不同部位上（一边大腿、一边小腿之类）→
    [(顶点对数, 右侧骨, 右侧部位, 左侧骨, 左侧部位)]（骨是 PMX 索引，部位是 humanoid 骨位去掉 left / right）。

    多半是模型镜像权重时弄错了：在 MMD 里同样会变形（膝盖一弯，绑在大腿上的鞋子就被扯开），
    转换器不改模型，只在日志里提示。PMX 坐标里角色的左边是 +X。只算两边都主要（≥ 0.5）挂在
    四肢的 humanoid 骨上、而且各自几乎不挂对面那个部位（< 0.2）的顶点对，关节处正常的混合权重不算。
    """
    part = {}
    for s, b in human.items():
        for side in ("left", "right"):
            if s.startswith(side):
                part[b] = s[len(side):]
    if not part:
        return []

    def key(x, y, z):
        return (round(x, 3), round(y, 3), round(z, 3))

    def dom(wl):
        return max(wl, key=lambda t: t[1])

    def on(wl, p):
        return sum(w for b, w in wl if part.get(b) == p)

    left = {key(*v["pos"]): i for i, v in enumerate(verts) if v["pos"][0] > 1e-4}
    groups = {}
    for i, v in enumerate(verts):
        x, y, z = v["pos"]
        if x >= -1e-4:
            continue
        j = left.get(key(-x, y, z))
        if j is None:
            continue
        (br, wr), (bl, wl) = dom(vweights[i]), dom(vweights[j])
        pr, pl = part.get(br), part.get(bl)
        if not pr or not pl or pr == pl or wr < 0.5 or wl < 0.5:
            continue
        if on(vweights[i], pl) >= 0.2 or on(vweights[j], pr) >= 0.2:
            continue
        k = (dom(raw_weights[i])[0], pr, dom(raw_weights[j])[0], pl)
        groups[k] = groups.get(k, 0) + 1
    return sorted(((n,) + k for k, n in groups.items() if n >= min_count), reverse=True)


def build_primitives(model, xf, xf_dir, vpos, vnrm, vweights, reverse_winding, make_material):
    """按 PMX 材质切网格。make_material(PMX 材质号, 材质) → glTF 材质号（只给有面的材质调用）。

    返回 (prims, mat_gidx)：prims 每项含 local（PMX 顶点 → 本 primitive 顶点）、顶点数据、
    索引和 glTF 材质号；mat_gidx：PMX 材质号 → glTF 材质号（0 面的材质不在里面）。
    """
    verts = model["vertices"]
    faces = model["faces"]
    prims = []
    mat_gidx = {}
    cursor = 0
    for mi_, mat in enumerate(model["materials"]):
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
            # 这里先写 PMX 骨索引 b，去掉无用节点后由 remap_joints 换成 joints 里的位置。以前错写成节点号 bone_node(b)，
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
                      "mat": mat_gidx[mi_], "mat_name": mat.get("name") or "", "tmap": {}})
    return prims, mat_gidx


def remap_joints(prims, slot):
    """JOINTS_0 里的 PMX 骨序号 → skin.joints 里的位置（slot：PMX 骨 → 位置）。

    不在 slot 里的骨（去掉的节点）只会以权重 0 出现（凑满 4 个的占位），换成本顶点第一根骨。
    """
    for pr in prims:
        jn = pr["jn"]
        for k in range(0, len(jn), 4):
            first = slot.get(jn[k], 0)
            for q in range(k, k + 4):
                jn[q] = slot.get(jn[q], first)


def write_accessors(gb, prims):
    """顶点属性和索引写进 GLB，结果放在 pr["acc"] / pr["indices"]。"""
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


# 多分一个网格折合多少「顶点 × morph target」：省下的不到这么多（密集存储约 1.2 MB）就不分
MESH_COST = 100000
MAX_MESHES = 8


def group_meshes(prims, n_targets):
    """按表情把 primitive 分成几个网格 → [(primitive 序号列表, 全局 morph target 序号列表)]，按首个 primitive 排。

    glTF 要求同一个网格里每个 primitive 带同样的 morph target，读取端（three.js、Unity）又多半按
    「顶点数 × target 数」展开成密集数组：全塞在一个网格里时，身体、衣服也要陪着脸带上全部表情
    （13 万顶点 × 180 个表情 ≈ 270 MB）。这里像 VRoid 的 Face / Body / Hair 那样分开：从「target 集合
    相同的放一起」开始，每次合并代价最小的两组（代价 = 网格顶点数 × target 数），直到再合并多出的
    比 MESH_COST 多、且网格数不超过 MAX_MESHES。哪个 primitive 都没动到的 target 放进 target 最多的网格，
    让表情还能引用它。
    """
    by_set = {}
    for i, pr in enumerate(prims):
        by_set.setdefault(frozenset(pr["tmap"]), []).append(i)
    groups = {}                      # id → [target 集合, 顶点数, primitive 序号]
    for k, (ts, ids) in enumerate(by_set.items()):
        groups[k] = [ts, sum(prims[i]["vcount"] for i in ids), ids]

    def extra(a, b):
        ta, va, _ = groups[a]
        tb, vb, _ = groups[b]
        return (va + vb) * len(ta | tb) - va * len(ta) - vb * len(tb)

    heap = [(extra(a, b), a, b) for a in groups for b in groups if a < b]
    heapq.heapify(heap)
    nxt = len(groups)
    while heap and len(groups) > 1:
        d, a, b = heapq.heappop(heap)
        if a not in groups or b not in groups:
            continue
        if d > MESH_COST and len(groups) <= MAX_MESHES:
            break
        ta, va, ia = groups.pop(a)
        tb, vb, ib = groups.pop(b)
        groups[nxt] = [ta | tb, va + vb, sorted(ia + ib)]
        for c in list(groups):
            if c != nxt:
                heapq.heappush(heap, (extra(c, nxt), c, nxt))
        nxt += 1
    out = sorted(([ids, set(ts)] for ts, _, ids in groups.values()), key=lambda g: g[0][0])
    used = set().union(*(ts for _, ts in out))
    orphans = set(range(n_targets)) - used
    if orphans:
        max(out, key=lambda g: len(g[1]))[1].update(orphans)
    return [(ids, sorted(ts)) for ids, ts in out]


def _zero_target(gb, pr, sparse):
    """这个 primitive 上的全零 morph target（同一个 primitive 共用一个）。"""
    if "zero" not in pr:
        if sparse:                   # 稀疏 accessor 至少要一项
            pr["zero"] = gb.add_sparse_accessor(pr["vcount"], [], [], "VEC3")
        else:
            pr["zero"] = gb.add_accessor([0.0] * (pr["vcount"] * 3), vrmio.FLOAT, "VEC3", 34962)
    return pr["zero"]


def build_meshes(gb, prims, groups, target_names, name, sparse=True, prim_names=False):
    """group_meshes 的分组 → (glTF meshes, where)。where[全局 target 序号] = [(网格号, 网格内序号)]。

    prim_names：morph target 名也写进每个 primitive 的 extras（VRM 0.x：UniVRM 自己导出时两处都写，
    老版本的读取端只看 primitive，读不到时 BlendShape 名变成编号）。"""
    meshes = []
    where = [[] for _ in target_names]
    for mi, (ids, slots) in enumerate(groups):
        m = {"name": name if len(groups) == 1 else _mesh_name(prims, ids, name), "primitives": []}
        for i in ids:
            pr = prims[i]
            p = {"attributes": pr["acc"], "indices": pr["indices"], "material": pr["mat"]}
            if slots:
                p["targets"] = [{"POSITION": pr["tmap"][s] if s in pr["tmap"] else _zero_target(gb, pr, sparse)}
                                for s in slots]
                if prim_names:
                    p["extras"] = {"targetNames": [target_names[s] for s in slots]}
            m["primitives"].append(p)
        if slots:
            m["extras"] = {"targetNames": [target_names[s] for s in slots]}
        for j, s in enumerate(slots):
            where[s].append((mi, j))
        meshes.append(m)
    used = set()
    for m in meshes:                 # 网格名会成为 Unity 里的物体名，别重
        base, k = m["name"], 2
        while m["name"] in used:
            m["name"] = "%s_%d" % (base, k)
            k += 1
        used.add(m["name"])
    return meshes, where


def _mesh_name(prims, ids, name):
    """几个网格时按里面最大的 primitive 的材质起名（在 Unity 的层级里一眼能认出是哪一块）。"""
    big = max(ids, key=lambda i: prims[i]["vcount"])
    return prims[big].get("mat_name") or name


def remap_binds(expr, where, mesh_nodes, is1):
    """表情绑定里的全局 morph target 序号 → 各网格里的（一个 target 分在几个网格时拆成几个绑定）。

    1.0 的 node 是网格节点（mesh_nodes[网格号]），0.x 的 mesh 是网格号。哪个网格都没有的 target
    （group_meshes 已经避免）就丢掉这个绑定。
    """
    if is1:
        for grp in (expr["preset"], expr["custom"]):
            for obj in grp.values():
                if "morphTargetBinds" not in obj:
                    continue
                obj["morphTargetBinds"] = [{"node": mesh_nodes[m], "index": j, "weight": b["weight"]}
                                           for b in obj["morphTargetBinds"] for m, j in where[b["index"]]]
                if not obj["morphTargetBinds"]:
                    del obj["morphTargetBinds"]
    else:
        for g in expr["blend_groups"]:
            g["binds"] = [{"mesh": m, "index": j, "weight": b["weight"]}
                          for b in g["binds"] for m, j in where[b["index"]]]
