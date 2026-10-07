# -*- coding: utf-8 -*-
"""骨骼预处理和 glTF 节点树。

  prepare_bones  付与骨合并、humanoid 映射与层级修正、拓扑排序（PMX 坐标）
  apply_tpose    A-pose → T-pose：骨骼位置和顶点一起重摆
  NodeTree       骨骼 → glTF 节点（先记父节点 + 世界坐标，最后统一换算 translation，
                 所以补占位骨、SpringBone 改挂父节点都可以随意改）
"""
from types import SimpleNamespace

from .humanoid import (REQUIRED_0X, REQUIRED_1_0, _HIPS_Y, _SYNTH, _append_aliases,
                       _clean_parents, _fix_humanoid_hierarchy, _map_humanoid, _reparent,
                       _topo_bones, _tpose_globals, _vrm_anchor)
from .mathutil import _I3, _m3vec


def prepare_bones(bones, is1, log):
    """→ SimpleNamespace(parents, alias, required, human_b, order, remap)。

    1) 付与 100% 的同位置骨（足D 等）并入付与親：权重与子骨都转过去
    2) humanoid 映射 + 层级修正（上半身 挂到 下半身 下等）
    """
    nb = len(bones)
    parents = _clean_parents(bones)
    alias = _append_aliases(bones, parents, eps=0.01)
    if alias:
        for i, dst in alias.items():
            for c in range(nb):
                if parents[c] == i:
                    _reparent(parents, c, dst)
        log("付与骨合并：%d 根（%s）→ 权重并入付与親"
            % (len(alias), "、".join(bones[i]["name"] for i in
                                    list(alias)[:6]) +
               ("…" if len(alias) > 6 else "")))
    required = REQUIRED_1_0 if is1 else REQUIRED_0X
    human_b = _map_humanoid(bones, is1, alias)
    _fix_humanoid_hierarchy(human_b, parents, bones, set(required), log)
    order, remap = _topo_bones(parents)
    return SimpleNamespace(parents=parents, alias=alias, required=required,
                           human_b=human_b, order=order, remap=remap)


def apply_tpose(verts, bones, bs, vweights, tpose, log):
    """A-pose → T-pose（线性混合蒙皮），返回 (glob, bpos, vpos, vnrm, vmat)。

    glob：每根骨的 T-pose 全局变换（没做 T-pose 时为 None）；vmat：每个顶点的混合旋转，
    表情偏移要跟着转（没动的顶点为 None）。都是 PMX 坐标。
    """
    nb = len(bones)
    vpos = [v["pos"] for v in verts]
    vnrm = [v["normal"] for v in verts]
    vmat = None
    bpos = [b["pos"] for b in bones]
    glob = _tpose_globals(bones, bs.parents, bs.order, bs.human_b, log) \
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
    return glob, bpos, vpos, vnrm, vmat


class NodeTree:
    """glTF 节点：名字、父节点、世界坐标（VRM 坐标）。骨骼按拓扑序排在最前面。"""

    def __init__(self, bones, bs, bpos, xf):
        self.names = []
        self.parent = []
        self.wpos = []
        self._remap = bs.remap
        self._of_bone = [0] * len(bones)
        self._name_set = None
        for new_i, old_i in enumerate(bs.order):
            par = bs.parents[old_i]
            self._of_bone[new_i] = len(self.names)
            self.names.append(bones[old_i]["name"] or ("bone%d" % old_i))
            self.parent.append(self._of_bone[bs.remap[par]]
                               if (par >= 0 and bs.remap[par] < new_i) else None)
            self.wpos.append(xf(bpos[old_i]))
        if not bones:                                  # 极端情况：没有骨骼
            self.names.append("root_bone")
            self.parent.append(None)
            self.wpos.append((0.0, 0.0, 0.0))

    def bone_node(self, pmx_index):
        """PMX 骨骼序号 → 节点序号。"""
        if 0 <= pmx_index < len(self._of_bone):
            return self._of_bone[self._remap[pmx_index]]
        return None

    def node_pos(self, n):
        return self.wpos[n]

    def is_ancestor(self, a, b):
        seen = set()
        while b is not None and b not in seen:
            if b == a:
                return True
            seen.add(b)
            b = self.parent[b]
        return False

    def add_node(self, name, parent, pos):
        """新增一个节点（重名时加 _2、_3 …），返回序号。"""
        if self._name_set is None:
            self._name_set = set(self.names)
        nm, k = name, 2
        while nm in self._name_set:
            nm = "%s_%d" % (name, k)
            k += 1
        self._name_set.add(nm)
        self.names.append(nm)
        self.parent.append(parent)
        self.wpos.append(pos)
        return len(self.names) - 1

    def set_parent(self, node, parent):
        self.parent[node] = parent

    def humanoid(self, human_b, required, unit, log):
        """humanoid 骨位 → 节点；缺失的必需骨位补占位骨（vrm_*）。

        unit(左, 上, 前) → 当前规范下的方向向量（1.0 左 +X 前 +Z；0.x 反过来）。
        """
        human = {k: self.bone_node(v) for k, v in human_b.items()}
        req = set(required)

        def ensure(name):
            if name in human:
                return human[name]
            pname = _vrm_anchor(name, set(human) | req)
            pnode = ensure(pname) if pname else None
            # 已有的、规范上应挂在本骨位下的 humanoid 骨（比如缺 spine 时的 chest）
            kids = [human[h] for h in list(human)
                    if _vrm_anchor(h, set(human) | {name}) == name
                    and not (pnode is not None and
                             self.is_ancestor(human[h], pnode))]
            if kids:
                kc = [sum(self.wpos[k][j] for k in kids) / len(kids) for j in range(3)]
                if pnode is not None:
                    pos = tuple((self.wpos[pnode][j] + kc[j]) * 0.5 for j in range(3))
                else:
                    pos = tuple(kc)
            else:
                off = _SYNTH.get(name, (0.0, 0.05, 0.0))
                d = unit(off[0], off[1], off[2])
                if pnode is None:
                    pos = (0.0, _HIPS_Y, 0.0) if name == "hips" else d
                else:
                    pos = tuple(self.wpos[pnode][j] + d[j] for j in range(3))
            idx = len(self.names)
            self.names.append("vrm_" + name)
            self.parent.append(pnode)
            self.wpos.append(pos)
            for k in kids:
                self.parent[k] = idx
            human[name] = idx
            return idx

        for r in required:
            if r not in human:
                ensure(r)
                log("提示：模型缺少 %s，已自动生成占位骨骼" % r, "warn")
        return human

    def gltf_nodes(self):
        """→ (glTF nodes 列表, 没有父节点的节点序号)。translation 按父节点换算成局部值。"""
        nodes = []
        top = []
        for i, nm in enumerate(self.names):
            p = self.parent[i]
            base = self.wpos[p] if p is not None else (0.0, 0.0, 0.0)
            nodes.append({"name": nm,
                          "translation": [self.wpos[i][j] - base[j] for j in range(3)]})
        for i, p in enumerate(self.parent):
            if p is not None:
                nodes[p].setdefault("children", []).append(i)
            else:
                top.append(i)
        return nodes, top
