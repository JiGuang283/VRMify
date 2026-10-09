# -*- coding: utf-8 -*-
"""网格：顶点权重整理、按材质切成 glTF primitive、写 accessor。"""
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
    return prims, mat_gidx


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
