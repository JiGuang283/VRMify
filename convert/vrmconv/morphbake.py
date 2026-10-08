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

    def __init__(self, model):
        self.bones = model["bones"]
        self.verts = model["vertices"]
        self._weights = None
        self._users = None

    def _vertex_weights(self):
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


def gaze_offsets(left_eye, right_eye):
    """→ [(表情名, 骨骼表情 offsets)]：两只眼骨一起转。

    PMX 坐标里脸朝 -Z、角色的左边是 +X、上是 +Y：
      向左看（角色自己的左边，VRM 规范的 lookLeft）= 绕 Y 轴转负角，(0,0,-1) → +X
      向上看 = 绕 X 轴转正角，(0,0,-1) → +Y
    """
    out = []
    for name, axis, sign in (("lookLeft", (0.0, 1.0, 0.0), -1.0), ("lookRight", (0.0, 1.0, 0.0), 1.0),
                             ("lookUp", (1.0, 0.0, 0.0), 1.0), ("lookDown", (1.0, 0.0, 0.0), -1.0)):
        half = math.radians(GAZE_DEG[name]) * sign * 0.5
        q = (axis[0] * math.sin(half), axis[1] * math.sin(half), axis[2] * math.sin(half), math.cos(half))
        out.append((name, [(b, (0.0, 0.0, 0.0), q) for b in (left_eye, right_eye) if b is not None]))
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
