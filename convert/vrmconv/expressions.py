# -*- coding: utf-8 -*-
"""表情：PMX 顶点 / 材质表情（含グループ展开）→ morph target + VRM 表情（1.0 expressions / 0.x blendShapeGroups）。"""
import re
import unicodedata

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


# 中文表情名（整张脸，单独对应一个预设；比日文名、英文名优先级低）。左右单眼的「左眨眼」等走 _WINK
EXPR_CN = {"眨眼": "blink", "闭眼": "blink", "啊": "aa", "阿": "aa", "咿": "ih", "伊": "ih", "呜": "ou", "乌": "ou",
           "诶": "ee", "欸": "ee", "哦": "oh", "喔": "oh", "开心": "happy", "高兴": "happy", "微笑": "happy",
           "生气": "angry", "愤怒": "angry", "伤心": "sad", "难过": "sad", "悲伤": "sad",
           "惊讶": "surprised", "吃惊": "surprised", "放松": "relaxed"}

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
# 候选可以是一个名字，也可以是几个名字的元组（ARKit 的左右各一个：都有才算，一起用）；
# EN 是「英文部位名」：VRoid 的 Fcl_BRW_Angry / Fcl_EYE_Joy / Fcl_MTH_Sad、Mouth_Sad 之类（_en_part）。
# 每个部位按 MMD 名 → 英文部位名 → ARKit 的顺序找，取第一个模型里有的。
EN = "@en"
_L_R = lambda a: (a + "Left", a + "Right")        # noqa: E731  ARKit 的左右一对
COMBO = {
    "happy": [(1, [("にこり", 1.0), ("にこり2", 1.0), ("喜び", 1.0), ("上", 0.3), (EN, 1.0)]),
              (2, [("笑い", 1.0), ("笑い2", 1.0), ("喜び", 1.0), ("なごみ", 1.0), (EN, 1.0),
                   (_L_R("eyeBlink"), 0.8)]),
              (3, [("にやり", 1.0), ("口角上げ", 1.0), ("にやり２", 1.0), (EN, 1.0), (_L_R("mouthSmile"), 1.0)])],
    # 生气比 MMD 原表情含蓄（和 VRoid 的 Angry 差不多）：VRM 预设通常和眨眼、口型、别的预设叠着用
    # （游戏常拿 angry 当「痛苦、用力」，再叠 ih / oh / 闭眼）。满值的「怒り」皱眉太重、「ｷﾘｯ」是瞪眼、
    # 「∧」拱起的嘴叠上张嘴就成了龇牙，所以都降权重，嘴优先用嘴角下撇
    "angry": [(1, [("怒り", 0.5), (EN, 0.5), (_L_R("browDown"), 0.5)]),
              (2, [("怒り目", 0.3), ("ｷﾘｯ", 0.3), ("じと目", 0.25), ("ジト目", 0.25), (EN, 0.3),
                   (_L_R("eyeSquint"), 0.3)]),
              (3, [("口角下げ", 0.4), ("ムッ", 0.4), ("∧", 0.3), ("怒った", 0.3), (EN, 0.4),
                   (_L_R("mouthFrown"), 0.4)])],
    # 难过不用「じと目」「∧」：半眯眼 + 撇嘴就是生气的样子（游戏里 sad 和 angry 分不出来）。
    # 星铁系模型没有难过的眼形，只用眉毛 +「心配する」（担心的波浪嘴）
    "sad": [(1, [("困る", 1.0), ("悲しい", 1.0), ("悲しむ", 1.0), (EN, 1.0), ("browInnerUp", 1.0)]),
            (2, [("悲しむ", 0.6), ("垂れ目", 0.6), (EN, 0.6)]),
            (3, [("心配する", 1.0), ("口角下げ", 0.5), (EN, 1.0), (_L_R("mouthFrown"), 0.7)])],
    "relaxed": [(1, [("にこり", 0.5), ("喜び", 0.5), ("上", 0.2), (EN, 0.5)]),
                (2, [("慈愛", 1.0), ("笑い目", 1.0), ("喜び", 0.7), ("なごみ", 1.0), (EN, 1.0),
                     (_L_R("eyeBlink"), 0.4)]),
                (3, [("にやり", 0.6), ("口角上げ", 0.6), (EN, 0.6), (_L_R("mouthSmile"), 0.6)])],
    # 惊讶只睁眼、抬眉，不张嘴：常和口型（aa / oh）、生气叠用，再带张嘴会撑得过大
    "surprised": [(1, [("上", 0.7), (EN, 0.7), (("browInnerUp",) + _L_R("browOuterUp"), 0.7)]),
                  (2, [("びっくり", 1.0), ("見開く", 1.0), ("驚かす", 1.0), (EN, 1.0), (_L_R("eyeWide"), 1.0)])],
}
# 名字对应不上单个表情时的退路（1.0 名）：ARKit 的眨眼、口型（粗略：张嘴 / 嘟嘴 / 噘圆 / 咧嘴）。
# ARKit 的笑眼用 eyeBlink 半闭（eyeSquint 只抬下眼睑，很多模型几乎看不出来）。
# 第一项必须有，后面的有就加上
FALLBACK = {"blink": [(_L_R("eyeBlink"), 1.0)],
            "aa": [(("jawOpen",), 1.0)], "ou": [(("mouthPucker",), 1.0)],
            "oh": [(("mouthFunnel",), 1.0), (("jawOpen",), 0.3)],
            # mouthStretch 单用是嘴角往下拉的苦脸：e / i 要和 mouthSmile、一点张嘴混起来
            "ee": [(_L_R("mouthStretch"), 0.4), (_L_R("mouthSmile"), 0.4), (("jawOpen",), 0.25)],
            "ih": [(_L_R("mouthStretch"), 0.3), (_L_R("mouthSmile"), 0.3), (("jawOpen",), 0.12)]}
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


def _key(name):
    """比较用的名字：全角半角统一（ｷﾘｯ = キリッ、２ = 2）、小写、去掉 blendShape. / vrc. 前缀和
    英文分隔符（_ . - 空格），结尾的 _L / _R 写成 left / right（eyeBlink_L = eyeBlinkLeft）。
    ∧ ω ▲ 这类符号表情名不动。"""
    s = unicodedata.normalize("NFKC", name or "").strip().lower()
    s = re.sub(r"^(?:blendshape\d*|vrc)\.", "", s)
    s = re.sub(r"[_.\- ]+([lr])$", lambda m: "left" if m.group(1) == "l" else "right", s)
    return re.sub(r"[_.\- ]+", "", s)


_TABLE_KEY = {True: {_key(n): p for n, p in EXPR_1_0.items()}, False: {_key(n): p for n, p in EXPR_0X.items()}}
_CN_KEY = {_key(n): p for n, p in EXPR_CN.items()}


class Lookup:
    """模型里的表情名索引：按 _key 找；找不到时认变体后缀 A / B / 1 / 2 / 3（怒りA、にこりB、笑い2），
    不认 L / R 这类单边的。"""

    def __init__(self, names):
        self.names = list(names)
        self.by_key = {}
        for n in self.names:
            self.by_key.setdefault(_key(n), n)

    def get(self, cand):
        k = _key(cand)
        if k in self.by_key:
            return self.by_key[k]
        for suf in ("a", "b", "1", "2", "3"):
            if k + suf in self.by_key:
                return self.by_key[k + suf]
        return None

    def all(self, cands):
        """几个名字都有 → 实际名字的元组；缺一个就 None。"""
        got = tuple(self.get(c) for c in cands)
        return got if all(got) and len(set(got)) == len(got) else None


_EN_PART_WORD = {"brow": 1, "brw": 1, "eyebrow": 1, "eyebrows": 1,
                 "eye": 2, "eyes": 2, "mouth": 3, "mth": 3, "lip": 3, "lips": 3}
# 每种情绪的英文词，按优先顺序（嘴：smile 是抿嘴笑，laugh 是张嘴大笑，先用 smile）
_EN_FEEL_WORDS = {"happy": ["joy", "happy", "smile", "laugh"], "angry": ["angry", "anger", "mad"],
                  "sad": ["sorrow", "sad", "trouble", "worried", "worry"],
                  "relaxed": ["fun", "relaxed", "relax", "gentle"],
                  "surprised": ["surprised", "surprise", "shock", "wide"]}
_EN_SIDE = {"l", "r", "left", "right"}


def _en_words(name):
    """Fcl_BRW_Angry → [fcl, brw, angry]；Mouth_Smile1 → [mouth, smile]；eyeSmileLeft → [eye, smile, left]。"""
    s = unicodedata.normalize("NFKC", name or "")
    s = re.sub(r"([a-z])([A-Z])", r"_", s).lower()
    return [re.sub(r"\d+$", "", w) for w in re.split(r"[^a-z0-9]+", s) if w]


def _en_part(lookup, preset, part):
    """英文部位名里找「这个部位 + 这种情绪」的表情：VRoid 的 Fcl_EYE_Joy、通用的 Mouth_Sad 等。
    单边的（带 L / R）不算；有几个时按情绪词的优先顺序、再按模型里的顺序。"""
    best = None
    for k, n in enumerate(lookup.names):
        ws = _en_words(n)
        if not ws or set(ws) & _EN_SIDE or "all" in ws:
            continue
        parts = {_EN_PART_WORD[w] for w in ws if w in _EN_PART_WORD}
        feels = [i for i, w in enumerate(_EN_FEEL_WORDS[preset]) if w in ws]
        if parts == {part} and feels:
            rank = (min(feels), k)
            if best is None or rank < best[0]:
                best = (rank, n)
    return best[1] if best else None


def _covers_any(deltas, a, b):
    """两组表情（各是名字元组）动的是不是同一批顶点（任一方向 _covers）。"""
    def merged(names):
        out = {}
        for n in names:
            for v, d in deltas[n].items():
                o = out.get(v, (0.0, 0.0, 0.0))
                out[v] = (o[0] + d[0], o[1] + d[1], o[2] + d[2])
        return out
    da, db = merged(a), merged(b)
    return _covers(da, db) or _covers(db, da)


def combo_recipes(deltas, panel, lookup=None):
    """→ {预设（1.0 名）: [(表情名, 权重, 部位 1 眉 / 2 目 / 3 口)]}（ARKit 左右一对时同一部位有两项）。

    deltas：表情名 → 顶点偏移（只算有顶点偏移的表情）；panel：表情名 → PMX 面板。
    每个部位按 COMBO 的候选顺序找（名字按 Lookup 规则匹配）。MMD 名的候选放在别的脸部面板
    （眉 / 目 / 口）时不算（比如眼睛面板的「喜び」不当眉毛）；「其他」面板不限制（有的模型眉毛放在那里）。
    和已选的部位动同一批顶点（_covers）的候选跳过、换下一个。至少两个部位找到才组合。
    """
    lookup = lookup or Lookup(deltas)
    out = {}
    for p, parts in COMBO.items():
        got = []                              # [(名字元组, 权重, 部位)]
        for pn, cands in parts:
            for c, w in cands:
                if c == EN:
                    n = _en_part(lookup, p, pn)
                    names = (n,) if n else None
                elif isinstance(c, tuple):
                    names = lookup.all(c)
                else:
                    n = lookup.get(c)
                    names = (n,) if n and panel.get(n) in (pn, 0, 4, None) else None
                if not names or any(n not in deltas for n in names):
                    continue
                if all(not set(names) & set(g[0]) and not _covers_any(deltas, names, g[0]) for g in got):
                    got.append((names, w, pn))
                    break
        if len(got) >= 2:
            out[p] = [(n, w, pn) for names, w, pn in got for n in names]
    return out


_WINK = re.compile(r"ウィンク|ｳｨﾝｸ|wink", re.I)
_BLINK_SIDE = re.compile(r"(?:blink|eye_?close[d]?|eye_def_c)[._ ]?(?:l|r|left|right)$|[左右]眨眼|眨眼[左右]", re.I)


def _wink_rank(name):
    """单眼闭眼的表情 → 优先级（0 = 名字就是单眼闭眼：eyeBlinkLeft、blink_R、左眨眼；
    1 = ウィンク；2 = ウィンク２ 这类笑眼版）；不是单眼闭眼 → None。"""
    if _BLINK_SIDE.search(re.sub(r"(?<=[a-z])(?=[A-Z])", "_", name or "")):
        return 0
    if _WINK.search(name or ""):
        return 2 if re.search(r"[2２]", name) else 1
    return None


def _wink_sides(flat, vertices):
    """→ {("L" / "R", 优先级): 表情名}，同一格取最前面的。

    MMD 惯例「ウィンク」闭左眼、「ウィンク右」闭右眼、「ウィンク２」是同一只眼的笑眼版，
    但不少模型不守（鸣潮的「ウィンク２」闭右眼，有的「ウィンク」闭右眼）；ARKit 的 eyeBlinkLeft
    是角色自己的左眼还是镜像也看工具。所以看顶点：PMX 里角色的左边是 +X，动的顶点九成以上在一边才算。
    """
    out = {}
    for n, d, _, _ in flat:
        rank = _wink_rank(n)
        if not d or rank is None:
            continue
        left = sum(1 for v in d if vertices[v]["pos"][0] > 0) / len(d)
        side = "L" if left >= 0.9 else "R" if left <= 0.1 else None
        if side:
            out.setdefault((side, rank), n)
    return out


def wink_presets(flat, vertices, is1):
    """单眼眨眼 → {表情名: blinkLeft / blinkRight}（0.x：blink_l / blink_r）；按 _wink_rank 的优先级。"""
    sides = _wink_sides(flat, vertices)
    to = {"L": "blinkLeft" if is1 else "blink_l", "R": "blinkRight" if is1 else "blink_r"}
    out = {}
    for side in ("L", "R"):
        n = next((sides[(side, r)] for r in (0, 1, 2) if (side, r) in sides), None)
        if n:
            out[n] = to[side]
    return out


def _table_preset(name, is1, keys=()):
    """MMD 日文名、中文名 → 预设（按 _key 比较，ｷﾘｯ / キリッ、あ２ / あ2 都认）。
    keys：模型里所有表情名的 _key；给了的话也认变体后缀（啊2、まばたきA），前提是模型里没有不带后缀的那个。"""
    k = _key(name)
    if k not in _TABLE_KEY[is1] and k not in _CN_KEY:
        m = re.fullmatch(r"(.+?)[ab123]", k)
        if m and m.group(1) not in keys:
            k = m.group(1)
    p = _TABLE_KEY[is1].get(k)
    if p:
        return p
    p = _CN_KEY.get(k)
    if p and not is1:
        p = _TO_0X.get(p, p)
        p = p if p in PRESETS_0 else None
    return p


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


_REPORT_ORDER = ["happy", "angry", "sad", "relaxed", "surprised", "blink", "blinkLeft", "blinkRight",
                 "aa", "ih", "ou", "ee", "oh"]


def report(expr_preset, blend_groups, is1, log):
    """一行汇总：每个预设用了哪些表情（权重不是 1 的标 ×w），缺哪些。眨眼、「あ」缺了是警告。"""
    got, miss = [], []
    for p in _REPORT_ORDER:
        if is1:
            obj = expr_preset.get(p)
            src = obj and ((obj.get("extras") or {}).get("source") or p)
            ws = [b["weight"] for b in (obj or {}).get("morphTargetBinds", [])]
        else:
            p0 = _TO_0X.get(p, p) if p != "surprised" else None
            gr = next((g for g in blend_groups if g["presetName"] == p0 or
                       (p0 is None and g["name"] == "Surprised")), None)
            src = gr and gr["name"]
            ws = [b["weight"] / 100.0 for b in (gr or {}).get("binds", [])]
        if not src:
            miss.append(p)
            continue
        names = src.split("+") if len(ws) > 1 and len(src.split("+")) == len(ws) else [src]
        if len(names) == len(ws):
            src = " + ".join(n if abs(w - 1) < 1e-6 else "%s×%g" % (n, round(w, 3)) for n, w in zip(names, ws))
        got.append("%s ← %s" % (p, src))
    log("表情预设：" + "；".join(got) + ("" if not miss else "　· 模型里找不到：" + "、".join(miss)),
        "warn" if {"blink", "aa"} & set(miss) else None)


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
    n_mat_expr = 0
    winks = wink_presets(flat, mats.model["vertices"], is1)
    # 日文名 / 中文名占用的预设，英文名不抢
    keys = {_key(n) for n, *_ in flat}
    jp_presets = {_table_preset(n, is1, keys) for n, *_ in flat} - {None} | set(winks.values())
    used_0x = set()
    mat_verts = None
    n_uv_expr = n_uv_bad = 0
    slot_of = {}                     # PMX 表情名 → morph target 序号（组合预设用）
    delta_of = {}                    # PMX 表情名 → 顶点偏移（组合时查重叠）
    for mname, deltas, _, _ in flat:
        if deltas:
            delta_of.setdefault(mname, deltas)
    panel = {mo["name"]: mo.get("panel") for mo in mats.model["morphs"]}
    lookup = Lookup(delta_of)
    recipes = combo_recipes(delta_of, panel, lookup) if combo else {}

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

        preset = winks.get(mname) or _table_preset(mname, is1, keys)
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
    # ---- 名字对应不上的预设：ARKit 的眨眼 / 口型（FALLBACK），眨眼再退一步用左右单眼合起来
    def add_multi(p, binds, label):
        if is1:
            expr_preset[p] = {"morphTargetBinds": [{"node": 0, "index": slot_of[n], "weight": w} for n, w in binds],
                              "isBinary": False, "extras": {"source": label}}
        else:
            blend_groups.append({"name": uniq_name(label, {g["name"] for g in blend_groups}),
                                 "presetName": _TO_0X.get(p, p),
                                 "binds": [{"mesh": 0, "index": slot_of[n], "weight": round(w * 100, 2)}
                                           for n, w in binds],
                                 "materialValues": [], "isBinary": False})

    def have(p):
        return p in expr_preset if is1 else any(g["presetName"] == _TO_0X.get(p, p) for g in blend_groups)

    for p, items in FALLBACK.items():
        first = lookup.all(items[0][0])
        if have(p) or not first:
            continue
        binds = [(n, items[0][1]) for n in first]
        for names, w in items[1:]:
            binds += [(n, w) for n in lookup.all(names) or ()]
        add_multi(p, binds, "+".join(n for n, _ in binds))
    by_side = {v: k for k, v in winks.items()}
    lr = [by_side.get("blinkLeft" if is1 else "blink_l"), by_side.get("blinkRight" if is1 else "blink_r")]
    if not have("blink") and all(n in slot_of for n in lr if n) and all(lr):
        add_multi("blink", [(lr[0], 1.0), (lr[1], 1.0)], "+".join(lr))
    report(expr_preset, blend_groups, is1, log)
    return {"target_names": target_names, "preset": expr_preset, "custom": expr_custom,
            "blend_groups": blend_groups}
