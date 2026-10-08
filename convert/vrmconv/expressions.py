# -*- coding: utf-8 -*-
"""表情：PMX 顶点 / 材质表情（含グループ展开）→ morph target + VRM 表情（1.0 expressions / 0.x blendShapeGroups）。"""
import re

import vrmio

from .materials import _lin_rgb, _mmd_color, _morph_material
from .mathutil import _m3vec

# MMD 标准表情名 → VRM 预设
EXPR_1_0 = {
    "まばたき": "blink", "瞬き": "blink", "ウィンク": "blinkRight",
    "ウィンク２": "blinkLeft", "ウィンク右": "blinkRight",
    "ウィンク左": "blinkLeft", "ｳｨﾝｸ": "blinkRight", "ｳｨﾝｸ２": "blinkLeft",
    "びっくり": "surprised", "怒り": "angry", "怒る": "angry",
    "悲しみ": "sad", "困る": "sad", "真面目": "relaxed",
    "にこり": "happy", "笑い": "happy", "にやり": "happy",
    "あ": "aa", "あ２": "aa", "い": "ih", "う": "ou", "え": "ee", "お": "oh",
}
# 注意：MMD 标准表情里的「上」「下」是**眉毛**表情（眉パネル），不是视线，
# 所以不映射到 lookUp / lookDown（以前映射了，VRM 里一看上方眉毛就跳起来）。
# MMD 没有标准的视线表情，视线交给 lookAt（type=bone）。
# VRM 0.x 的 presetName 规范里是小写枚举（three-vrm 也只认小写，"Joy" 会被当成自定义表情）
EXPR_0X = {
    "まばたき": "blink", "瞬き": "blink", "ウィンク": "blink_r",
    "ウィンク２": "blink_l", "ウィンク右": "blink_r", "ウィンク左": "blink_l",
    "怒り": "angry", "悲しみ": "sorrow", "困る": "sorrow",
    "にこり": "joy", "笑い": "joy",
    "あ": "a", "あ２": "a", "い": "i", "う": "u", "え": "e", "お": "o",
}


# 英文表情名（FBX 等转来的模型）→ VRM 1.0 预设。只在日文名没占用这个预设时才用。
#   VRChat：vrc.v_aa / vrc.blink_left    VRoid：Fcl_MTH_A / Fcl_EYE_Close_L / Fcl_ALL_Joy
#   Unity 酱：MTH_A / EYE_DEF_C（闭眼）    通用：blink、mouth_a、happy …
_EN_VOWEL = {"a": "aa", "aa": "aa", "i": "ih", "ih": "ih", "u": "ou", "ou": "ou",
             "e": "ee", "ee": "ee", "o": "oh", "oh": "oh"}
_EN_EMO = {"joy": "happy", "happy": "happy", "smile": "happy", "angry": "angry", "anger": "angry",
           "sorrow": "sad", "sad": "sad", "fun": "relaxed", "relaxed": "relaxed",
           "surprised": "surprised", "surprise": "surprised", "neutral": "neutral"}
_TO_0X = {"happy": "joy", "aa": "a", "ih": "i", "ou": "u", "ee": "e", "oh": "o", "sad": "sorrow",
          "relaxed": "fun", "blinkLeft": "blink_l", "blinkRight": "blink_r"}


def _preset_en(name, is1):
    s = (name or "").strip().lower().replace("-", "_").replace(" ", "_")
    s = re.sub(r"^blendshape\d*\.", "", s)
    p = None
    m = re.fullmatch(r"(?:.*[._])?(?:mth|mouth|v)[._]?(aa|ih|ou|ee|oh|a|i|u|e|o)", s) \
        or re.fullmatch(r"(aa|ih|ou|ee|oh|a|i|u|e|o)", s)
    if m:
        p = _EN_VOWEL[m.group(1)]
    elif re.search(r"mouth_?open$", s):
        p = "aa"
    elif re.search(r"(blink|eye_?close[d]?|eye_def_c)[._]?(l|left)$", s):
        p = "blinkLeft"
    elif re.search(r"(blink|eye_?close[d]?|eye_def_c)[._]?(r|right)$", s):
        p = "blinkRight"
    elif re.search(r"(?:^|[._])(blink|eye_?close[d]?|eye_def_c)$", s):
        p = "blink"
    else:
        m = re.search(r"(?:^|[._])(?:all_|face_)?([a-z]+)$", s)
        if m and m.group(1) in _EN_EMO and (s == m.group(1) or re.search(r"(?:^|_)(?:fcl_)?all_|^face_", s)):
            p = _EN_EMO[m.group(1)]
    if p and not is1:
        p = _TO_0X.get(p, p)
    return p


def _flatten_morphs(morphs):
    """把顶点表情和材质表情（含グループ展开）整理成
    [(名字, {顶点: (dx,dy,dz)}, [(材质, 运算, 28 个值, 比率)])]。

    材质表情（kind 8）在 MMD 里常用来显示/隐藏部件（脸红、换道具），
    VRM 对应 expression 的 materialColorBinds。材质号 -1 表示所有材质。
    """
    def resolve(i, seen):
        if i in seen:
            return {}, []
        seen = seen | {i}
        mo = morphs[i]
        if mo["kind"] == 1:
            d = {}
            for vi, off in mo["offsets"]:
                if abs(off[0]) + abs(off[1]) + abs(off[2]) > 1e-9:
                    d[vi] = (off[0], off[1], off[2])
            return d, []
        if mo["kind"] == 8:
            return {}, [(mi, op, vals, 1.0) for mi, op, vals in mo["offsets"]]
        if mo["kind"] == 0:
            out, mats = {}, []
            for sub, rate in mo["offsets"]:
                if not (0 <= sub < len(morphs)) or abs(rate) < 1e-9:
                    continue
                sd, sm = resolve(sub, seen)
                for vi, d in sd.items():
                    ox, oy, oz = out.get(vi, (0.0, 0.0, 0.0))
                    out[vi] = (ox + d[0] * rate, oy + d[1] * rate,
                               oz + d[2] * rate)
                mats.extend((mi, op, vals, r * rate) for mi, op, vals, r in sm)
            return out, mats
        return {}, []

    out = []
    for i, mo in enumerate(morphs):
        if mo["kind"] not in (0, 1, 8):
            continue
        d, m = resolve(i, frozenset())
        if d or m:
            out.append((mo["name"], d, m))
    return out


def uniq_name(name, used):
    """name 已在 used 里时加 _2、_3 … 直到不重名。"""
    if name not in used:
        return name
    k = 2
    while "%s_%d" % (name, k) in used:
        k += 1
    return "%s_%d" % (name, k)


def build_expressions(flat, prims, mats, mat_gidx, gb, xf, vmat, is1, sparse_morphs, log):
    """表情 → 各 primitive 的 morph target + VRM 表情。

    flat：_flatten_morphs 的结果；prims：mesh.build_primitives 的结果（会往 pr["targets"] 里追加）；
    mats：materials.MaterialBuilder；vmat：T-pose 后每个顶点的混合旋转（表情偏移要跟着转）。
    返回 dict：target_names、preset / custom（1.0）、blend_groups（0.x）。
    morphTargetBinds 的 node 先写 0，写出前由调用方改成网格节点。
    """
    materials = mats.model["materials"]
    target_names = []
    target_set = set()
    expr_preset = {}
    expr_custom = {}
    blend_groups = []
    table = EXPR_1_0 if is1 else EXPR_0X
    n_mat_expr = 0
    jp_presets = {table[n] for n, _, _ in flat if n in table}    # 日文名占用的预设，英文名不抢
    used_0x = set()

    for mname, deltas, ops in flat:
        slot = None
        if deltas:
            slot = len(target_names)
            for pr in prims:
                loc = pr["local"]
                items = []
                for vi, d in deltas.items():
                    j = loc.get(vi)
                    if j is None:
                        continue
                    if vmat is not None and vmat[vi] is not None:
                        d = _m3vec(vmat[vi], d)
                    # 偏移量也要乘缩放（xf 是纯线性变换，正好适用于偏移）。
                    # 以前用 xf_dir 只翻了轴没缩放，偏移大了 1/scale≈12 倍，
                    # 表情一开脸就拉成长刺。
                    items.append((j, xf(d)))
                if sparse_morphs:
                    # 稀疏存储：一个表情通常只动几百个顶点，密集存储每个表情
                    # 每个 primitive 都要「顶点数 × 12 字节」
                    items.sort()
                    acc = gb.add_sparse_accessor(
                        pr["vcount"], [j for j, _ in items],
                        [c for _, d in items for c in d], "VEC3")
                else:
                    arr = [0.0] * (pr["vcount"] * 3)
                    for j, d in items:
                        arr[j * 3:j * 3 + 3] = d
                    acc = gb.add_accessor(arr, vrmio.FLOAT, "VEC3", 34962)
                pr["targets"].append({"POSITION": acc})
            # Unity 的 BlendShape 名在一个网格内必须唯一，重名时 UniVRM 导入直接抛
            # 「Blend shape name already exists」中断（开拓者 PMX 里有两个「手前」）
            target_names.append(uniq_name(mname, target_set))
            target_set.add(target_names[-1])

        # 材质表情 → 目标颜色（权重 1 时的漫反射 RGBA）
        colors = []
        for mi_, mat in enumerate(materials):
            if mi_ not in mat_gidx:
                continue
            r = _morph_material(mat, ops, mi_)
            if r is None:
                continue
            base = _mmd_color(mat["diffuse"], mat["ambient"])
            c = _mmd_color(*r)
            if max(abs(c[k] - base[k]) for k in range(4)) < 1e-6:
                continue
            lit_c, shade_c, _, _ = mats.lit_shade(mat, c)
            colors.append((mi_, lit_c, shade_c, max(abs(c[k] - base[k])
                                                    for k in range(3)) > 1e-6))
        if slot is None and not colors:
            continue
        if colors:
            n_mat_expr += 1

        preset = table.get(mname)
        if not preset:
            preset = _preset_en(mname, is1)
            if preset in jp_presets:
                preset = None
        if is1:
            obj = {"morphTargetBinds": ([{"node": 0, "index": slot,
                                          "weight": 1.0}]
                                        if slot is not None else []),
                   "materialColorBinds": [
                       b for mi_, c, sc, rgb in colors for b in (
                           [{"material": mat_gidx[mi_], "type": "color",
                             "targetValue": _lin_rgb(c)}] +
                           # targetValue 规范要求正好 4 个分量：UniVRM 直接读 [3]，
                           # 只写 RGB 会在导入时 IndexOutOfRange 中断
                           ([{"material": mat_gidx[mi_], "type": "shadeColor",
                              "targetValue": _lin_rgb(sc) + [1.0]}]
                            if rgb else []))],
                   "isBinary": False}
            # 规范要求这些数组非空（minItems 1）：没有就不写
            for k_ in ("morphTargetBinds", "materialColorBinds"):
                if not obj[k_]:
                    del obj[k_]
            if preset and preset not in expr_preset:
                # 记下来源的 PMX 表情名：界面里重新指定预设表情（vrm_edit）时要用
                obj["extras"] = {"source": mname}
                expr_preset[preset] = obj
            else:
                # 自定义表情名必须唯一（PMX 里同名表情会互相覆盖）
                expr_custom[uniq_name(mname, expr_custom)] = obj
        else:
            # 同一个预设只给第一个（「あ」「あ２」都是 a 时，第二个当自定义）
            if preset in used_0x:
                preset = None
            used_0x.add(preset)
            blend_groups.append({
                "name": mname,
                "presetName": preset or "unknown",
                "binds": ([{"mesh": 0, "index": slot, "weight": 100}]
                          if slot is not None else []),
                "materialValues": [
                    v for mi_, c, sc, rgb in colors for v in (
                        [{"materialName": mats.materials[mat_gidx[mi_]]["name"],
                          "propertyName": "_Color", "targetValue": c}] +
                        ([{"materialName":
                           mats.materials[mat_gidx[mi_]]["name"],
                           "propertyName": "_ShadeColor",
                           "targetValue": sc + [1.0]}]
                         if rgb else []))],
                "isBinary": False})
    if target_names:
        log("表情：导出 %d 个 morph target（%s存储）"
            % (len(target_names), "稀疏" if sparse_morphs else "密集"))
    if n_mat_expr:
        log("表情：%d 个含材质变化（材质表情 → materialColorBinds）" % n_mat_expr)
    return {"target_names": target_names, "preset": expr_preset, "custom": expr_custom,
            "blend_groups": blend_groups}
