# -*- coding: utf-8 -*-
"""材质：PMX 材质 → glTF 材质 + MToon（1.0 的 VRMC_materials_mtoon / 0.x 的 materialProperties）。

颜色按 MMD 的显示公式算，阴影色和明暗交界按 PMX 的 toon 渐变换算，VRM 1.0 的颜色因子写线性值；
透明模式看漫反射 alpha、贴图的 alpha 分布（只算材质的 UV 用到的部分）和材质表情。
"""
import io
import math
import os

import vrmio

ALPHA_GRID = 1024     # 统计 UV 覆盖区域的透明度时，贴图 alpha 缩到的最长边（像素）


def _alpha_grid(data):
    """贴图字节 → 缩小的 alpha 通道（Pillow 灰度图；最近邻缩小，不造出假的中间值）。
    没有 alpha、没装 Pillow 或解不开时为 None。"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if im.mode in ("RGBA", "LA", "PA"):
            a = im.getchannel("A")
        elif im.mode == "P" and "transparency" in im.info:
            a = im.convert("RGBA").getchannel("A")
        else:
            return None
        k = ALPHA_GRID / max(a.size)
        if k < 1:
            a = a.resize((max(1, round(a.width * k)), max(1, round(a.height * k))), Image.NEAREST)
        return a
    except Exception:  # noqa: BLE001  解不开就按整张图的统计
        return None


def _lin(c):
    """sRGB（gamma）分量 → 线性分量。

    MMD 在 gamma 空间里算颜色（材质色、toon 贴图都是 sRGB 值，直接相乘）；glTF 的
    baseColorFactor 和 VRM 1.0 MToon 的各个颜色因子规定是线性值，引擎会按线性来乘
    贴图。以前把 gamma 值原样写进去：阴影色 0.8 实际显示成 sRGB 0.9，只暗 10%，
    在游戏的环境光下基本看不见（原生 VRoid 皮肤的阴影色 0.x 写 (0.97, 0.81, 0.86)，
    读进来是线性 (0.93, 0.63, 0.72)）。gamma 空间的乘法 a·b 对应线性的 lin(a)·lin(b)，
    所以换算后效果和 MMD 一致。VRM 0.x 的 materialProperties 本来就是 gamma 值，不用转。
    """
    c = max(0.0, min(1.0, c))
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _lin_rgb(rgb):
    """颜色列表的 RGB 分量转线性，第 4 个（alpha）原样保留。"""
    return [round(_lin(v), 5) if i < 3 else v for i, v in enumerate(rgb)]


def _mmd_color(diffuse, ambient):
    """MMD 实际显示的材质颜色（RGBA）。

    MMD 默认着色器：颜色 = saturate(漫反射 × 光源色 + 环境色)，光源色默认
    154/255 ≈ 0.6；alpha 取漫反射 alpha。常见的「漫反射 1、环境色 0.5」算出来
    就是白色，但有的材质靠环境色调色（比如脸红：漫反射白、环境色偏红），
    只用漫反射会把颜色丢掉。
    """
    return [min(1.0, max(0.0, diffuse[k] * 0.6 + ambient[k]))
            for k in range(3)] + [diffuse[3]]


# MMD 共享 toon（toon01～toon06.bmp）的渐变：上半白、v=0.5 处硬切到下半的颜色。
# 取自 three.js MMDLoader 内嵌的 DEFAULT_TOON_TEXTURES；它的 toon07～10 是全白的
# 占位图，不可信，这几张当作「不知道」走默认阴影
_SHARED_TOON = {
    0: ((255, 255, 255), (205, 205, 205)),
    1: ((255, 255, 255), (245, 225, 225)),
    2: ((255, 255, 255), (154, 154, 154)),
    3: ((255, 255, 255), (248, 239, 235)),
    4: ((255, 255, 255), (254, 232, 223)),
    5: ((255, 237, 97), (195, 172, 3)),
}


def _toon_column(rows, w, h):
    """toon 贴图 → 从上（v=0，正对光）到下（v=1，背光 / 自阴影）的平均颜色（0～1）。"""
    xs = sorted({0, w // 4, w // 2, (3 * w) // 4, w - 1})
    return [tuple(sum(rows[y][x][k] for x in xs) / (255.0 * len(xs)) for k in range(3))
            for y in range(h)]


def _toon_shading(col):
    """MMD toon 渐变 → MToon 1.0 的 (亮部色, 阴影色, shadingShift, shadingToony)。

    MMD：颜色 ×= toon(v)，v = 0.5 − 0.5·N·L；自阴影（投影）里取最下端 toon(1)。
    MToon 1.0：shading = linearstep(−1+toony, 1−toony, N·L + shift) × 投影，
    颜色 = lerp(阴影色, 亮部色, shading)。所以
      亮部色 = toon 最上端，阴影色 = 最下端（投影里正好也是它），
      明暗交界在亮度降到一半的 v_mid 处：N·L = 1 − 2·v_mid → shift = 2·v_mid − 1，
      过渡宽度 Δv（亮度 10%→90%）对应 N·L 的 2·Δv → toony = 1 − Δv。
    星铁 / 鸣潮的皮肤 toon 在 v≈0.75 以后才变暗，换算出来 shift≈+0.6：脸上基本全亮、
    只有投影是粉色——和原生 VRoid 皮肤（0.x ShadeShift −1 / Toony 1）的效果一致。
    渐变几乎没有明暗差（纯白 toon）时阴影色 = 亮部色：MMD 里这种材质本来就没有阴影。
    """
    if not col:
        return None
    lum = [0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2] for c in col]
    top, bot = lum[0], lum[-1]
    if top - bot < 0.02:
        return col[0], col[0], 0.0, 0.9
    n = len(col)

    def first_below(level):
        for i, v in enumerate(lum):
            if v <= level:
                return i / max(1, n - 1)
        return 1.0

    v_mid = first_below(top - 0.5 * (top - bot))
    dv = first_below(top - 0.9 * (top - bot)) - first_below(top - 0.1 * (top - bot))
    shift = max(-1.0, min(1.0, 2.0 * v_mid - 1.0))
    toony = max(0.0, min(1.0, 1.0 - dv))
    return col[0], col[-1], shift, toony


def one_sided_layers(model, samples=300, inward_min=0.7, opposite_min=0.5):
    """PMX 里设计成单面、背后紧贴着一层反向表面的材质 → {材质序号: (朝里的比例, 法线相反的比例)}。

    两类：描边外壳（把脸 / 身体复制一份往外推、翻面、涂黑：单面时只在边缘露出一圈当轮廓线，叫
    「顔エッジ」「outline」之类）和内侧面（裙子、袖子里面另做一层翻过来的面显示里侧的贴图）。
    「所有材质双面」不能套到它们上：外壳会整片盖住脸和身体，内侧面会和外层重叠闪烁。

    名字靠不住，按几何判断，每个单面材质抽样最多 samples 个三角面：
      朝里   面的朝向（PMX 绕序的叉积）指向它所属骨骼那一段（主要权重的骨，骨头 → 骨尾）
      反向   离它最近的别的材质的顶点（模型高度的 3% 以内），顶点法线和这个面相反——外壳和它下面的表面、
             内侧面和外层都是这样；眼睛、嘴里朝里的面旁边是方向一致的眼白、牙齿，不算
    两项都过半（朝里 ≥ inward_min，反向 ≥ opposite_min，有效样本至少 20 个）的才算。
    """
    V, F, B = model["vertices"], model["faces"], model["bones"]
    mats = model["materials"]
    if not V or not B or not any(m["faces"] >= 3 and not (m["flag"] & 0x01) for m in mats):
        return {}
    ys = [v["pos"][1] for v in V]
    h = (max(ys) - min(ys)) or 1.0
    r, eps = 0.03 * h, 0.0003 * h
    tails = []
    for b in B:
        t = b.get("tail")
        if b.get("tail_kind") == 1 and isinstance(t, int) and 0 <= t < len(B):
            tails.append(B[t]["pos"])
        elif b.get("tail_kind") == 0 and t and not isinstance(t, int):
            tails.append(tuple(b["pos"][i] + t[i] for i in range(3)))
        else:
            tails.append(b["pos"])
    mat_of = [0] * len(V)
    k = 0
    for mi, m in enumerate(mats):
        for t in range(k, min(k + m["faces"], len(F))):
            mat_of[F[t]] = mi
        k += m["faces"]
    cell = r / 4                     # 细网格，由近到远一圈圈找：外壳离下面的表面通常只有零点几毫米
    grid = {}
    for vi, v in enumerate(V):
        p = v["pos"]
        grid.setdefault((int(p[0] // cell), int(p[1] // cell), int(p[2] // cell)), []).append(vi)

    def nearest_other(p, mi):
        cx, cy, cz = int(p[0] // cell), int(p[1] // cell), int(p[2] // cell)
        best, bd = -1, r
        for ring in range(5):        # 第 ring 圈：切比雪夫距离正好是 ring 的格子
            if best >= 0 and bd <= (ring - 1) * cell:
                break
            rng = range(-ring, ring + 1)
            for dx in rng:
                for dy in rng:
                    for dz in rng:
                        if max(abs(dx), abs(dy), abs(dz)) != ring:
                            continue
                        for vi in grid.get((cx + dx, cy + dy, cz + dz), ()):
                            if mat_of[vi] != mi:
                                q = V[vi]["pos"]
                                dd = math.sqrt((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2)
                                if eps < dd < bd:
                                    best, bd = vi, dd
        return best

    def bone_of(v):
        ws = list(v["wweights"]) or [1.0]
        bi = max(zip(ws, v["wbones"]))[1] if v["wbones"] else -1
        return bi if 0 <= bi < len(B) else -1

    out, k = {}, 0
    for mi, m in enumerate(mats):
        n = m["faces"] // 3
        start, k = k, k + m["faces"]
        if n < 1 or m["flag"] & 0x01:
            continue
        step = max(1, n // samples)
        tot, inward = 0, []          # 先只算朝里（便宜），不够的材质不用找邻居
        for t in range(start, start + n * 3, 3 * step):
            if t + 2 >= len(F):
                break
            a, b_, c = (V[F[t + j]]["pos"] for j in range(3))
            u = (b_[0] - a[0], b_[1] - a[1], b_[2] - a[2])
            w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
            fn = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
            ln = math.sqrt(fn[0] ** 2 + fn[1] ** 2 + fn[2] ** 2)
            bi = bone_of(V[F[t]])
            if ln < 1e-12 or bi < 0:
                continue
            p = ((a[0] + b_[0] + c[0]) / 3, (a[1] + b_[1] + c[1]) / 3, (a[2] + b_[2] + c[2]) / 3)
            s0, s1 = B[bi]["pos"], tails[bi]               # 骨头 → 骨尾上离 p 最近的点
            ab = (s1[0] - s0[0], s1[1] - s0[1], s1[2] - s0[2])
            ll = ab[0] ** 2 + ab[1] ** 2 + ab[2] ** 2
            f = 0.0 if ll < 1e-12 else max(0.0, min(1.0, sum((p[i] - s0[i]) * ab[i] for i in range(3)) / ll))
            d = tuple(p[i] - s0[i] - ab[i] * f for i in range(3))
            if math.sqrt(d[0] ** 2 + d[1] ** 2 + d[2] ** 2) < eps:
                continue
            tot += 1
            if fn[0] * d[0] + fn[1] * d[1] + fn[2] * d[2] < 0:
                inward.append((p, fn, ln))
        if tot < 20 or len(inward) < inward_min * tot:
            continue
        opposite = 0
        for p, fn, ln in inward:
            q = nearest_other(p, mi)
            if q >= 0:
                nq = V[q]["normal"]
                lq = math.sqrt(nq[0] ** 2 + nq[1] ** 2 + nq[2] ** 2) or 1.0
                if (fn[0] * nq[0] + fn[1] * nq[1] + fn[2] * nq[2]) / (ln * lq) < -0.5:
                    opposite += 1
            if opposite >= opposite_min * tot:
                break
        if opposite >= opposite_min * tot:
            out[mi] = (len(inward) / tot, opposite / tot)
    return out


def _morph_material(mat, ops, mi):
    """把材质表情作用到材质 mi 上，返回权重为 1 时的 (漫反射 RGBA, 环境色 RGB)。

    PMX 材质表情：运算 0 = 乘算（基础 × 值），1 = 加算（基础 + 值）；
    28 个值里 [0:4] 是漫反射 RGBA、[8:11] 是环境色。グループ里的比率按
    「基础 → 目标」线性插值（VRM 的 materialColorBinds 也是按权重插值）。
    没有作用到这个材质返回 None。
    """
    dif = list(mat["diffuse"])
    amb = list(mat["ambient"])
    hit = False
    for m, op, vals, rate in ops:
        if m != mi and m != -1:
            continue
        hit = True
        for cur, off, n in ((dif, 0, 4), (amb, 8, 3)):
            for k in range(n):
                v = vals[off + k]
                tgt = cur[k] * v if op == 0 else cur[k] + v
                cur[k] = min(1.0, max(0.0, cur[k] + (tgt - cur[k]) * rate))
    return (dif, amb) if hit else None


class MaterialBuilder:
    """逐个 PMX 材质生成 glTF 材质，贴图写进 gb（vrmio.GLBBuilder）。

    flat：_flatten_morphs 的结果——会把材质隐藏掉的材质表情决定该材质用 MASK。
    结果在 self.materials（glTF materials）和 self.props_0x（0.x 的 materialProperties）。
    """

    def __init__(self, model, base_dir, gb, is1, flat, outline=False, force_double_sided=False,
                 tex_opt=None, known_alpha=None):
        self.model = model
        self.tex_opt = tex_opt           # textures.Optimizer：嵌入前缩小 / 转 JPEG（None = 原样）
        # {贴图文件的 normcase 绝对路径: png_alpha_stats 结果}：调用方已经统计过的（FBX 转换时解过）
        self.known_alpha = known_alpha or {}
        self._pre = {}                   # prefetch 提前读好的贴图：PMX 贴图序号 → _load 的结果
        self.base_dir = base_dir
        self.finder = vrmio.TextureFinder(base_dir)
        self.tex_moved, self.tex_missing, self.tex_bad = set(), set(), set()   # 贴图路径：日志里汇报
        self.gb = gb
        self.is1 = is1
        self.outline = outline
        self.force_double_sided = force_double_sided
        # 「所有材质双面」时仍保持单面的：描边外壳、内侧面（one_sided_layers）
        self.keep_one_sided = one_sided_layers(model) if force_double_sided else {}
        self.materials = []
        self.props_0x = []
        self.img_cache = {}
        self.tex_alpha = {}              # PMX 贴图 → png_alpha_stats 结果
        self.tex_grid = {}               # PMX 贴图 → _alpha_grid（只有整张图里有透明像素的贴图）
        self.alpha_by_uv = 0             # 按 UV 覆盖区域统计后不再算透明的材质数
        self.toon_cache = {}
        self.blend_rank = 0
        self.sph_skipped = 0
        self.morph_min_alpha = {}        # PMX 材质 → 材质表情能把 alpha 压到多低
        self.morph_max_alpha = {}        # PMX 材质 → 材质表情能把 alpha 抬到多高
        self.face_start = []             # PMX 材质 → 它的第一个面在 model["faces"] 里的位置
        k = 0
        for mat in model["materials"]:
            self.face_start.append(k)
            k += mat["faces"]
        for f in flat:
            ops = f[2]
            for mi_, mat in enumerate(model["materials"]):
                r = _morph_material(mat, ops, mi_)
                if r is not None:
                    self.morph_min_alpha[mi_] = min(self.morph_min_alpha.get(mi_, 1.0), r[0][3])
                    self.morph_max_alpha[mi_] = max(self.morph_max_alpha.get(mi_, 0.0), r[0][3])

    def _find(self, idx):
        """PMX 贴图序号 → 磁盘上的文件路径（找不到为 None；查找规则见 vrmio.TextureFinder）。"""
        return self.finder.find(self.model["textures"][idx])[0]

    def _load(self, idx):
        """PMX 贴图序号 → (原图数据, mime, 透明统计, 瘦身后的 (数据, mime), 缩小的 alpha)，读不了为 None。
        不改动 self 的状态，可以在线程里跑（见 prefetch）。"""
        c = self._find(idx)
        data, mime = vrmio.load_texture(c) if c else (None, None)
        if not data:
            return None
        # 透明度统计用原图（瘦身后可能已是 JPEG）；只有完全不透明的贴图才会转 JPEG，结论不变
        key = os.path.normcase(os.path.abspath(c))
        alpha = self.known_alpha[key] if key in self.known_alpha else vrmio.png_alpha_stats(data)
        grid = _alpha_grid(data) if alpha and alpha[0] and alpha[1] >= alpha[0] * 0.001 else None
        slim = self.tex_opt.process(data, mime) if self.tex_opt is not None else (data, mime)
        return data, mime, alpha, slim, grid

    def prefetch(self, threads=1):
        """材质要用的贴图先一起读好（读文件、透明统计、瘦身）：Pillow 解码 / 编码在 C 里跑、不占 GIL，
        几张图可以同时处理。结果留给 resolve_tex，贴图写进 GLB 的顺序不变。"""
        textures = self.model["textures"]
        want = []
        for mat in self.model["materials"]:
            if mat["faces"] < 3:                     # 没有三角面的材质不会生成（见 mesh.build_primitives）
                continue
            for idx in (mat["tex"], mat["sph"] if mat["sph_mode"] == 2 else -1):
                if 0 <= idx < len(textures) and idx not in want:
                    want.append(idx)
        if threads < 2 or len(want) < 2:
            return
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(min(threads, len(want))) as ex:
            self._pre.update(zip(want, ex.map(self._load, want)))

    def resolve_tex(self, idx):
        """PMX 贴图序号 → glTF texture 序号（-1 = 没有 / 读不了）。"""
        if idx is None or idx < 0 or idx >= len(self.model["textures"]):
            return -1
        if idx in self.img_cache:
            return self.img_cache[idx]
        got = self._pre.pop(idx) if idx in self._pre else self._load(idx)
        rel = self.model["textures"][idx]
        path, how = self.finder.find(rel)
        if path is None:
            self.tex_missing.add(rel)
        elif got is None:
            self.tex_bad.add(rel)
        elif how == "moved":
            self.tex_moved.add(rel)
        if got is None:
            self.img_cache[idx] = -1
            return -1
        data, mime, self.tex_alpha[idx], slim, grid = got
        if grid is not None:
            self.tex_grid[idx] = grid
        if self.tex_opt is not None:
            data, mime = self.tex_opt.account(data, slim)
        ti = self.gb.add_texture(self.gb.add_image(data, mime))
        self.img_cache[idx] = ti
        return ti

    def toon_params(self, mat):
        """材质的 toon 渐变 → (亮部色, 阴影色, shift, toony)（见 _toon_shading）；
        没有 toon、共享 toon07～10、贴图找不到或解不了时返回 None（走默认阴影）。"""
        if mat["toon_flag"]:
            key = ("shared", mat["toon"])
        elif 0 <= mat["toon"] < len(self.model["textures"]):
            key = ("tex", mat["toon"])
        else:
            return None
        if key not in self.toon_cache:
            r = None
            if key[0] == "shared":
                st = _SHARED_TOON.get(key[1])
                if st:
                    t, b = [tuple(c / 255.0 for c in x) for x in st]
                    r = _toon_shading([t] * 16 + [b] * 16)
            else:
                path = self._find(key[1])
                if path:
                    with open(path, "rb") as f:
                        img = vrmio.decode_rgba(f.read())
                    if img:
                        r = _toon_shading(_toon_column(img[2], img[0], img[1]))
            self.toon_cache[key] = r
        return self.toon_cache[key]

    def lit_shade(self, mat, color):
        """MMD 显示色（_mmd_color）→ MToon 的 (亮部 RGBA, 阴影 RGB, shift, toony)。"""
        tp = self.toon_params(mat)
        if tp is None:
            # 没有可用的 toon：实际几乎都是眼睛、高光、眉睫、嘴、表情贴片。按二次元的
            # 惯例（原生 VRoid 的眼睛、眉毛阴影色都是白）不画阴影，不然一进投影眼睛就发黑
            return list(color), list(color[:3]), 0.0, 0.9
        lit, dark, shift, toony = tp
        return ([color[k] * lit[k] for k in range(3)] + [color[3]],
                [color[k] * dark[k] for k in range(3)], shift, toony)

    def used_alpha(self, mi_, mat):
        """材质贴图的透明统计（同 png_alpha_stats），只算这个材质的 UV 覆盖到的部分。

        有的模型几个部件共用一张大图集，或者贴图右边还拼着用不到的半透明图层（同一张图
        u 0～1/3 是颜色，后面是别的通道）：按整张图统计，不透明的身体、衣服也被当成半透明，
        BLEND 的排序问题让部件在转动视角时消失、前后遮挡错乱。三角面按 UV 画到缩小的
        alpha 图上，只统计盖住的像素。整张图没有透明像素的贴图不用算。"""
        whole = self.tex_alpha.get(mat["tex"])
        grid = self.tex_grid.get(mat["tex"])
        if grid is None or mat["faces"] < 3:
            return whole
        from PIL import Image, ImageDraw
        w, h = grid.size
        mask = Image.new("L", (w, h), 0)
        draw = ImageDraw.Draw(mask)
        verts, faces = self.model["vertices"], self.model["faces"]
        s = self.face_start[mi_]
        for t in range(s, s + mat["faces"] - 2, 3):
            uv = [verts[faces[t + k]]["uv"] for k in range(3)]
            # 平铺（repeat）：整个三角形移到最小角所在的那一格
            ou, ov = math.floor(min(p[0] for p in uv)), math.floor(min(p[1] for p in uv))
            draw.polygon([((u - ou) * w, (v - ov) * h) for u, v in uv], fill=255)
        hist = grid.histogram(mask=mask)
        st = (sum(hist), sum(hist[:250]), sum(hist[11:245]))
        # UV 全落在完全透明的地方：靠 UV 表情挪过去才显示的贴片（表情符号）、靠材质表情显示的部件，
        # 初始 UV 说明不了什么，按整张图
        return st if st[0] - st[1] + st[2] > 0 else whole

    @staticmethod
    def _see_through(st):
        return bool(st and st[0] and st[1] >= st[0] * 0.001)

    def alpha_mode(self, mi_, mat):
        """OPAQUE / MASK / BLEND。

        以前只看漫反射 alpha，贴图自带透明通道（头发高光层、瞳孔、
        镂空裙摆）的材质全被写成 OPAQUE，透明区域画成实心——
        实测头发被一层黑色盖住、眼睛一片黑。现在还看贴图的 alpha 分布：
          * 半透明像素占可见像素的比例低（只有抗锯齿边缘）→ MASK（裁剪，
            不需要排序，Unity 里最稳）
          * 比例高（柔和的叠加层、薄纱）→ BLEND
        材质表情会把它隐藏（alpha → 0）的不透明材质用 MASK，才能真的隐藏。
        """
        st = self.used_alpha(mi_, mat)
        if self._see_through(self.tex_alpha.get(mat["tex"])) and not self._see_through(st):
            self.alpha_by_uv += 1
        if mat["diffuse"][3] <= 0.001 and self.morph_max_alpha.get(mi_, 0.0) > 0.5:
            # 默认隐藏、靠材质表情显示的部件（FBX 的替换网格：另一种手势 / 张嘴的头）：
            # 用 MASK，显示出来时就是普通的不透明材质，不用 BLEND 的排序
            if self._see_through(st) and st[2] >= (st[0] - st[1] + st[2]) * 0.3:
                return "BLEND"
            return "MASK"
        if mat["diffuse"][3] < 0.999:
            return "BLEND"
        if self._see_through(st):
            n, trans, mid = st
            visible = n - trans + mid
            if visible <= 0 or mid >= visible * 0.3:
                return "BLEND"
            return "MASK"
        if self.morph_min_alpha.get(mi_, 1.0) < 0.5:
            return "MASK"
        return "OPAQUE"

    def make(self, mi_, mat):
        """生成一个 glTF 材质，返回它在 self.materials 里的序号。"""
        ti = self.resolve_tex(mat["tex"])
        # 亮部 / 阴影色和明暗交界按 PMX 的 toon 渐变换算（MMD 的暗部和自阴影都取
        # toon 最下端的颜色）。以前一律「阴影 = 亮部 × 0.8」：灰色的阴影叠在浅色皮肤上
        # 几乎看不出来，星铁、鸣潮的脸又白又平；原生 VRoid 皮肤的阴影是粉色的
        diff, shade, shift, toony = self.lit_shade(mat, _mmd_color(mat["diffuse"], mat["ambient"]))
        mode = self.alpha_mode(mi_, mat)
        double = bool(mat["flag"] & 0x01) or (self.force_double_sided and mi_ not in self.keep_one_sided)
        # 加算球面贴图（sph_mode 2）≈ MToon 的 matcap（同样是加算）；
        # 乘算（1）没有对应项，跳过
        si = -1
        if mat["sph_mode"] == 2:
            si = self.resolve_tex(mat["sph"])
        elif mat["sph_mode"] == 1 and 0 <= mat["sph"] < len(self.model["textures"]):
            self.sph_skipped += 1
        rq = 0
        if mode == "BLEND":         # 半透明按 PMX 材质顺序排绘制顺序
            rq = min(9, self.blend_rank)
            self.blend_rank += 1
        edge = self.outline and bool(mat["flag"] & 0x10) and mat["edge_size"] > 0
        # 材质名在模型内唯一：有的程序按材质名找材质，
        # PMX 允许重名，重名时只会找到第一个
        name = mat["name"] or ("mat%d" % mi_)
        used = {m["name"] for m in self.materials}
        k = 2
        while name in used:
            name = "%s_%d" % (mat["name"] or ("mat%d" % mi_), k)
            k += 1
        gmat = {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": _lin_rgb(diff),
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9,
            },
            "doubleSided": double,
            "emissiveFactor": [0.0, 0.0, 0.0],
            "alphaMode": mode,
        }
        if mode == "MASK":
            gmat["alphaCutoff"] = 0.5
        if ti >= 0:
            gmat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": ti}

        if self.is1:
            mt = {
                "specVersion": "1.0",
                "transparentWithZWrite": False,
                "renderQueueOffsetNumber": rq,
                "shadeColorFactor": _lin_rgb(shade),
                "shadingShiftFactor": round(shift, 4),
                "shadingToonyFactor": round(toony, 4),
                "giEqualizationFactor": 0.9,
                "matcapFactor": [1.0, 1.0, 1.0] if si >= 0 else [0.0, 0.0, 0.0],
                "parametricRimColorFactor": [0.0, 0.0, 0.0],
                "parametricRimFresnelPowerFactor": 5.0,
                "parametricRimLiftFactor": 0.0,
                "rimLightingMixFactor": 1.0,
                "outlineWidthMode": "worldCoordinates" if edge else "none",
                "outlineWidthFactor": (mat["edge_size"] * 0.001) if edge else 0.0,
                "outlineColorFactor": _lin_rgb(mat["edge_color"][:3]),
                "outlineLightingMixFactor": 1.0,
            }
            if ti >= 0:
                mt["shadeMultiplyTexture"] = {"index": ti}
            if si >= 0:
                mt["matcapTexture"] = {"index": si}
            gmat["extensions"] = {"VRMC_materials_mtoon": mt}
        else:
            blend = mode == "BLEND"
            mask = mode == "MASK"
            tex_props = {}
            if ti >= 0:
                tex_props["_MainTex"] = ti
                tex_props["_ShadeTexture"] = ti   # 以前没写，暗部只剩一块纯色
            if si >= 0:
                tex_props["_SphereAdd"] = si
            self.props_0x.append({
                "name": gmat["name"],
                "shader": "VRM/MToon",
                "renderQueue": (3000 + rq) if blend else (2450 if mask else 2000),
                "floatProperties": {
                    "_Cutoff": 0.5, "_BumpScale": 1.0,
                    # 0.x：smoothstep(shift, shift + 1 − toony, N·L)，换算成同样的交界和宽度
                    "_ReceiveShadowRate": 1.0,
                    "_ShadeShift": round(max(-1.0, min(1.0, -shift - (1.0 - toony))), 4),
                    "_ShadeToony": round(max(0.0, 2.0 * toony - 1.0), 4),
                    "_LightColorAttenuation": 0.0,
                    "_IndirectLightIntensity": 0.1, "_RimLightingMix": 0.0,
                    "_RimFresnelPower": 1.0, "_RimLift": 0.0,
                    # 0.x 世界坐标模式的线宽单位是厘米
                    "_OutlineWidth": (mat["edge_size"] * 0.1) if edge else 0.0,
                    "_OutlineScaledMaxDistance": 1.0,
                    "_OutlineLightingMix": 1.0, "_UvAnimScrollX": 0.0,
                    "_UvAnimScrollY": 0.0, "_UvAnimRotation": 0.0,
                    "_MToonVersion": 38.0, "_DebugMode": 0.0,
                    "_BlendMode": 2.0 if blend else (1.0 if mask else 0.0),
                    "_OutlineWidthMode": 1.0 if edge else 0.0,
                    "_OutlineColorMode": 0.0,
                    # Unity CullMode：0 Off / 1 Front / 2 Back。以前单面写成 1，
                    # 剔除的是正面
                    "_CullMode": 0.0 if double else 2.0,
                    "_OutlineCullMode": 1.0,
                    # 半透明：SrcAlpha / OneMinusSrcAlpha、不写深度。
                    # 以前透明材质也写 1/0/ZWrite=1，等于不透明
                    "_SrcBlend": 5.0 if blend else 1.0,
                    "_DstBlend": 10.0 if blend else 0.0,
                    "_ZWrite": 0.0 if blend else 1.0,
                    "_AlphaToMask": 0.0,
                },
                "vectorProperties": {
                    "_Color": [diff[0], diff[1], diff[2], diff[3]],
                    "_ShadeColor": shade + [1.0],
                    "_EmissionColor": [0.0, 0.0, 0.0, 1.0],
                    "_OutlineColor": list(mat["edge_color"][:3]) + [1.0],
                    "_RimColor": [0.0, 0.0, 0.0, 1.0],
                },
                "textureProperties": tex_props,
                "keywordMap": {"_NORMALMAP": False, "_ALPHATEST_ON": mask,
                               "_ALPHABLEND_ON": blend,
                               "_ALPHAPREMULTIPLY_ON": False,
                               "MTOON_OUTLINE_WIDTH_WORLD": edge,
                               "MTOON_OUTLINE_COLOR_FIXED": edge},
                "tagMap": {"RenderType": ("Transparent" if blend else
                                          "TransparentCutout" if mask
                                          else "Opaque")},
            })
        self.materials.append(gmat)
        return len(self.materials) - 1

    def log_summary(self, log):
        modes = [m["alphaMode"] for m in self.materials]
        log("材质透明模式：不透明 %d · 裁剪（MASK）%d · 半透明（BLEND）%d"
            % (modes.count("OPAQUE"), modes.count("MASK"), modes.count("BLEND")))
        if self.alpha_by_uv:
            log("材质：%d 个材质的贴图有透明部分，但模型只用到不透明的区域，按不透明处理" % self.alpha_by_uv)
        if self.is1:
            nmc = sum(1 for m in self.materials if "matcapTexture" in
                      m["extensions"]["VRMC_materials_mtoon"])
        else:
            nmc = sum(1 for p in self.props_0x if "_SphereAdd" in p["textureProperties"])
        if nmc:
            log("材质：%d 个加算球面贴图转为 MToon 的 matcap" % nmc)
        if self.tex_opt is not None:
            if not self.tex_opt.active and (self.tex_opt.max_size or self.tex_opt.jpeg):
                log("贴图：未安装 Pillow，缩小贴图和 JPEG 存储未生效", "warn")
            elif self.tex_opt.summary():
                log(self.tex_opt.summary())
        if self.sph_skipped:
            log("材质：%d 个材质的乘算球面贴图（.sph）在 MToon 中没有对应项，已忽略"
                % self.sph_skipped, "warn")
        if self.keep_one_sided:
            names = [self.model["materials"][i]["name"] for i in sorted(self.keep_one_sided)]
            log("材质：%d 个材质是描边外壳或内侧面（%s），「所有材质双面」时仍保持单面，否则会盖住模型或重叠闪烁"
                % (len(names), "、".join(names[:6]) + ("…" if len(names) > 6 else "")))

        def names(rels):
            rels = sorted(rels)
            return "、".join(r.replace("\\", "/").rsplit("/", 1)[-1] for r in rels[:6]) + ("…" if len(rels) > 6 else "")
        if self.tex_moved:
            log("贴图：%d 张不在模型记录的位置，已按文件名在模型文件夹里找到（%s）" % (len(self.tex_moved), names(self.tex_moved)))
        if self.tex_missing:
            log("贴图：%d 张找不到（%s），用到它们的材质只有底色。贴图要和模型放在一起：请加入整个文件夹或压缩包"
                % (len(self.tex_missing), names(self.tex_missing)), "warn")
        if self.tex_bad:
            log("贴图：%d 张读取失败（%s），用到它们的材质只有底色" % (len(self.tex_bad), names(self.tex_bad)), "warn")
