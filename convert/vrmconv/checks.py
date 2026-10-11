# -*- coding: utf-8 -*-
"""转换后的自检：只在日志里提示，不改输出。

  follow    网格在 MMD 里跟着哪些关节动、在 VRM 里还跟不跟（付与骨没处理到之类）
  odd_bones humanoid 骨位置明显不对：在身体另一侧、离父骨太远、离网格太远（多半是把模型外面的控制骨当成了 humanoid 骨）
"""
import math

from .humanoid import _clean_parents, _vrm_anchor

MIN_VERTS = 20           # 少于这么多顶点的不提示（个别顶点权重刷错很常见，看不出来）


def follow(bones, bs, raw_weights, vweights, tol=0.3):
    """→ [(顶点数, 骨, humanoid 骨位, MMD 里的跟随率, VRM 里的跟随率)]，按顶点数从多到少。

    MMD 里顶点跟某个关节转多少：按原始权重，累加每根骨沿父骨链和付与（按付与率）得到的该关节的旋转；
    VRM 里：按最终权重，累加祖先里有该关节的骨。差 tol 以上的顶点按「主要挂在哪根骨上」归类。
    hips 不比（MMD 的 上半身 不在 下半身 下，VRM 要求在，这是有意改的）。
    """
    n = len(bones)
    if not n:
        return []
    orig = _clean_parents(bones)
    slot = {i: h for h, i in bs.human_b.items() if h != "hips"}
    memo_l, memo_m = {}, {}

    def local(i, depth=0):          # 骨自己的旋转里有哪些关节：{骨位: 比例}
        if i in memo_l:
            return memo_l[i]
        out = {slot[i]: 1.0} if i in slot else {}
        ir = bones[i].get("inherit_rot")
        if ir and depth < 16 and 0 <= ir[0] < n and ir[0] != i:
            for h, r in local(ir[0], depth + 1).items():
                out[h] = out.get(h, 0.0) + ir[1] * r
        memo_l[i] = out
        return out

    order = sorted(range(n), key=lambda i: _depth(orig, i))     # 父在前（PMX 的骨序不一定是）
    for i in order:
        out = dict(memo_m.get(orig[i], {})) if orig[i] >= 0 else {}
        for h, r in local(i).items():
            out[h] = out.get(h, 0.0) + r
        memo_m[i] = out

    vrm = {}

    def vrm_of(b):
        if b not in vrm:
            s, k, seen = set(), b, set()
            while 0 <= k < n and k not in seen:
                if k in slot:
                    s.add(slot[k])
                seen.add(k)
                k = bs.parents[k]
            vrm[b] = s
        return vrm[b]

    # 只看可能不一致的骨上的顶点：MMD 的跟随和它（并入后）在 VRM 里的祖先不一样的
    odd = set()
    for b in range(n):
        want = memo_m[b]
        got = vrm_of(bs.alias.get(b, b))
        if any(abs(r - (1.0 if h in got else 0.0)) >= tol for h, r in want.items()) or \
                any(h not in want for h in got):
            odd.add(b)
    found = {}
    for vi, wl in enumerate(raw_weights):
        if odd.isdisjoint(b for b, _ in wl):
            continue
        want = {}
        for b, w in wl:
            for h, r in memo_m[b].items():
                want[h] = want.get(h, 0.0) + w * r
        got = {}
        for b, w in vweights[vi]:
            for h in vrm_of(b):
                got[h] = got.get(h, 0.0) + w
        for h, r in want.items():
            if r - got.get(h, 0.0) >= tol:
                main = max(wl, key=lambda t: t[1])[0]
                e = found.setdefault((main, h), [0, 0.0, 0.0])
                e[0] += 1
                e[1] += r
                e[2] += got.get(h, 0.0)
    return sorted(((c, b, h, sw / c, sg / c) for (b, h), (c, sw, sg) in found.items() if c >= MIN_VERTS),
                  key=lambda t: -t[0])


def _depth(parents, i):
    d, seen = 0, set()
    while 0 <= parents[i] < len(parents) and i not in seen:
        seen.add(i)
        i = parents[i]
        d += 1
    return d


def odd_bones(bs, bpos, vpos, far=0.1, seg=0.35, side=0.02):
    """→ [(humanoid 骨位, 骨, 原因, 数值)]：位置明显不对的 humanoid 骨（PMX 坐标、T-pose 后，角色左边是 +X）。

    多半是模型外面的控制骨（调整用的「肩調整」之类）被当成了 humanoid 骨，动作时肩膀、手臂会被拉过去：
      side  左边的骨在身体右侧（或反过来）超过模型高度的 side 倍
      seg   离 humanoid 父骨位超过模型高度的 seg 倍（大腿、小腿最长也只有 1/4 左右）
      far   附近（模型高度的 far 倍以内）没有任何顶点：关节都在身体里，离皮肤顶多几厘米。用网格哈希，不逐点算
    数值：side / far 是模型高度的倍数，seg 是 (父骨位, 距离 / 模型高度)。
    """
    if not vpos or not bs.human_b:
        return []
    ys = [p[1] for p in vpos]
    h = max(ys) - min(ys)
    if h <= 1e-6:
        return []
    cell = h * far / 2
    occ = {(int(p[0] // cell), int(p[1] // cell), int(p[2] // cell)) for p in vpos}
    out = []
    for slot_, i in bs.human_b.items():
        p = bpos[i]
        if (slot_.startswith("left") and p[0] < -side * h) or (slot_.startswith("right") and p[0] > side * h):
            out.append((slot_, i, "side", abs(p[0]) / h))
            continue
        par = _vrm_anchor(slot_, bs.human_b)
        if par and math.dist(p, bpos[bs.human_b[par]]) > seg * h:
            out.append((slot_, i, "seg", (par, math.dist(p, bpos[bs.human_b[par]]) / h)))
            continue
        x, y, z = (int(c // cell) for c in p)
        if not any((x + dx, y + dy, z + dz) in occ
                   for dx in (-2, -1, 0, 1, 2) for dy in (-2, -1, 0, 1, 2) for dz in (-2, -1, 0, 1, 2)):
            out.append((slot_, i, "far", far))
    return out


def report(bones, bs, raw_weights, vweights, bpos, vpos, scale, log):
    """跑两项检查，把问题写进日志（warn）。"""
    for c, b, h, want, got in follow(bones, bs, raw_weights, vweights)[:6]:
        log("自检：%d 个顶点主要挂在 %s 上，MMD 里跟着 %s 转 %d%%，VRM 里只跟 %d%%，动作时这部分会变形"
            % (c, bones[b]["name"], h, round(want * 100), round(got * 100)), "warn")
    ys = [p[1] for p in vpos] or [0.0]
    cm = (max(ys) - min(ys)) * scale * 100
    for h, i, why, v in odd_bones(bs, bpos, vpos)[:6]:
        name = bones[i]["name"]
        if why == "side":
            log("自检：humanoid 骨 %s（%s）在身体的另一侧，可能对应错了骨骼，动作时这部分会被拉开" % (h, name), "warn")
        elif why == "seg":
            log("自检：humanoid 骨 %s（%s）离 %s 有 %d cm，可能对应错了骨骼，动作时这部分会被拉开"
                % (h, name, v[0], round(v[1] * cm)), "warn")
        else:
            log("自检：humanoid 骨 %s（%s）离网格超过 %d cm，可能对应错了骨骼，动作时这部分会被拉开"
                % (h, name, round(v * cm)), "warn")
