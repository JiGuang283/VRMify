# -*- coding: utf-8 -*-
"""VRM 表情只能动顶点（morph target）、材质颜色和贴图偏移，PMX 里另外两种表情要先「烘焙」：

  BoneBaker   骨骼表情（kind 2，寄り目 / +目上 / 齿轮旋转 …）→ 顶点偏移：在静止姿势下
              按表情给的平移 / 旋转摆骨（付与骨跟着付与親转、子骨跟着父骨），线性混合蒙皮
              算出每个顶点的位移。以前直接丢掉，含骨骼表情的グループ表情也就缺了一块。
  gaze_offsets 视线表情（lookUp / lookDown / lookLeft / lookRight）：转左右眼骨的骨骼表情，
              同样烘焙成顶点偏移。lookAt 仍然是 bone 类型；有的程序（游戏的表情动画）
              直接播这四个表情，模型里没有就看不到眼珠动。
  uv_binds    UV 表情（kind 3，漫符 / 表情贴图切换）→ 贴图的缩放 + 偏移（VRM 1.0 的
              textureTransformBinds、0.x 的 _MainTex_ST）。只有整个材质的 UV 按同一个
              仿射变换移动时才能表示（漫符那种一个材质一块面片的正好是）。

都在 PMX 坐标里算（左手系，脸朝 -Z、左手 +X）；结果和普通顶点表情一样交给 expressions。
"""
import math

from .mathutil import _I3, _axis_angle, _m3mul, _m3vec, _to_axis_angle
from .mesh import vertex_weights


def _quat_m3(q):
    """PMX 骨骼表情的四元数 (x, y, z, w) → 旋转矩阵。

    MMD 的矩阵是行向量约定（v' = v·M），换成列向量正好是标准公式，数值上不用改符号。
    """
    x, y, z, w = q
    s = math.sqrt(x * x + y * y + z * z)
    if s < 1e-9:
        return _I3
    return _axis_angle((x / s, y / s, z / s), 2.0 * math.atan2(s, w))


def _scale_rot(m, rate):
    """旋转按比例缩放（付与率）：轴不变、角度 × rate。"""
    if m is _I3 or abs(rate - 1.0) < 1e-9:
        return m
    ax, ang = _to_axis_angle(m)
    if abs(ang * rate) < 1e-9:
        return _I3
    return _axis_angle(ax, ang * rate)


class BoneBaker:
    """骨骼表情 → {顶点: (dx, dy, dz)}（PMX 坐标）。"""

    def __init__(self, model, weights=None):
        """weights：已经算好的不带付与别名的顶点权重（mesh.vertex_weights(..., {})），没给时用到再算。"""
        self.bones = model["bones"]
        self.verts = model["vertices"]
        self._weights = weights
        self._users = None

    def _vertex_weights(self):
        if self._users is None:
            if self._weights is None:
                self._weights = vertex_weights(self.verts, len(self.bones), {})
            users = {}
            for vi, wl in enumerate(self._weights):
                for b, _ in wl:
                    users.setdefault(b, []).append(vi)
            self._users = users
        return self._weights

    def deltas(self, offsets):
        """offsets：[(骨, (tx, ty, tz), (qx, qy, qz, qw))]，即 PMX 骨骼表情的 offsets。"""
        bones = self.bones
        nb = len(bones)
        loc_r, loc_t = {}, {}
        for b, t, q in offsets:
            if not (0 <= b < nb):
                continue
            r = _quat_m3(q)
            if r is not _I3:
                loc_r[b] = _m3mul(r, loc_r.get(b, _I3))
            if abs(t[0]) + abs(t[1]) + abs(t[2]) > 1e-9:
                o = loc_t.get(b, (0.0, 0.0, 0.0))
                loc_t[b] = (o[0] + t[0], o[1] + t[1], o[2] + t[2])
        if not loc_r and not loc_t:
            return {}

        # 付与：付与先的局部旋转 / 移动 = 自己的 + 付与親的 × 付与率（付与親也可能是付与骨）
        eff = {}

        def local(b, depth=0):
            if b in eff:
                return eff[b]
            r, t = loc_r.get(b, _I3), loc_t.get(b, (0.0, 0.0, 0.0))
            if depth < 32:
                ir = bones[b].get("inherit_rot")
                if ir and 0 <= ir[0] < nb and ir[0] != b:
                    rs = _scale_rot(local(ir[0], depth + 1)[0], ir[1])
                    if rs is not _I3:
                        r = rs if r is _I3 else _m3mul(rs, r)
                im = bones[b].get("inherit_mov")
                if im and 0 <= im[0] < nb and im[0] != b:
                    ts = local(im[0], depth + 1)[1]
                    t = (t[0] + ts[0] * im[1], t[1] + ts[1] * im[1], t[2] + ts[2] * im[1])
            eff[b] = (r, t)
            return eff[b]

        # 全局：G_b = G_父 ∘ 平移(骨位置 + t) ∘ R ∘ 平移(-骨位置)，只记不是恒等的
        glob = {}
        state = {}
        for start in range(nb):
            stack = [start]
            while stack:
                b = stack[-1]
                if b in state:
                    stack.pop()
                    continue
                p = bones[b]["parent"]
                if 0 <= p < nb and p not in state and p != b and len(stack) < nb:
                    stack.append(p)
                    continue
                stack.pop()
                r, t = local(b)
                par = glob.get(p) if (0 <= p < nb and p != b) else None
                if r is _I3 and abs(t[0]) + abs(t[1]) + abs(t[2]) < 1e-12 and par is None:
                    state[b] = True
                    continue
                pos = bones[b]["pos"]
                rp = _m3vec(r, pos)
                lt = (pos[0] + t[0] - rp[0], pos[1] + t[1] - rp[1], pos[2] + t[2] - rp[2])
                if par is None:
                    glob[b] = (r, lt)
                else:
                    pm, pt = par
                    q = _m3vec(pm, lt)
                    glob[b] = (_m3mul(pm, r), (q[0] + pt[0], q[1] + pt[1], q[2] + pt[2]))
                state[b] = True
        if not glob:
            return {}

        weights = self._vertex_weights()
        touched = set()
        for b in glob:
            touched.update(self._users.get(b, ()))
        out = {}
        for vi in touched:
            p = self.verts[vi]["pos"]
            x = y = z = 0.0
            for b, w in weights[vi]:
                g = glob.get(b)
                if g is None:
                    q = p
                else:
                    q = _m3vec(g[0], p)
                    q = (q[0] + g[1][0], q[1] + g[1][1], q[2] + g[1][2])
                x += q[0] * w
                y += q[1] * w
                z += q[2] * w
            d = (x - p[0], y - p[1], z - p[2])
            if abs(d[0]) + abs(d[1]) + abs(d[2]) > 1e-7:
                out[vi] = d
        return out


# 视线表情的角度：VRoid 的 lookAt 量程（水平 / 垂直）同量级；眼骨在眼球中心附近，
# 转 12° 眼珠大约移动眼宽的 1/4
GAZE_DEG = {"lookLeft": 12.0, "lookRight": 12.0, "lookUp": 10.0, "lookDown": 10.0}
LOOK_TRAVEL = 0.28          # 看到最边上时瞳孔走多远（瞳孔宽度的比例；VRoid 向内看约 29%）


def eye_look_range(model, vweights, parents, eyes):
    """按眼球几何算视线最大转角 → (角度, 说明)。

    角度：{"lookLeft", "lookRight", "lookUp", "lookDown"}（度）；算不出或测出来不合理时为 None
    （调用方用默认的 10°），说明给日志用。

    瞳孔看起来移动多少 = 转角 × 支点到瞳孔的距离。VRoid 的眼骨支点很深（约 6 cm），转 8° 就走
    瞳孔宽度的 29%；MMD 模型的支点常常贴着眼球（2~4 cm），固定 10° 只走 15~25%，转眼不明显。
    所以按「瞳孔走 LOOK_TRAVEL × 瞳孔宽度」反推左右的角度（8°~20°）。上下按左右的比例：
    向下 0.7（动画眼睛竖向本来就扁，再多会钻到下眼皮里），向上 0.5（上眼皮挡着；VRoid 是 0°）。
    瞳孔 = 跟着眼骨及其子骨（左目先 等）走的顶点（权重 > 0.5）。
    """
    bones, verts = model["bones"], model["vertices"]
    real = [e for e in eyes if e is not None]
    # 两眼间距：测出来的「瞳孔」比它还宽、或者支点离瞳孔比它还远，就是把别的东西也挂在眼骨上了
    # （整个眼部、脸），这时候算出来的角度不可信
    span = abs(bones[real[0]]["pos"][0] - bones[real[1]]["pos"][0]) if len(real) == 2 else None
    angles = []
    for eye in real:
        if eye is None:
            continue
        sub = {eye}
        grew = True
        while grew:
            grew = False
            for i, p in enumerate(parents):
                if p in sub and i not in sub:
                    sub.add(i)
                    grew = True
        pts = [verts[v]["pos"] for v in range(len(verts))
               if sum(w for b, w in vweights[v] if b in sub) > 0.5]
        if len(pts) < 8:
            continue
        px, _, pz = bones[eye]["pos"]
        xs = [q[0] for q in pts]
        dist = sorted(math.hypot(q[0] - px, q[2] - pz) for q in pts)
        far = dist[len(dist) // 2:]                  # 眼球前面（瞳孔表面）那一半
        r = sum(far) / len(far)
        width = max(xs) - min(xs)
        if span and (width > 0.8 * span or r > 1.5 * span):
            return None, ("眼骨上挂的顶点不像一只眼睛（宽 %.2f、支点到表面 %.2f，两眼相距 %.2f），用默认 10°"
                          % (width, r, span))
        if r > 1e-6 and width > 1e-6:
            angles.append(math.degrees(math.atan(LOOK_TRAVEL * width / r)))
    if not angles:
        return None, "眼骨上没有找到瞳孔顶点，用默认 10°"
    raw = sum(angles) / len(angles)
    h = min(20.0, max(8.0, raw))
    note = "按眼球几何，瞳孔走到约 %d%% 瞳孔宽度" % round(LOOK_TRAVEL * 100)
    if h != raw:
        note += "；算出 %.1f°，限制到 %.0f°" % (raw, h)
    return {"lookLeft": h, "lookRight": h, "lookDown": round(h * 0.7, 2), "lookUp": round(h * 0.5, 2)}, note


def gaze_offsets(left_eye, right_eye, deg=None):
    """→ [(表情名, 骨骼表情 offsets)]：两只眼骨一起转（角度 deg，默认 GAZE_DEG）。

    PMX 坐标里脸朝 -Z、角色的左边是 +X、上是 +Y：
      向左看（角色自己的左边，VRM 规范的 lookLeft）= 绕 Y 轴转负角，(0,0,-1) → +X
      向上看 = 绕 X 轴转正角，(0,0,-1) → +Y
    """
    out = []
    for name, axis, sign in (("lookLeft", (0.0, 1.0, 0.0), -1.0), ("lookRight", (0.0, 1.0, 0.0), 1.0),
                             ("lookUp", (1.0, 0.0, 0.0), 1.0), ("lookDown", (1.0, 0.0, 0.0), -1.0)):
        half = math.radians((deg or GAZE_DEG)[name]) * sign * 0.5
        q = (axis[0] * math.sin(half), axis[1] * math.sin(half), axis[2] * math.sin(half), math.cos(half))
        out.append((name, [(b, (0.0, 0.0, 0.0), q) for b in (left_eye, right_eye) if b is not None]))
    return out


# 视线表情按这个角度烘焙（和调整面板滑块的上限一样），实际角度靠权重：转眼骨 θ 时顶点的位移
# 恰好是「横向场 × sinθ + 深度场 ×（1 − cosθ）」（Rodrigues），左右一对 ±Θ 的烘焙结果正好拆出这两个场：
#   横向 = (d(+Θ) − d(−Θ)) / 2，深度 = (d(+Θ) + d(−Θ)) / 2
# 表情 = 横向 × sinθ/sinΘ + 深度 × (1−cosθ)/(1−cosΘ)，任意 θ ≤ Θ 都和真的转眼骨一样（蒙皮是线性混合，
# 部分挂在眼骨上的眼皮也一样）。调整面板改视线角度时只改这两个权重，不用重新烘焙
GAZE_MAX = {"h": 40.0, "v": 30.0}
GAZE_PAIRS = (("lookLeft", "lookRight", "h"), ("lookUp", "lookDown", "v"))
GAZE_DEPTH = {"h": "lookLeftRight_depth", "v": "lookUpDown_depth"}
GAZE_KEY = {"lookLeft": "h", "lookRight": "h", "lookUp": "up", "lookDown": "down"}   # 对应 lookAt 的哪个转角


def gaze_weights(deg, axis):
    """转 deg 度 → (横向权重, 深度权重)。超过烘焙角度时按烘焙角度。"""
    big = math.radians(GAZE_MAX[axis])
    t = math.radians(min(max(deg, 0.0), GAZE_MAX[axis]))
    return round(math.sin(t) / math.sin(big), 6), round((1 - math.cos(t)) / (1 - math.cos(big)), 6)


def split_gaze(flat, names):
    """flat（_flatten_morphs 的结果）里按 GAZE_MAX 烘焙的视线表情（成对的、从末尾找）拆成横向场 + 深度场：
    lookLeft / lookRight / lookUp / lookDown 原地换成横向场，再追加两个深度场。
    → {预设: (横向名, 深度名, 轴)}；names 是补进去的视线表情名，不成对的不拆。"""
    pos = {}
    for k in range(len(flat) - 1, -1, -1):
        if flat[k][0] in names and flat[k][0] not in pos:
            pos[flat[k][0]] = k
    out = {}
    for a, b, axis in GAZE_PAIRS:
        if a not in pos or b not in pos:
            continue
        da, db = flat[pos[a]][1], flat[pos[b]][1]
        tan, depth = {}, {}
        for v in set(da) | set(db):
            x, y = da.get(v, (0.0, 0.0, 0.0)), db.get(v, (0.0, 0.0, 0.0))
            t = tuple((x[i] - y[i]) * 0.5 for i in range(3))
            d = tuple((x[i] + y[i]) * 0.5 for i in range(3))
            if abs(t[0]) + abs(t[1]) + abs(t[2]) > 1e-9:
                tan[v] = t
            if abs(d[0]) + abs(d[1]) + abs(d[2]) > 1e-9:
                depth[v] = d
        flat[pos[a]] = (a, tan, [], {})
        flat[pos[b]] = (b, {v: (-t[0], -t[1], -t[2]) for v, t in tan.items()}, [], {})
        flat.append((GAZE_DEPTH[axis], depth, [], {}))
        out[a] = (a, GAZE_DEPTH[axis], axis)
        out[b] = (b, GAZE_DEPTH[axis], axis)
    return out


def material_vertices(model):
    """→ [每个材质用到的顶点集合]。"""
    faces = model["faces"]
    out = []
    f0 = 0
    for mat in model["materials"]:
        n = mat["faces"]
        out.append(set(faces[f0:f0 + n]))
        f0 += n
    return out


def uv_binds(model, uvs, mat_verts=None, tol=2e-3):
    """UV 表情 {顶点: (du, dv)} → ([(材质, (缩放 u, v), (偏移 u, v))], 表示不了的材质数)。

    每个被动到的材质：所有顶点（没动的偏移算 0）的新 UV 要能写成
    u' = su·u + ou、v' = sv·v + ov（最小二乘拟合后误差 < tol），否则算「表示不了」。
    """
    if not uvs:
        return [], 0
    mat_verts = mat_verts if mat_verts is not None else material_vertices(model)
    verts = model["vertices"]
    out, bad = [], 0
    for mi, vs in enumerate(mat_verts):
        if not vs or not any(v in uvs for v in vs):
            continue
        fit = []
        for k in (0, 1):
            xs = [verts[v]["uv"][k] for v in vs]
            ys = [xs[i] + uvs.get(v, (0.0, 0.0))[k] for i, v in enumerate(vs)]
            n = len(xs)
            mx, my = sum(xs) / n, sum(ys) / n
            sxx = sum((x - mx) ** 2 for x in xs)
            if sxx < 1e-12:              # 所有顶点 UV 一样：只能是纯偏移
                s = 1.0
            else:
                s = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
            o = my - s * mx
            if max(abs(s * x + o - y) for x, y in zip(xs, ys)) > tol:
                fit = None
                break
            fit.append((s, o))
        if fit is None:
            bad += 1
            continue
        (su, ou), (sv, ov) = fit
        if abs(su - 1) < 1e-6 and abs(sv - 1) < 1e-6 and abs(ou) < 1e-6 and abs(ov) < 1e-6:
            continue
        out.append((mi, (su, sv), (ou, ov)))
    return out, bad
