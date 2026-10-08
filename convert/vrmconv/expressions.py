# -*- coding: utf-8 -*-
"""表情：PMX 顶点 / 材质表情（含グループ展开）→ morph target + VRM 表情（1.0 expressions / 0.x blendShapeGroups）。"""
import re

import vrmio

from . import morphbake
from .materials import _lin_rgb, _mmd_color, _morph_material
from .mathutil import _m3vec

# MMD 标准表情名 → VRM 预设
# 单眼眨眼（ウィンク…）不在表里：名字靠不住，按顶点在哪一边决定（wink_presets）
EXPR_1_0 = {
    "まばたき": "blink", "瞬き": "blink",
    "びっくり": "surprised", "怒り": "angry", "怒る": "angry",
    "悲しみ": "sad", "困る": "sad",
    "にこり": "happy", "笑い": "happy", "にやり": "happy",
    "あ": "aa", "あ２": "aa", "い": "ih", "う": "ou", "え": "ee", "お": "oh",
}
# 「真面目」是严肃的眉毛，不是 relaxed（放松、愉快）；relaxed 交给下面的组合。
# 注意：MMD 标准表情里的「上」「下」是**眉毛**表情（眉パネル），不是视线，
# 所以不映射到 lookUp / lookDown（以前映射了，VRM 里一看上方眉毛就跳起来）。
# MMD 没有标准的视线表情，视线交给 lookAt（type=bone）。
# VRM 0.x 的 presetName 规范里是小写枚举（three-vrm 也只认小写，"Joy" 会被当成自定义表情）
EXPR_0X = {
    "まばたき": "blink", "瞬き": "blink",
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
          "relaxed": "fun", "blinkLeft": "blink_l", "blinkRight": "blink_r", "lookUp": "lookup",
          "lookDown": "lookdown", "lookLeft": "lookleft", "lookRight": "lookright"}


# 情绪预设组合眉 / 眼 / 嘴（像 VRoid 的 Fcl_ALL_*）。MMD 的情绪表情是按面板拆开的，
# 一个预设只对应一个表情时，sad / angry 只动眉毛、happy / surprised 只动眼睛，游戏里很弱。
# 每个部位：(PMX 面板：1 眉 / 2 目 / 3 口, [(候选表情名, 权重)]，取第一个模型里有的)。
# 闭眼类的眼睛表情（なごみ、笑い）不用半权重：很多模型是把瞳孔压扁来闭眼，一半时瞳孔缩成小点。
# 眼睛是闭着笑的预设（BLINK_BLEND）设 overrideBlink: blend，不然再眨眼会闭两遍；
# 别的预设不设——游戏播难过、生气时照样眨眼，blend 会把眨眼压没。
COMBO = {
    "happy": [(1, [("にこり", 1.0), ("にこり2", 1.0), ("喜び", 1.0), ("上", 0.3)]),
              (2, [("笑い", 1.0), ("笑い2", 1.0), ("喜び", 1.0), ("なごみ", 1.0)]),
              (3, [("にやり", 1.0), ("口角上げ", 1.0), ("にやり２", 1.0)])],
    "angry": [(1, [("怒り", 1.0)]),
              (2, [("怒り目", 0.7), ("ｷﾘｯ", 0.7), ("じと目", 0.5), ("ジト目", 0.5)]),
              (3, [("∧", 0.8), ("口角下げ", 0.8), ("ムッ", 0.8), ("怒った", 0.6)])],
    # 难过不用「じと目」「∧」：半眯眼 + 撇嘴就是生气的样子（游戏里 sad 和 angry 分不出来）。
    # 星铁系模型没有难过的眼形，只用眉毛 +「心配する」（担心的波浪嘴）
    "sad": [(1, [("困る", 1.0), ("悲しい", 1.0), ("悲しむ", 1.0)]),
            (2, [("悲しむ", 0.6), ("垂れ目", 0.6)]),
            (3, [("心配する", 1.0), ("口角下げ", 0.5)])],
    "relaxed": [(1, [("にこり", 0.5), ("喜び", 0.5), ("上", 0.2)]),
                (2, [("慈愛", 1.0), ("笑い目", 1.0), ("喜び", 0.7), ("なごみ", 1.0)]),
                (3, [("にやり", 0.6), ("口角上げ", 0.6)])],
    "surprised": [(1, [("上", 1.0)]),
                  (2, [("びっくり", 1.0), ("見開く", 1.0), ("驚かす", 1.0)]),
                  (3, [("お", 0.4), ("ワ", 0.4), ("あ", 0.4)])],
}
# VRM 0.x 没有 surprised 预设：和 VRoid 的 0.x 一样写成 unknown、名字叫 Surprised
BLINK_BLEND = {"happy", "relaxed"}
_COMBO_0X = {"happy": "joy", "angry": "angry", "sad": "sorrow", "relaxed": "fun", "surprised": None}
PRESETS_1 = {"happy", "angry", "sad", "relaxed", "surprised", "neutral", "aa", "ih", "ou", "ee", "oh",
             "blink", "blinkLeft", "blinkRight", "lookUp", "lookDown", "lookLeft", "lookRight"}
PRESETS_0 = {"neutral", "a", "i", "u", "e", "o", "blink", "joy", "angry", "sorrow", "fun",
             "lookup", "lookdown", "lookleft", "lookright", "blink_l", "blink_r"}


def _covers(big, small):
    """big 的偏移已经包含了 small（八成以上顶点相同、幅度一半以上）：两个一起用就动两遍。
    比如有的模型「ｷﾘｯ」「垂れ目」里自带了「怒り」「困る」的眉毛。"""
    shared = [v for v in small if v in big]
    if not small or len(shared) < 0.8 * len(small):
        return False
    a = sum(abs(big[v][0]) + abs(big[v][1]) + abs(big[v][2]) for v in shared)
    b = sum(abs(small[v][0]) + abs(small[v][1]) + abs(small[v][2]) for v in shared)
    return b > 0 and a >= 0.5 * b


def combo_recipes(deltas, panel):
    """→ {预设（1.0 名）: [(表情名, 权重, 部位 1 眉 / 2 目 / 3 口)]}。

    deltas：表情名 → 顶点偏移（只算有顶点偏移的表情）；panel：表情名 → PMX 面板。
    候选表情放在别的脸部面板（眉 / 目 / 口）时不算（比如眼睛面板的「喜び」不当眉毛）；
    「其他」面板不限制（有的模型眉毛放在那里）。和已选的部位动同一批顶点（_covers）的候选跳过、
    换下一个。至少两个部位找到才组合。
    """
    def clash(a, b):
        return _covers(deltas[a], deltas[b]) or _covers(deltas[b], deltas[a])

    out = {}
    for p, parts in COMBO.items():
        got = []
        for pn, cands in parts:
            for n, w in cands:
                if n in deltas and panel.get(n) in (pn, 0, 4, None) \
                        and all(n != g[0] and not clash(n, g[0]) for g in got):
                    got.append((n, w, pn))
                    break
        if len(got) >= 2:
            out[p] = got
    return out


_WINK = re.compile(r"ウィンク|ｳｨﾝｸ|wink", re.I)


def _wink_sides(flat, vertices):
    """→ {("L" / "R", 名字里有「２」): 表情名}，同一格取最前面的。

    MMD 惯例「ウィンク」闭左眼、「ウィンク右」闭右眼、「ウィンク２」是同一只眼的笑眼版，
    但不少模型不守（鸣潮的「ウィンク２」闭右眼，有的「ウィンク」闭右眼），所以看顶点：
    PMX 里角色的左边是 +X，动的顶点九成以上在一边才算。
    """
    out = {}
    for n, d, _, _ in flat:
        if not d or not _WINK.search(n):
            continue
        left = sum(1 for v in d if vertices[v]["pos"][0] > 0) / len(d)
        side = "L" if left >= 0.9 else "R" if left <= 0.1 else None
        if side:
            out.setdefault((side, bool(re.search(r"[2２]", n))), n)
    return out


def wink_presets(flat, vertices, is1):
    """单眼眨眼 → {表情名: blinkLeft / blinkRight}（0.x：blink_l / blink_r）；名字里没有「２」的优先。"""
    sides = _wink_sides(flat, vertices)
    to = {"L": "blinkLeft" if is1 else "blink_l", "R": "blinkRight" if is1 else "blink_r"}
    out = {}
    for side in ("L", "R"):
        n = sides.get((side, False)) or sides.get((side, True))
        if n:
            out[n] = to[side]
    return out


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
    elif re.fullmatch(r"(?:.*[._])?look_?(up|down|left|right)", s):
        p = "look" + re.fullmatch(r"(?:.*[._])?look_?(up|down|left|right)", s).group(1).capitalize()
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
        if p not in PRESETS_0:          # surprised 在 0.x 里不是预设
            p = None
    return p


def _flatten_morphs(morphs, model=None, extra_bone=None):
    """把顶点 / 骨骼 / UV / 材质表情（含グループ展开）整理成
    [(名字, {顶点: (dx,dy,dz)}, [(材质, 运算, 28 个值, 比率)], {顶点: (du,dv)})]。

    材质表情（kind 8）在 MMD 里常用来显示/隐藏部件（脸红、换道具），
    VRM 对应 expression 的 materialColorBinds。材质号 -1 表示所有材质。
    骨骼表情（kind 2）给了 model 时烘焙成顶点偏移（morphbake.BoneBaker）；
    UV 表情（kind 3）留给 build_expressions 换成贴图偏移。
    extra_bone：[(名字, 骨骼表情 offsets)]，追加的合成表情（视线），同样烘焙。
    """
    baker = morphbake.BoneBaker(model) if model is not None else None
    cache = {}

    def resolve(i, seen):
        if i in seen:
            return {}, [], {}
        if i in cache:
            return cache[i]
        seen = seen | {i}
        mo = morphs[i]
        k = mo["kind"]
        if k == 1:
            d = {}
            for vi, off in mo["offsets"]:
                if abs(off[0]) + abs(off[1]) + abs(off[2]) > 1e-9:
                    d[vi] = (off[0], off[1], off[2])
            res = d, [], {}
        elif k == 2:
            res = (baker.deltas(mo["offsets"]) if baker else {}), [], {}
        elif k == 3:
            res = {}, [], {vi: (off[0], off[1]) for vi, off in mo["offsets"]
                           if abs(off[0]) + abs(off[1]) > 1e-9}
        elif k == 8:
            res = {}, [(mi, op, vals, 1.0) for mi, op, vals in mo["offsets"]], {}
        elif k == 0:
            out, mats, uvs = {}, [], {}
            for sub, rate in mo["offsets"]:
                if not (0 <= sub < len(morphs)) or abs(rate) < 1e-9:
                    continue
                sd, sm, su = resolve(sub, seen)
                for vi, d in sd.items():
                    ox, oy, oz = out.get(vi, (0.0, 0.0, 0.0))
                    out[vi] = (ox + d[0] * rate, oy + d[1] * rate,
                               oz + d[2] * rate)
                for vi, d in su.items():
                    ox, oy = uvs.get(vi, (0.0, 0.0))
                    uvs[vi] = (ox + d[0] * rate, oy + d[1] * rate)
                mats.extend((mi, op, vals, r * rate) for mi, op, vals, r in sm)
            res = out, mats, uvs
        else:
            res = {}, [], {}
        cache[i] = res
        return res

    out = []
    for i, mo in enumerate(morphs):
        if mo["kind"] not in (0, 1, 2, 3, 8):
            continue
        d, m, u = resolve(i, frozenset())
        if d or m or u:
            out.append((mo["name"], d, m, u))
    for name, offsets in extra_bone or ():
        d = baker.deltas(offsets) if baker else {}
        if d:
            out.append((name, d, [], {}))
    return out


def uniq_name(name, used):
    """name 已在 used 里时加 _2、_3 … 直到不重名。"""
    if name not in used:
        return name
    k = 2
    while "%s_%d" % (name, k) in used:
        k += 1
    return "%s_%d" % (name, k)


def build_expressions(flat, prims, mats, mat_gidx, gb, xf, vmat, is1, sparse_morphs, log, combo=True):
    """表情 → 各 primitive 的 morph target + VRM 表情。

    flat：_flatten_morphs 的结果；prims：mesh.build_primitives 的结果（会往 pr["targets"] 里追加）；
    UV 表情能表示成整个材质的缩放 + 偏移时写 textureTransformBinds（0.x：_MainTex_ST）；
    mats：materials.MaterialBuilder；vmat：T-pose 后每个顶点的混合旋转（表情偏移要跟着转）。
    combo：情绪预设组合眉 / 眼 / 嘴（COMBO）；原来单个对应的表情回到自定义表情。
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
    winks = wink_presets(flat, mats.model["vertices"], is1)
    jp_presets = {table[n] for n, *_ in flat if n in table} | set(winks.values())   # 日文名占用的预设，英文名不抢
    used_0x = set()
    mat_verts = None
    n_uv_expr = n_uv_bad = 0
    slot_of = {}                     # PMX 表情名 → morph target 序号（组合预设用）
    delta_of = {}                    # PMX 表情名 → 顶点偏移（组合时查重叠）
    for mname, deltas, _, _ in flat:
        if deltas:
            delta_of.setdefault(mname, deltas)
    panel = {mo["name"]: mo.get("panel") for mo in mats.model["morphs"]}
    recipes = combo_recipes(delta_of, panel) if combo else {}

    def add_target(name, deltas):
        """一个 morph target（每个 primitive 一份）→ 序号。"""
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
                # 每个 primitive 都要「顶点数 × 12 字节」。稀疏至少要一项：空的写一个零偏移
                items.sort()
                items = items or [(0, (0.0, 0.0, 0.0))]
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
        target_names.append(uniq_name(name, target_set))
        target_set.add(target_names[-1])
        return len(target_names) - 1

    for mname, deltas, ops, uvs in flat:
        slot = None
        if deltas:
            slot = add_target(mname, deltas)
            slot_of.setdefault(mname, slot)

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
        # UV 表情 → 贴图缩放 + 偏移（只用于出现在网格里的材质）
        tex = []
        if uvs:
            if mat_verts is None:
                mat_verts = morphbake.material_vertices(mats.model)
            binds, bad = morphbake.uv_binds(mats.model, uvs, mat_verts)
            tex = [(mi_, sc, off) for mi_, sc, off in binds if mi_ in mat_gidx]
            n_uv_bad += bad
            if tex:
                n_uv_expr += 1
        if slot is None and not colors and not tex:
            continue
        if colors:
            n_mat_expr += 1

        preset = winks.get(mname) or table.get(mname)
        if not preset and not _WINK.search(mname):
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
                   "textureTransformBinds": [
                       {"material": mat_gidx[mi_], "scale": [sc[0], sc[1]], "offset": [off[0], off[1]]}
                       for mi_, sc, off in tex],
                   "isBinary": False}
            # 规范要求这些数组非空（minItems 1）：没有就不写
            for k_ in ("morphTargetBinds", "materialColorBinds", "textureTransformBinds"):
                if not obj[k_]:
                    del obj[k_]
            if preset and preset not in expr_preset:
                # 记下来源的 PMX 表情名：界面里重新指定预设表情（vrm_edit）时要用
                obj["extras"] = {"source": mname}
                expr_preset[preset] = obj
            else:
                # 自定义表情名必须唯一（PMX 里同名表情会互相覆盖）
                expr_custom[uniq_name(mname, set(expr_custom) | PRESETS_1)] = obj
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
                         if rgb else []))] + [
                    # Unity 的 UV 原点在左下：v' = sv·v + ov 换成 ST 是 (sv, 1 - sv - ov)
                    {"materialName": mats.materials[mat_gidx[mi_]]["name"],
                     "propertyName": "_MainTex_ST",
                     "targetValue": [s_[0], s_[1], o_[0], 1.0 - s_[1] - o_[1]]}
                    for mi_, s_, o_ in tex],
                "isBinary": False})
    if target_names:
        log("表情：导出 %d 个 morph target（%s存储）"
            % (len(target_names), "稀疏" if sparse_morphs else "密集"))
    if n_mat_expr:
        log("表情：%d 个含材质变化（材质表情 → materialColorBinds）" % n_mat_expr)
    if n_uv_expr:
        log("表情：%d 个 UV 表情 → 贴图偏移（textureTransformBinds）" % n_uv_expr)
    if n_uv_bad:
        log("表情：%d 处 UV 表情不是整个材质一起平移 / 缩放，VRM 表示不了，已跳过" % n_uv_bad, "warn")

    for p, parts in recipes.items():
        label = "+".join(n for n, _, _ in parts)
        binds = [(slot_of[n], w) for n, w, _ in parts]
        if is1:
            old = expr_preset.pop(p, None)
            if old is not None:      # 原来单个对应的表情回到自定义表情，界面里还能选
                src = (old.pop("extras", None) or {}).get("source") or p
                expr_custom[uniq_name(src, set(expr_custom) | PRESETS_1)] = old
            obj = {"morphTargetBinds": [{"node": 0, "index": s, "weight": w} for s, w in binds],
                   "isBinary": False, "extras": {"source": label}}
            # 笑眼（已经闭着）时再眨眼会闭两遍（眼皮穿到脸颊里）。blend：情绪表情越强，眨眼越弱
            if p in BLINK_BLEND and any(pn == 2 for _, _, pn in parts):
                obj["overrideBlink"] = "blend"
            expr_preset[p] = obj
        else:
            p0 = _COMBO_0X[p]
            if p0:
                for gr in blend_groups:
                    if gr["presetName"] == p0:
                        gr["presetName"] = "unknown"
            name = label if p0 else p.capitalize()
            blend_groups.append({"name": uniq_name(name, {g["name"] for g in blend_groups}),
                                 "presetName": p0 or "unknown",
                                 "binds": [{"mesh": 0, "index": s, "weight": round(w * 100, 2)} for s, w in binds],
                                 "materialValues": [], "isBinary": False})
    if recipes:
        log("表情：情绪预设组合眉 / 眼 / 嘴（像 VRoid）：%s" % "，".join(
            "%s = %s" % (p, " + ".join(n if w == 1 else "%s×%g" % (n, w) for n, w, _ in parts))
            for p, parts in recipes.items()))
    return {"target_names": target_names, "preset": expr_preset, "custom": expr_custom,
            "blend_groups": blend_groups}
