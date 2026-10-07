# -*- coding: utf-8 -*-
"""3x3 矩阵小工具（行主序元组，纯 Python）。"""
import math


_I3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def _m3mul(a, b):
    return tuple(sum(a[r * 3 + k] * b[k * 3 + c] for k in range(3))
                 for r in range(3) for c in range(3))


def _m3vec(m, v):
    return (m[0] * v[0] + m[1] * v[1] + m[2] * v[2],
            m[3] * v[0] + m[4] * v[1] + m[5] * v[2],
            m[6] * v[0] + m[7] * v[1] + m[8] * v[2])


def _m3T(m):
    return (m[0], m[3], m[6], m[1], m[4], m[7], m[2], m[5], m[8])


def _axis_angle(axis, ang):
    x, y, z = axis
    c, s = math.cos(ang), math.sin(ang)
    t = 1.0 - c
    return (t * x * x + c, t * x * y - s * z, t * x * z + s * y,
            t * x * y + s * z, t * y * y + c, t * y * z - s * x,
            t * x * z - s * y, t * y * z + s * x, t * z * z + c)


def _to_axis_angle(m):
    ang = math.acos(max(-1.0, min(1.0, (m[0] + m[4] + m[8] - 1.0) * 0.5)))
    if ang < 1e-9:
        return (1.0, 0.0, 0.0), 0.0
    ax = (m[7] - m[5], m[2] - m[6], m[3] - m[1])
    ln = math.sqrt(ax[0] ** 2 + ax[1] ** 2 + ax[2] ** 2)
    if ln < 1e-9:                    # 180°：取对角线最大的那列
        k = max(range(3), key=lambda i: m[i * 4])
        col = [(m[i * 3 + k] + (1.0 if i == k else 0.0)) for i in range(3)]
        ln = math.sqrt(sum(x * x for x in col)) or 1.0
        return tuple(x / ln for x in col), ang
    return (ax[0] / ln, ax[1] / ln, ax[2] / ln), ang


def _align(d, t):
    """把方向 d 转到方向 t 的最小旋转，返回 (矩阵, 角度)。"""
    ld = math.sqrt(sum(x * x for x in d))
    lt = math.sqrt(sum(x * x for x in t))
    if ld < 1e-9 or lt < 1e-9:
        return _I3, 0.0
    d = [x / ld for x in d]
    t = [x / lt for x in t]
    ax = (d[1] * t[2] - d[2] * t[1], d[2] * t[0] - d[0] * t[2],
          d[0] * t[1] - d[1] * t[0])
    s = math.sqrt(sum(x * x for x in ax))
    ang = math.atan2(s, sum(d[k] * t[k] for k in range(3)))
    if s < 1e-9:
        return _I3, 0.0
    return _axis_angle((ax[0] / s, ax[1] / s, ax[2] / s), ang), ang
