# -*- coding: utf-8 -*-
"""材质：PMX 材质 → glTF 材质 + MToon（1.0 的 VRMC_materials_mtoon / 0.x 的 materialProperties）。

颜色按 MMD 的显示公式算，阴影色和明暗交界按 PMX 的 toon 渐变换算，VRM 1.0 的颜色因子写线性值；
透明模式看漫反射 alpha、贴图的 alpha 分布和材质表情。
"""
import os

import vrmio


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

    def __init__(self, model, base_dir, gb, is1, flat, outline=False, force_double_sided=False):
        self.model = model
        self.base_dir = base_dir
        self.gb = gb
        self.is1 = is1
        self.outline = outline
        self.force_double_sided = force_double_sided
        self.materials = []
        self.props_0x = []
        self.img_cache = {}
        self.tex_alpha = {}              # PMX 贴图 → png_alpha_stats 结果
        self.toon_cache = {}
        self.blend_rank = 0
        self.sph_skipped = 0
        self.morph_min_alpha = {}        # PMX 材质 → 材质表情能把 alpha 压到多低
        for _, _, ops in flat:
            for mi_, mat in enumerate(model["materials"]):
                r = _morph_material(mat, ops, mi_)
                if r is not None:
                    self.morph_min_alpha[mi_] = min(self.morph_min_alpha.get(mi_, 1.0), r[0][3])

    def _find(self, idx):
        """PMX 贴图序号 → 磁盘上的文件路径（找不到为 None）。"""
        rel = self.model["textures"][idx].replace("\\", "/")
        for c in (os.path.join(self.base_dir, rel),
                  os.path.join(self.base_dir, os.path.basename(rel))):
            if os.path.isfile(c):
                return c
        return None

    def resolve_tex(self, idx):
        """PMX 贴图序号 → glTF texture 序号（-1 = 没有 / 读不了）。"""
        if idx is None or idx < 0 or idx >= len(self.model["textures"]):
            return -1
        if idx in self.img_cache:
            return self.img_cache[idx]
        rel = self.model["textures"][idx].replace("\\", "/")
        data = None
        for c in (os.path.join(self.base_dir, rel),
                  os.path.join(self.base_dir, os.path.basename(rel))):
            if os.path.isfile(c):
                data, mime = vrmio.load_texture(c)
                if data:
                    break
        if not data:
            self.img_cache[idx] = -1
            return -1
        ti = self.gb.add_texture(self.gb.add_image(data, mime))
        self.img_cache[idx] = ti
        self.tex_alpha[idx] = vrmio.png_alpha_stats(data)
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
        if mat["diffuse"][3] < 0.999:
            return "BLEND"
        st = self.tex_alpha.get(mat["tex"])
        if st and st[0] and st[1] >= st[0] * 0.001:
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
        double = True if self.force_double_sided else bool(mat["flag"] & 0x01)
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
        gmat = {
            "name": mat["name"] or ("mat%d" % mi_),
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
        log("材质透明模式：不透明 %d · 裁剪(MASK) %d · 半透明(BLEND) %d"
            % (modes.count("OPAQUE"), modes.count("MASK"), modes.count("BLEND")))
        if self.is1:
            nmc = sum(1 for m in self.materials if "matcapTexture" in
                      m["extensions"]["VRMC_materials_mtoon"])
            log("材质：MToon（VRMC_materials_mtoon）· 加算球面贴图 → matcap %d 个" % nmc)
        if self.sph_skipped:
            log("提示：%d 个材质的乘算球面贴图（.sph）在 MToon 里没有对应项，已忽略"
                % self.sph_skipped, "warn")
