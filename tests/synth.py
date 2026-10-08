# -*- coding: utf-8 -*-
"""合成一个很小的 MMD 人形 PMX，给测试用（仓库里不能放真实模型，见 README）。

骨架用 MMD 标准日文骨名（A 姿势的手臂、双眼），每根骨挂一个小方块网格；
头上一条三节的头发链挂物理刚体（→ SpringBone）；两张贴图：一张不透明、一张带透明；
几个标准表情（まばたき / あ / にこり / 笑い）和一个材质表情。
eyes=True 时再加：眼珠（挂在左右眼骨上）、骨骼表情「寄り目」、含骨骼表情的グループ「見開く」、
一块漫符面片（独立材质）和移动它 UV 的 UV 表情「怒マーク」。
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("formats", "convert"):
    _d = os.path.join(ROOT, _p)
    if _d not in sys.path:
        sys.path.insert(0, _d)
import pmxio  # noqa: E402
import vrmio  # noqa: E402

# 名字, 父骨, 位置（MMD 单位：脸朝 -Z，角色左手在 +X）
_BONES = [
    ("センター", None, (0, 8, 0)),
    ("下半身", "センター", (0, 11, 0)),
    ("上半身", "センター", (0, 11.5, 0)),
    ("上半身2", "上半身", (0, 13, 0)),
    ("首", "上半身2", (0, 15.5, 0)),
    ("頭", "首", (0, 16.5, 0)),
    ("左目", "頭", (0.35, 17.4, -0.8)),
    ("右目", "頭", (-0.35, 17.4, -0.8)),
]
for _s, _x in (("左", 1), ("右", -1)):
    _BONES += [
        (_s + "肩", "上半身2", (0.4 * _x, 15, 0)),
        (_s + "腕", _s + "肩", (1.4 * _x, 15, 0)),
        (_s + "ひじ", _s + "腕", (3.2 * _x, 13.7, 0)),
        (_s + "手首", _s + "ひじ", (4.9 * _x, 12.5, 0)),
        (_s + "足", "下半身", (0.9 * _x, 10.5, 0)),
        (_s + "ひざ", _s + "足", (0.9 * _x, 6, 0)),
        (_s + "足首", _s + "ひざ", (0.9 * _x, 1.2, 0)),
        (_s + "つま先", _s + "足首", (0.9 * _x, 0.2, -1.2)),
    ]
_BONES += [
    ("髪1", "頭", (0, 17.5, 0.9)),
    ("髪2", "髪1", (0, 15.6, 1.2)),
    ("髪3", "髪2", (0, 13.7, 1.4)),
    ("髪先", "髪3", (0, 11.8, 1.5)),
]


def _png(w, h, alpha):
    rows = []
    for y in range(h):
        r = bytearray()
        for x in range(w):
            a = 255 if not alpha else (0 if (x // 8 + y // 8) % 2 else 255)
            r += bytes(((x * 255) // max(1, w - 1), (y * 255) // max(1, h - 1), 128, a))
        rows.append(bytes(r))
    return vrmio.encode_png(w, h, rows)


def _box(model, center, half, bone):
    """以 center 为中心、半边长 half 的方块，权重全给 bone。返回三角形索引（从外面看顺时针，MMD 的正面）。"""
    v0 = len(model["vertices"])
    cx, cy, cz = center
    for k in range(8):
        dx = half if k & 1 else -half
        dy = half if k & 2 else -half
        dz = half if k & 4 else -half
        n = math.sqrt(3)
        model["vertices"].append({
            "pos": (cx + dx, cy + dy, cz + dz), "normal": (dx / half / n, dy / half / n, dz / half / n),
            "uv": ((dx / half + 1) / 2, (dy / half + 1) / 2), "wtype": 0, "wbones": [bone],
            "wweights": [1.0], "edge": 1.0})
    quads = [(0, 2, 3, 1), (4, 5, 7, 6), (0, 1, 5, 4), (2, 6, 7, 3), (0, 4, 6, 2), (1, 3, 7, 5)]
    tris = []
    for a, b, c, d in quads:
        tris += [v0 + a, v0 + b, v0 + c, v0 + a, v0 + c, v0 + d]
    return tris


def _material(name, tex, faces, toon=-1):
    return {"name": name, "name_en": name, "diffuse": (1.0, 1.0, 1.0, 1.0), "specular": (0, 0, 0),
            "shininess": 5.0, "ambient": (0.5, 0.5, 0.5), "flag": 0x01 | 0x02 | 0x04 | 0x10,
            "edge_color": (0, 0, 0, 1), "edge_size": 1.0, "tex": tex, "sph": -1, "sph_mode": 0,
            "toon_flag": 1 if toon >= 0 else 0, "toon": toon, "memo": "", "faces": faces}


def build(folder, name="合成モデル", tex_size=256, eyes=False):
    """在 folder 里写出 <name>.pmx 和贴图，返回 PMX 路径。"""
    os.makedirs(os.path.join(folder, "tex"), exist_ok=True)
    with open(os.path.join(folder, "tex", "body.png"), "wb") as f:
        f.write(_png(tex_size, tex_size, alpha=False))
    with open(os.path.join(folder, "tex", "hair.png"), "wb") as f:
        f.write(_png(64, 64, alpha=True))

    m = pmxio.new_model(name)
    m["textures"] = ["tex\\body.png", "tex\\hair.png"]
    index = {n: i for i, (n, _, _) in enumerate(_BONES)}
    for i, (n, parent, pos) in enumerate(_BONES):
        flag = 0x0001 | 0x0002 | 0x0008 | 0x0010
        if n == "センター":
            flag |= 0x0004
        m["bones"].append({"name": n, "name_en": n, "pos": pos, "parent": index[parent] if parent else -1,
                           "layer": 0, "flag": flag, "tail": -1})
    for n, _, _ in _BONES:          # tail：第一个子骨
        kids = [index[c] for c, p, _ in _BONES if p == n]
        if kids:
            m["bones"][index[n]]["tail"] = kids[0]

    body, hair = [], []
    for n, _, pos in _BONES:
        if n.startswith("髪"):
            hair += _box(m, pos, 0.35, index[n])
        elif not n.endswith("目"):
            body += _box(m, pos, 0.5 if n != "頭" else 1.0, index[n])
    m["faces"] = body + hair
    m["materials"] = [_material("体", 0, len(body), toon=1), _material("髪", 1, len(hair))]
    if eyes:
        eye = []
        for n in ("左目", "右目"):
            x, y, z = _BONES[index[n]][2]
            eye += _box(m, (x, y, z - 0.25), 0.12, index[n])      # 眼骨在眼珠后面
        v0 = len(m["vertices"])
        for k, (dx, dy) in enumerate(((0, 0), (1, 0), (1, 1), (0, 1))):
            m["vertices"].append({"pos": (0.6 + dx * 0.4, 18 + dy * 0.4, -1.05), "normal": (0, 0, -1),
                                  "uv": (dx * 0.25, dy * 0.25), "wtype": 0, "wbones": [index["頭"]],
                                  "wweights": [1.0], "edge": 0.0})
        emo = [v0, v0 + 1, v0 + 2, v0, v0 + 2, v0 + 3]
        m["faces"] += eye + emo
        m["materials"] += [_material("目", 0, len(eye)), _material("漫符", 1, len(emo))]

    # 表情：动头部方块（「頭」是第 6 根有网格的骨 → 第 6 个方块）
    hv = 5 * 8
    m["morphs"] = [
        {"name": "まばたき", "name_en": "blink", "panel": 1, "kind": 1,
         "offsets": [(hv + k, (0.0, -0.2, 0.0)) for k in range(4)]},
        {"name": "あ", "name_en": "a", "panel": 2, "kind": 1,
         "offsets": [(hv + k, (0.0, -0.3, 0.0)) for k in range(2)]},
        {"name": "にこり", "name_en": "smile", "panel": 3, "kind": 1,
         "offsets": [(hv + k, (0.1, 0.1, 0.0)) for k in range(4, 8)]},
        {"name": "笑い", "name_en": "laugh", "panel": 3, "kind": 1,
         "offsets": [(hv + k, (0.2, 0.0, 0.0)) for k in range(4, 8)]},
        {"name": "照れ", "name_en": "blush", "panel": 4, "kind": 8,
         "offsets": [(0, 0, (1, 0.8, 0.8, 1) + (1,) * 24)]},
    ]
    if eyes:
        s = math.sin(math.radians(5))
        c = math.cos(math.radians(5))
        m["morphs"] += [
            # 两眼往中间转 10°（PMX 四元数 x, y, z, w；左眼在 +X，绕 Y 转正角朝 -X）
            {"name": "寄り目", "name_en": "cross", "panel": 2, "kind": 2,
             "offsets": [(index["左目"], (0, 0, 0), (0, s, 0, c)), (index["右目"], (0, 0, 0), (0, -s, 0, c))]},
            {"name": "見開く", "name_en": "wide", "panel": 2, "kind": 0,
             "offsets": [(0, -0.5), (5, 1.0)]},                  # まばたき × -0.5 + 寄り目
            # 漫符面片的 UV 整体移到贴图右边一格
            {"name": "怒マーク", "name_en": "anger", "panel": 4, "kind": 3,
             "offsets": [(v0 + k, (0.5, 0.25, 0, 0)) for k in range(4)]},
        ]

    # 物理：頭（跟随骨骼）+ 头发三节（物理）
    def rb(nm, bone, pos, mode, r=0.6):
        return {"name": nm, "name_en": nm, "bone": index[bone], "group": 1 if mode else 0,
                "mask": 0xFFFF & ~(1 << 1), "shape": 0, "size": (r, 0, 0), "pos": pos, "rot": (0, 0, 0),
                "mass": 1.0, "move_damp": 0.5, "rot_damp": 0.5, "repulsion": 0.0, "friction": 0.5,
                "mode": mode}
    m["rigid_bodies"] = [rb("頭", "頭", (0, 17.2, 0), 0, 1.6), rb("髪1", "髪1", (0, 16.5, 1.0), 0),
                         rb("髪2", "髪2", (0, 14.6, 1.3), 1), rb("髪3", "髪3", (0, 12.7, 1.45), 1)]

    def joint(nm, a, b, pos):
        return {"name": nm, "name_en": nm, "type": 0, "rb_a": a, "rb_b": b, "pos": pos, "rot": (0, 0, 0),
                "move_min": (0, 0, 0), "move_max": (0, 0, 0), "rot_min": (-0.5, -0.5, -0.5),
                "rot_max": (0.5, 0.5, 0.5), "spring_move": (0, 0, 0), "spring_rot": (10, 10, 10)}
    m["joints"] = [joint("髪1-2", 1, 2, (0, 15.6, 1.2)), joint("髪2-3", 2, 3, (0, 13.7, 1.4))]

    path = os.path.join(folder, name + ".pmx")
    pmxio.write_pmx(m, path)
    return path
