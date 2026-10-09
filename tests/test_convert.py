# -*- coding: utf-8 -*-
"""不依赖真实模型的测试：用 tests/synth.py 合成的 PMX 跑转换、后调整（vrm_edit）、压缩包输入。

    python -m unittest discover -s tests -v

Schema 校验需要 jsonschema（requirements-dev.txt），贴图瘦身需要 Pillow；缺了对应的测试会跳过。
"""
import copy
import io
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, os.path.join(ROOT, "convert"), os.path.join(ROOT, "formats"), os.path.join(ROOT, "tools", "qa")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import archive  # noqa: E402
import check_vrm  # noqa: E402
import pipeline  # noqa: E402
import pmxio  # noqa: E402
import pmx2vrm  # noqa: E402
import synth  # noqa: E402
import vrm_edit  # noqa: E402
import vrmio  # noqa: E402
from vrmconv import mesh, morphbake, textures  # noqa: E402

_VALIDATORS = None


def validators():
    global _VALIDATORS
    if _VALIDATORS is None:
        _VALIDATORS = check_vrm.load_validators()
    return _VALIDATORS[0]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="m2v_test_")
        cls.pmx = synth.build(os.path.join(cls.tmp, "src"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def convert(self, name, **kw):
        out = os.path.join(self.tmp, name + ".vrm")
        pmx2vrm.convert(self.pmx, out, **kw)
        return out

    def assertValid(self, path):
        errs, _, summary = check_vrm.check(path, validators())
        self.assertEqual(errs, [], "\n".join(errs))
        return summary


class ConvertTest(Base):
    def test_vrm1(self):
        out = self.convert("v1")
        s = self.assertValid(out)
        self.assertEqual(s["spec"], "1.0")
        self.assertEqual(s["springs"], 1)
        g, _ = vrmio.read_glb(out)
        ex = g["extensions"]["VRMC_vrm"]["expressions"]
        self.assertEqual(set(ex["preset"]), {"blink", "aa", "happy"})
        # 来源 PMX 表情名记在 extras 里（vrm_edit 靠它重新指定预设）
        self.assertEqual(ex["preset"]["happy"]["extras"]["source"], "にこり")
        self.assertIn("笑い", ex["custom"])
        self.assertIn("照れ", ex["custom"])
        # 只输出通用 VRM：不加某个程序私有的自定义表情
        self.assertFalse({"faceRed", "faceBlue", "hideHighlight", "tongue"} & set(ex["custom"]))

    def test_vrm0_lowercase_presets(self):
        out = self.convert("v0", spec="0.x")
        self.assertValid(out)
        g, _ = vrmio.read_glb(out)
        groups = g["extensions"]["VRM"]["blendShapeMaster"]["blendShapeGroups"]
        presets = [gr["presetName"] for gr in groups]
        self.assertTrue(all(p == p.lower() for p in presets), presets)
        self.assertEqual(presets.count("joy"), 1)          # にこり 和 笑い 只能有一个是 joy
        self.assertIn("blink", presets)

    def test_meta(self):
        meta = {"title": "T", "author": "me", "avatar_permission": "onlyAuthor",
                "commercial_usage": "corporation", "allow_redistribution": True,
                "modification": "allowModification", "credit_notation": "unnecessary", "bogus": 1}
        out = self.convert("meta", meta=meta)
        self.assertValid(out)
        m = vrmio.read_glb(out)[0]["extensions"]["VRMC_vrm"]["meta"]
        self.assertEqual((m["name"], m["authors"], m["avatarPermission"], m["commercialUsage"]),
                         ("T", ["me"], "onlyAuthor", "corporation"))
        self.assertTrue(m["allowRedistribution"])
        self.assertEqual((m["modification"], m["creditNotation"]), ("allowModification", "unnecessary"))
        out0 = self.convert("meta0", spec="0.x", meta=meta)
        m0 = vrmio.read_glb(out0)[0]["extensions"]["VRM"]["meta"]
        self.assertEqual((m0["allowedUserName"], m0["commercialUssageName"]), ("OnlyAuthor", "Allow"))

    def test_auto_spring(self):
        # 去掉所有刚体：头发骨（髪1～3）应按名字自动补成 SpringBone，身体补碰撞体
        import pmxio
        m = pmxio.read_pmx(self.pmx)
        m["rigid_bodies"], m["joints"] = [], []
        bare = os.path.join(self.tmp, "src", "nophys.pmx")
        pmxio.write_pmx(m, bare)
        out = os.path.join(self.tmp, "auto.vrm")
        pmx2vrm.convert(bare, out)
        s = self.assertValid(out)
        self.assertEqual(s["springs"], 1)
        self.assertGreaterEqual(s["colliders"], 5)
        out2 = os.path.join(self.tmp, "noauto.vrm")
        pmx2vrm.convert(bare, out2, auto_spring=False)
        self.assertNotIn("VRMC_springBone", vrmio.read_glb(out2)[0]["extensions"])

    def test_sideways_model_is_turned(self):
        # 侧着的模型（Unreal 来的 psk 常见）：转正后和正面的结果一样
        import pmxio
        m = pmxio.read_pmx(self.pmx)
        m["rigid_bodies"], m["joints"] = [], []
        front = os.path.join(self.tmp, "src", "front.pmx")
        pmxio.write_pmx(m, front)

        def turn(v):                   # 绕 Y 转 -90°：左手从 +X 转到 +Z
            return (-v[2], v[1], v[0])
        for v in m["vertices"]:
            v["pos"], v["normal"] = turn(v["pos"]), turn(v["normal"])
        for b in m["bones"]:
            b["pos"] = turn(b["pos"])
        for mo in m["morphs"]:
            if mo["kind"] == 1:
                mo["offsets"] = [(i, turn(o)) for i, o in mo["offsets"]]
        side = os.path.join(self.tmp, "src", "side.pmx")
        pmxio.write_pmx(m, side)
        a, b = os.path.join(self.tmp, "front.vrm"), os.path.join(self.tmp, "side.vrm")
        pmx2vrm.convert(front, a)
        pmx2vrm.convert(side, b)
        ga, gb = vrmio.read_glb(a)[0], vrmio.read_glb(b)[0]
        ha = ga["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["leftHand"]["node"]
        hb = gb["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["leftHand"]["node"]
        wa, wb = vrmio.node_world_matrices(ga)[ha], vrmio.node_world_matrices(gb)[hb]
        for k in range(3):
            self.assertAlmostEqual(vrmio.world_translation(wa)[k], vrmio.world_translation(wb)[k], places=3)

    def test_deterministic(self):
        a, b = self.convert("det_a"), self.convert("det_b")
        with open(a, "rb") as fa, open(b, "rb") as fb:
            self.assertEqual(fa.read(), fb.read())

    @unittest.skipUnless(textures.available(), "需要 Pillow")
    def test_texture_slimming(self):
        big = synth.build(os.path.join(self.tmp, "big"), tex_size=1024)
        plain, small = os.path.join(self.tmp, "tex_plain.vrm"), os.path.join(self.tmp, "tex_small.vrm")
        pmx2vrm.convert(big, plain)
        pmx2vrm.convert(big, small, tex_max=256, tex_jpeg=True)
        self.assertValid(small)
        self.assertLess(os.path.getsize(small), os.path.getsize(plain))
        g, _ = vrmio.read_glb(small)
        mimes = sorted(i["mimeType"] for i in g["images"])
        # 不透明的身体贴图 → JPEG；带透明的头发贴图保持 PNG（裁剪模式要用 alpha）
        self.assertEqual(mimes, ["image/jpeg", "image/png"])
        hair = [m for m in g["materials"] if m["name"] == "髪"][0]
        self.assertEqual(hair["alphaMode"], "MASK")


class EditTest(Base):
    def setUp(self):
        self.v1 = self.convert("e1")
        self.v0 = self.convert("e0", spec="0.x")
        thumb = os.path.join(self.tmp, "thumb.png")
        with open(thumb, "wb") as f:
            f.write(synth._png(32, 32, alpha=False))
        self.edits = {"title": "改名", "thumbnail": thumb,
                      "expressions": {"happy": "笑い", "joy": "笑い", "blink": "", "aa": "まばたき"},
                      "spring": {"stiffness": 2.0, "gravity": 0.2, "chains": {}}}

    def _read(self, p):
        with open(p, "rb") as f:
            return f.read()

    def test_apply_restore_1(self):
        orig = self._read(self.v1)
        info = vrm_edit.describe(self.v1)
        self.assertEqual(info["assign"]["happy"], "にこり")
        self.assertEqual(len(info["springs"]), 1)
        vrm_edit.apply(self.v1, self.edits)
        self.assertValid(self.v1)
        g, _ = vrmio.read_glb(self.v1)
        v = g["extensions"]["VRMC_vrm"]
        self.assertEqual(v["meta"]["name"], "改名")
        self.assertIsInstance(v["meta"]["thumbnailImage"], int)
        self.assertNotIn("blink", v["expressions"]["preset"])
        self.assertEqual(v["expressions"]["preset"]["happy"]["extras"]["source"], "笑い")
        self.assertIn("にこり", v["expressions"]["custom"])        # 原来的 happy 回到自定义
        j = g["extensions"]["VRMC_springBone"]["springs"][0]["joints"][0]
        base = info["springs"][0]["joints"][0]
        self.assertAlmostEqual(j["stiffness"], round(base["stiffness"] * 2, 4))
        self.assertAlmostEqual(j["gravityPower"], round(base["gravityPower"] + 0.2, 4))
        # 再套一次结果不变（从原始值重新套，不累乘）
        once = self._read(self.v1)
        vrm_edit.apply(self.v1, self.edits)
        self.assertEqual(self._read(self.v1), once)
        # describe 看到的仍是原始值
        self.assertEqual(vrm_edit.describe(self.v1)["assign"], info["assign"])
        # 空调整 = 还原
        vrm_edit.apply(self.v1, {})
        g0, b0 = vrmio.read_glb(self.v1)
        self.assertNotIn("extras", g0)
        self.assertEqual(len(self._read(self.v1)), len(orig))

    def test_intensity(self):
        """预设强度：形态键权重相乘，单个最多到 1；还原后回到原值。"""
        def happy():
            g, _ = vrmio.read_glb(self.v1)
            return [b["weight"] for b in g["extensions"]["VRMC_vrm"]["expressions"]["preset"]["happy"]["morphTargetBinds"]]
        orig = happy()
        vrm_edit.apply(self.v1, {"intensity": {"happy": 0.5}})
        self.assertValid(self.v1)
        self.assertEqual(happy(), [round(w * 0.5, 4) for w in orig])
        vrm_edit.apply(self.v1, {"intensity": {"happy": 2.0}})
        self.assertEqual(happy(), [min(1.0, w * 2) for w in orig])
        vrm_edit.apply(self.v1, {"intensity": {"happy": 1.0}})       # 100% = 没调
        self.assertEqual(happy(), orig)
        g, _ = vrmio.read_glb(self.v1)
        self.assertNotIn("extras", g)

    def test_intensity_0x(self):
        vrm_edit.apply(self.v0, {"intensity": {"joy": 0.5}})
        self.assertValid(self.v0)
        g, _ = vrmio.read_glb(self.v0)
        joy = [gr for gr in g["extensions"]["VRM"]["blendShapeMaster"]["blendShapeGroups"] if gr["presetName"] == "joy"]
        self.assertEqual([b["weight"] for b in joy[0]["binds"]], [50.0])
        vrm_edit.apply(self.v0, {})

    def test_chain_off(self):
        info = vrm_edit.describe(self.v1)
        key = info["springs"][0]["key"]
        vrm_edit.apply(self.v1, {"spring": {"chains": {key: {"off": True}}}})
        self.assertValid(self.v1)
        g, _ = vrmio.read_glb(self.v1)
        self.assertNotIn("VRMC_springBone", g["extensions"])      # 唯一的链关掉 → 整个扩展去掉
        self.assertNotIn("VRMC_springBone", g["extensionsUsed"])
        vrm_edit.apply(self.v1, {})
        self.assertIn("VRMC_springBone", vrmio.read_glb(self.v1)[0]["extensions"])

    def test_apply_restore_0(self):
        orig = vrmio.read_glb(self.v0)
        vrm_edit.apply(self.v0, self.edits)
        self.assertValid(self.v0)
        g, _ = vrmio.read_glb(self.v0)
        v = g["extensions"]["VRM"]
        self.assertEqual(v["meta"]["title"], "改名")
        self.assertIsInstance(v["meta"]["texture"], int)
        by = {gr["presetName"]: gr["name"] for gr in v["blendShapeMaster"]["blendShapeGroups"]}
        self.assertEqual(by["joy"], "笑い")
        self.assertNotIn("blink", by)
        vrm_edit.apply(self.v0, {})
        self.assertEqual(vrmio.read_glb(self.v0), orig)

    def test_is_empty(self):
        self.assertTrue(vrm_edit.is_empty({}))
        self.assertTrue(vrm_edit.is_empty({"spring": {"stiffness": 1.0, "chains": {"a": {}}}, "title": ""}))
        self.assertFalse(vrm_edit.is_empty({"spring": {"chains": {"a": {"off": True}}}}))


def morph_deltas(g, b, name):
    """VRM 里某个 morph target 的 [(顶点位置, 偏移)]（只要有偏移的顶点）。"""
    names = g["meshes"][0]["extras"]["targetNames"]
    t = names.index(name)
    out = []
    for pr in g["meshes"][0]["primitives"]:
        pos = vrmio.read_accessor(g, b, pr["attributes"]["POSITION"])
        d = vrmio.read_accessor(g, b, pr["targets"][t]["POSITION"])
        for i in range(0, len(d), 3):
            if abs(d[i]) + abs(d[i + 1]) + abs(d[i + 2]) > 1e-7:
                out.append((pos[i:i + 3], d[i:i + 3]))
    return out


class MorphBakeTest(Base):
    """骨骼表情 / 视线表情烘焙成顶点偏移，UV 表情换成贴图偏移。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pmx = synth.build(os.path.join(cls.tmp, "eyes"), "眼モデル", eyes=True)

    def test_vrm1(self):
        out = self.convert("eyes1")
        self.assertValid(out)
        g, b = vrmio.read_glb(out)
        ex = g["extensions"]["VRMC_vrm"]["expressions"]
        for k in ("lookUp", "lookDown", "lookLeft", "lookRight"):
            self.assertIn(k, ex["preset"])
        # VRM 1.0：角色的左边是 +X、上是 +Y
        left = morph_deltas(g, b, "lookLeft")
        self.assertEqual(len(left), 16)                          # 两个眼珠方块
        self.assertTrue(all(d[0] > 0 for _, d in left))
        self.assertTrue(all(d[1] > 0 for _, d in morph_deltas(g, b, "lookUp")))
        # 寄り目：两眼往中间（左眼 x>0 往 -X，右眼往 +X）
        cross = morph_deltas(g, b, "寄り目")
        self.assertEqual(len(cross), 16)
        self.assertTrue(all((p[0] > 0) == (d[0] < 0) for p, d in cross))
        # グループ里的骨骼表情也在
        self.assertEqual(len(morph_deltas(g, b, "見開く")), 16 + 4)
        # UV 表情 → textureTransformBinds：整块往右下移 (0.5, 0.25)
        tt = ex["custom"]["怒マーク"]["textureTransformBinds"]
        self.assertEqual(len(tt), 1)
        self.assertEqual(g["materials"][tt[0]["material"]]["name"], "漫符")
        self.assertEqual([round(x, 4) for x in tt[0]["scale"] + tt[0]["offset"]], [1, 1, 0.5, 0.25])
        self.assertNotIn("morphTargetBinds", ex["custom"]["怒マーク"])

    def test_vrm0(self):
        out = self.convert("eyes0", spec="0.x")
        self.assertValid(out)
        groups = vrmio.read_glb(out)[0]["extensions"]["VRM"]["blendShapeMaster"]["blendShapeGroups"]
        by = {x["name"]: x for x in groups}
        self.assertEqual(by["lookLeft"]["presetName"], "lookleft")
        mv = by["怒マーク"]["materialValues"]
        self.assertEqual(mv[0]["propertyName"], "_MainTex_ST")
        # Unity 的 v 从下往上：(sx, sy, ox, 1 - sy - oy)
        self.assertEqual([round(x, 4) for x in mv[0]["targetValue"]], [1, 1, 0.5, -0.25])

    def test_look_range_from_eye_geometry(self):
        """lookAt 的最大转角按眼球几何算：瞳孔走到 LOOK_TRAVEL × 瞳孔宽度；上下按比例缩小。"""
        out = self.convert("eyes_look")
        g, _ = vrmio.read_glb(out)
        la = g["extensions"]["VRMC_vrm"]["lookAt"]
        h = la["rangeMapHorizontalOuter"]["outputScale"]
        self.assertTrue(8.0 <= h <= 20.0, h)
        self.assertNotEqual(h, 10.0)                       # 不是以前的固定值
        self.assertEqual(la["rangeMapHorizontalInner"]["outputScale"], h)
        self.assertAlmostEqual(la["rangeMapVerticalDown"]["outputScale"], h * 0.7, places=1)
        self.assertAlmostEqual(la["rangeMapVerticalUp"]["outputScale"], h * 0.5, places=1)
        # 没有眼骨下的顶点、或者挂在眼骨上的东西不像一只眼睛时退回 10°（日志说明原因）
        m = pmxio.read_pmx(self.pmx)
        parents = [b["parent"] for b in m["bones"]]
        idx = {b["name"]: i for i, b in enumerate(m["bones"])}
        eyes = [idx["左目"], idx["右目"]]
        look, note = morphbake.eye_look_range(m, [[] for _ in m["vertices"]], parents, eyes)
        self.assertIsNone(look)
        self.assertIn("10°", note)
        whole = [[(idx["左目"], 1.0)] for _ in m["vertices"]]        # 整个模型都挂在左眼上
        look, note = morphbake.eye_look_range(m, whole, parents, eyes)
        self.assertIsNone(look)
        self.assertIn("不像一只眼睛", note)
        look, note = morphbake.eye_look_range(m, mesh.vertex_weights(m["vertices"], len(m["bones"]), {}),
                                              parents, eyes)
        self.assertIsNotNone(look)

    def test_tune_look(self):
        """调整面板改视线最大转角：describe 给转换时的值，apply 改 rangeMap，空调整还原。"""
        out = self.convert("eyes_tune")
        info = vrm_edit.describe(out)
        orig = info["look"]
        self.assertTrue(8.0 <= orig["h"] <= 20.0)
        vrm_edit.apply(out, {"lookAt": {"h": 25, "down": 5, "up": 0}})
        self.assertValid(out)
        la = vrmio.read_glb(out)[0]["extensions"]["VRMC_vrm"]["lookAt"]
        self.assertEqual([la[k]["outputScale"] for k in ("rangeMapHorizontalInner", "rangeMapHorizontalOuter",
                                                         "rangeMapVerticalDown", "rangeMapVerticalUp")], [25, 25, 5, 0])
        self.assertEqual(vrm_edit.describe(out)["look"], orig)         # describe 仍是转换时的值
        vrm_edit.apply(out, {})
        la = vrmio.read_glb(out)[0]["extensions"]["VRMC_vrm"]["lookAt"]
        self.assertEqual(la["rangeMapHorizontalOuter"]["outputScale"], orig["h"])
        self.assertEqual(la["rangeMapVerticalUp"]["outputScale"], orig["up"])

    def test_gaze_fields_exact(self):
        """视线表情 = 横向场 × sinθ/sinΘ + 深度场 × (1−cosθ)/(1−cosΘ)：任意角度都等于直接转眼骨。"""
        m = pmxio.read_pmx(self.pmx)
        idx = {b["name"]: i for i, b in enumerate(m["bones"])}
        eyes = (idx["左目"], idx["右目"])
        flat = [(n, morphbake.BoneBaker(m).deltas(o), [], {}) for n, o in morphbake.gaze_offsets(
            *eyes, {"lookLeft": 40.0, "lookRight": 40.0, "lookUp": 30.0, "lookDown": 30.0})]
        split = morphbake.split_gaze(flat, {"lookLeft", "lookRight", "lookUp", "lookDown"})
        self.assertEqual(split["lookDown"], ("lookDown", "lookUpDown_depth", "v"))
        field = {n: d for n, d, _, _ in flat}
        for p, deg in (("lookLeft", 13.0), ("lookDown", 7.5), ("lookRight", 40.0)):
            tan, depth, axis = split[p]
            wt, wd = morphbake.gaze_weights(deg, axis)
            direct = morphbake.BoneBaker(m).deltas(dict(morphbake.gaze_offsets(
                *eyes, {"lookLeft": deg, "lookRight": deg, "lookUp": deg, "lookDown": deg}))[p])
            for v, d in direct.items():
                t, z = field[tan].get(v, (0, 0, 0)), field[depth].get(v, (0, 0, 0))
                for k in range(3):
                    self.assertAlmostEqual(t[k] * wt + z[k] * wd, d[k], places=5)

    def test_tune_look_moves_gaze_expressions(self):
        out = self.convert("eyes_gaze_w")
        g = vrmio.read_glb(out)[0]
        names = g["meshes"][0]["extras"]["targetNames"]
        ex = g["extensions"]["VRMC_vrm"]["expressions"]
        self.assertNotIn("lookLeftRight_depth", ex.get("custom", {}))          # 深度场只是 morph target
        h = vrm_edit.describe(out)["look"]["h"]
        binds = ex["preset"]["lookLeft"]["morphTargetBinds"]
        self.assertEqual([names[b["index"]] for b in binds], ["lookLeft", "lookLeftRight_depth"])
        self.assertEqual([b["weight"] for b in binds], list(morphbake.gaze_weights(h, "h")))
        vrm_edit.apply(out, {"lookAt": {"h": 30, "down": 5}})
        self.assertValid(out)
        ex = vrmio.read_glb(out)[0]["extensions"]["VRMC_vrm"]["expressions"]["preset"]
        self.assertEqual([b["weight"] for b in ex["lookRight"]["morphTargetBinds"]], list(morphbake.gaze_weights(30, "h")))
        self.assertEqual([b["weight"] for b in ex["lookDown"]["morphTargetBinds"]], list(morphbake.gaze_weights(5, "v")))
        vrm_edit.apply(out, {})                                                   # 还原
        ex = vrmio.read_glb(out)[0]["extensions"]["VRMC_vrm"]["expressions"]["preset"]
        self.assertEqual([b["weight"] for b in ex["lookLeft"]["morphTargetBinds"]], list(morphbake.gaze_weights(h, "h")))

    def test_no_gaze(self):
        out = self.convert("eyes_nogaze", gaze=False)
        ex = vrmio.read_glb(out)[0]["extensions"]["VRMC_vrm"]["expressions"]
        self.assertNotIn("lookLeft", ex["preset"])
        self.assertIn("寄り目", ex["custom"])

    def test_plain_model_has_no_gaze(self):
        """眼骨上没有网格（synth 默认）：转眼骨动不了任何顶点，不补。"""
        pmx = synth.build(os.path.join(self.tmp, "plain"))
        out = os.path.join(self.tmp, "plain.vrm")
        pmx2vrm.convert(pmx, out)
        self.assertNotIn("lookLeft", vrmio.read_glb(out)[0]["extensions"]["VRMC_vrm"]["expressions"]["preset"])


class ComboTest(Base):
    """情绪预设组合眉 / 眼 / 嘴：按 PMX 面板挑表情，原来单个对应的回到自定义。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        m = pmxio.read_pmx(synth.build(os.path.join(cls.tmp, "combo"), "組合モデル"))
        hv = 5 * 8                                   # 头部方块的顶点
        # 「怒り目」带着「怒り」的眉毛（同样的顶点、更大的偏移）：和怒り一起用会动两遍，跳过它用ｷﾘｯ
        for name, panel, vs in (("怒り", 1, (0, 1)), ("怒り目", 2, (0, 1, 2)), ("ｷﾘｯ", 2, (2, 3)),
                                ("∧", 3, (4, 5)), ("喜び", 2, (2, 3)), ("上", 1, (6,)), ("びっくり", 2, (7,))):
            m["morphs"].append({"name": name, "name_en": name, "panel": panel, "kind": 1,
                                "offsets": [(hv + k, (0.0, 0.05 * panel, 0.0)) for k in vs]})
        # 单眼眨眼按顶点在哪边定（角色的左边是 +X），不按名字：这里故意和 MMD 惯例反着来
        left = [hv + k for k in range(8) if m["vertices"][hv + k]["pos"][0] > 0]
        right = [hv + k for k in range(8) if m["vertices"][hv + k]["pos"][0] < 0]
        for name, vs in (("ウィンク２", left), ("ウィンク", right), ("ウィンク右", left)):
            m["morphs"].append({"name": name, "name_en": name, "panel": 2, "kind": 1,
                                "offsets": [(v, (0.0, -0.01, 0.0)) for v in vs]})
        cls.pmx = os.path.join(cls.tmp, "combo", "組合モデル.pmx")
        pmxio.write_pmx(m, cls.pmx)

    def binds(self, g, obj):
        names = g["meshes"][0]["extras"]["targetNames"]
        return [(names[b["index"]], b["weight"]) for b in obj["morphTargetBinds"]]

    def test_vrm1(self):
        out = self.convert("combo1")
        self.assertValid(out)
        g = vrmio.read_glb(out)[0]
        ex = g["extensions"]["VRMC_vrm"]["expressions"]
        self.assertEqual(self.binds(g, ex["preset"]["angry"]), [("怒り", 0.5), ("ｷﾘｯ", 0.3), ("∧", 0.3)])
        self.assertEqual(ex["preset"]["angry"]["extras"]["source"], "怒り+ｷﾘｯ+∧")
        self.assertIn("怒り", ex["custom"])                        # 原来单个对应的还在
        self.assertNotIn("overrideBlink", ex["preset"]["angry"])    # 生气时照样眨眼
        self.assertEqual(ex["preset"]["happy"]["overrideBlink"], "blend")   # 笑眼不再叠加眨眼
        # synth 的「にこり」「笑い」在口面板，不当眉毛 / 眼睛：眉毛用「上」、眼睛用「喜び」
        self.assertEqual(self.binds(g, ex["preset"]["happy"]), [("上", 0.3), ("喜び", 1.0)])
        self.assertIn("にこり", ex["custom"])
        # 只找到一个部位（嘴）→ 不组合，没有 sad
        self.assertNotIn("sad", ex["preset"])

    def test_wink_by_side(self):
        out = self.convert("wink1")
        g = vrmio.read_glb(out)[0]
        ex = g["extensions"]["VRMC_vrm"]["expressions"]
        self.assertEqual(self.binds(g, ex["preset"]["blinkRight"]), [("ウィンク", 1.0)])
        # 左边有「ウィンク２」「ウィンク右」两个：名字里没有「２」的优先
        self.assertEqual(self.binds(g, ex["preset"]["blinkLeft"]), [("ウィンク右", 1.0)])
        self.assertIn("ウィンク２", ex["custom"])

    def test_vrm0(self):
        out = self.convert("combo0", spec="0.x")
        self.assertValid(out)
        groups = vrmio.read_glb(out)[0]["extensions"]["VRM"]["blendShapeMaster"]["blendShapeGroups"]
        by = {x["name"]: x for x in groups}
        self.assertEqual(by["怒り+ｷﾘｯ+∧"]["presetName"], "angry")
        self.assertEqual([b["weight"] for b in by["怒り+ｷﾘｯ+∧"]["binds"]], [50, 30, 30])
        self.assertEqual(by["怒り"]["presetName"], "unknown")
        # 0.x 没有 surprised 预设：和 VRoid 一样是 unknown + 名字 Surprised
        self.assertEqual(by["Surprised"]["presetName"], "unknown")
        self.assertEqual([b["weight"] for b in by["Surprised"]["binds"]], [70, 100])
        self.assertNotIn("surprised", {x["presetName"] for x in groups})

    def test_off(self):
        out = self.convert("combo_off", combo_expr=False)
        g = vrmio.read_glb(out)[0]
        self.assertEqual(self.binds(g, g["extensions"]["VRMC_vrm"]["expressions"]["preset"]["angry"]),
                         [("怒り", 1.0)])

    def test_tune_back_to_single(self):
        out = self.convert("combo_edit")
        info = vrm_edit.describe(out)
        self.assertEqual(info["assign"]["angry"], "怒り+ｷﾘｯ+∧")
        vrm_edit.apply(out, {"expressions": {"angry": "怒り"}})
        self.assertValid(out)
        g = vrmio.read_glb(out)[0]
        ex = g["extensions"]["VRMC_vrm"]["expressions"]
        self.assertEqual(self.binds(g, ex["preset"]["angry"]), [("怒り", 1.0)])
        self.assertIn("怒り+ｷﾘｯ+∧", ex["custom"])


class OtherNamingTest(Base):
    """不按 MMD 标准起名的模型：ARKit（左右各一个）、中文名；名字靠不住的单眼眨眼按顶点定左右。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        m = pmxio.read_pmx(synth.build(os.path.join(cls.tmp, "arkit"), "ARKitモデル"))
        hv = 5 * 8
        left = [hv + k for k in range(8) if m["vertices"][hv + k]["pos"][0] > 0]
        right = [hv + k for k in range(8) if m["vertices"][hv + k]["pos"][0] < 0]
        rename = {"まばたき": "眨眼", "あ": "啊2", "にこり": "Mouth_Smile1", "笑い": "Mouth_Laugh1"}
        for mo in m["morphs"]:
            mo["name"] = rename.get(mo["name"], mo["name"])
            mo["panel"] = 4
        # 左右各一组：eyeBlinkLeft 故意放在角色右边（-X），看是不是按顶点认
        for name, vs in (("eyeBlinkLeft", right), ("eyeBlinkRight", left), ("eyeWideLeft", left[:2]),
                         ("eyeWideRight", right[:2]), ("browInnerUp", left[2:3] + right[2:3]),
                         ("browOuterUpLeft", left[3:4]), ("browOuterUpRight", right[3:4]),
                         ("jawOpen", left[:1] + right[:1]), ("mouthPucker", left[1:2] + right[1:2])):
            m["morphs"].append({"name": name, "name_en": name, "panel": 4, "kind": 1,
                                "offsets": [(v, (0.0, -0.01, 0.001)) for v in vs]})
        cls.pmx = os.path.join(cls.tmp, "arkit", "ARKitモデル.pmx")
        pmxio.write_pmx(m, cls.pmx)

    def test_presets(self):
        logs = []
        out = os.path.join(self.tmp, "arkit.vrm")
        pmx2vrm.convert(self.pmx, out, log=lambda s, t=None: logs.append((s, t)))
        self.assertValid(out)
        g = vrmio.read_glb(out)[0]
        names = g["meshes"][0]["extras"]["targetNames"]
        pre = g["extensions"]["VRMC_vrm"]["expressions"]["preset"]

        def binds(p):
            return [(names[b["index"]], b["weight"]) for b in pre[p]["morphTargetBinds"]]
        self.assertEqual(binds("blink"), [("眨眼", 1.0)])                 # 中文名
        self.assertEqual(binds("aa"), [("啊2", 1.0)])                     # 中文名 + 变体后缀
        self.assertEqual(binds("ou"), [("mouthPucker", 1.0)])            # ARKit 退路
        self.assertEqual(binds("blinkLeft"), [("eyeBlinkRight", 1.0)])   # 按顶点：它在角色左边
        self.assertEqual(binds("blinkRight"), [("eyeBlinkLeft", 1.0)])
        # 惊讶 = ARKit 的抬眉（三个）+ 睁眼（左右一对）
        self.assertEqual(sorted(n for n, _ in binds("surprised")),
                         sorted(["browInnerUp", "browOuterUpLeft", "browOuterUpRight", "eyeWideLeft", "eyeWideRight"]))
        rep = [s for s, t in logs if s.startswith("表情预设：")]
        self.assertEqual(len(rep), 1)
        self.assertIn("surprised ← browInnerUp×0.7", rep[0])
        self.assertIn("模型里找不到：", rep[0])


class SkeletonTest(Base):
    """MMD 式的骨架：下半身 / 上半身从同一点出发、缺上半身2。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        m = pmxio.read_pmx(synth.build(os.path.join(cls.tmp, "mmd"), "MMD骨架"))
        idx = {b["name"]: i for i, b in enumerate(m["bones"])}
        m["bones"][idx["下半身"]]["pos"] = m["bones"][idx["上半身"]]["pos"]     # 和上半身同一点
        for b in m["bones"]:                                                   # 去掉上半身2
            if b["parent"] == idx["上半身2"]:
                b["parent"] = idx["上半身"]
        m["bones"][idx["上半身2"]]["name"] = "未使用"
        m["bones"][idx["上半身2"]]["parent"] = idx["上半身"]
        cls.pmx = os.path.join(cls.tmp, "mmd", "MMD骨架.pmx")
        pmxio.write_pmx(m, cls.pmx)

    def test_material_names_unique(self):
        """PMX 允许材质重名；按材质名找材质的程序（脸部染色）只会找到第一个。"""
        m = pmxio.read_pmx(self.pmx)
        for mat in m["materials"]:
            mat["name"] = "体"
        pmx = os.path.join(self.tmp, "mmd", "同名材质.pmx")
        pmxio.write_pmx(m, pmx)
        out = os.path.join(self.tmp, "dup.vrm")
        pmx2vrm.convert(pmx, out)
        self.assertValid(out)
        names = [x["name"] for x in vrmio.read_glb(out)[0]["materials"]]
        self.assertEqual(names, ["体", "体_2"])

    def world(self, g, node):
        par = {c: i for i, n in enumerate(g["nodes"]) for c in n.get("children", [])}
        x = [0.0, 0.0, 0.0]
        while node is not None:
            x = [a + b for a, b in zip(x, g["nodes"][node].get("translation", [0, 0, 0]))]
            node = par.get(node)
        return x

    def test_upper_chest_by_hierarchy(self):
        """上半身3 在 上半身 和 上半身2 之间（名字和层级反着）：按层级分，上半身3 是 chest。"""
        m = pmxio.read_pmx(synth.build(os.path.join(self.tmp, "uc"), "上半身3"))
        idx = {b["name"]: i for i, b in enumerate(m["bones"])}
        m["bones"].append({"name": "上半身3", "name_en": "", "pos": (0, 12.2, 0), "parent": idx["上半身"],
                           "layer": 0, "flag": 0x0001 | 0x0002 | 0x0008 | 0x0010, "tail": idx["上半身2"]})
        m["bones"][idx["上半身2"]]["parent"] = len(m["bones"]) - 1
        pmx = os.path.join(self.tmp, "uc", "上半身3.pmx")
        pmxio.write_pmx(m, pmx)
        out, logs = os.path.join(self.tmp, "uc.vrm"), []
        pmx2vrm.convert(pmx, out, log=lambda s, t=None: logs.append(s))
        self.assertValid(out)
        g = vrmio.read_glb(out)[0]
        hb = g["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]
        self.assertEqual(g["nodes"][hb["chest"]["node"]]["name"], "上半身3")
        self.assertEqual(g["nodes"][hb["upperChest"]["node"]]["name"], "上半身2")
        self.assertFalse([s for s in logs if "无法修正" in s], logs)

    def test_hips_and_chest(self):
        out = self.convert("mmd1")
        self.assertValid(out)
        g, b = vrmio.read_glb(out)
        hb = {k: v["node"] for k, v in g["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"].items()}
        hips, spine, leg = (self.world(g, hb[k]) for k in ("hips", "spine", "leftUpperLeg"))
        # hips 移到骨盆：在大腿根和 spine 之间，不和 spine 重合
        self.assertGreater(spine[1] - hips[1], 0.3 * (spine[1] - leg[1]))
        self.assertGreater(hips[1], leg[1])
        # 1.0 也补 chest / neck（游戏的布娃娃要）
        self.assertIn("chest", hb)
        self.assertIn("neck", hb)
        # 蒙皮：每根骨的逆绑定矩阵 = 节点世界坐标取反（hips 挪过也一致）
        skin = g["skins"][0]
        m = vrmio.read_accessor(g, b, skin["inverseBindMatrices"])
        for k, j in enumerate(skin["joints"]):
            w = self.world(g, j)
            for a in range(3):
                self.assertAlmostEqual(-m[16 * k + 12 + a], w[a], places=5)


class SkirtTest(Base):
    """左腿后面一片后裙：两条链分在腿轴两侧（A 在内侧、B 在外侧），裙片朝后。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        m = pmxio.read_pmx(cls.pmx)
        idx = {b["name"]: i for i, b in enumerate(m["bones"])}
        z, ys = 1.5, (10.6, 9.0, 7.4, 5.8)
        cols = {"A": 0.35, "B": 1.45}                      # 左腿轴在 x = 0.9
        for c, x in cols.items():
            parent = idx["下半身"]
            for k, y in enumerate(ys):
                idx["後裙%s%d" % (c, k)] = len(m["bones"])
                m["bones"].append({"name": "後裙%s%d" % (c, k), "name_en": "", "pos": (x, y, z),
                                   "parent": parent, "layer": 0, "flag": 0x0001 | 0x0002 | 0x0008 | 0x0010,
                                   "tail": -1})
                parent = idx["後裙%s%d" % (c, k)]
        v0, xs = len(m["vertices"]), [0.2 + 0.2 * i for i in range(8)]
        for y in ys:
            for x in xs:
                c = "A" if x < 0.9 else "B"
                k = min(range(3), key=lambda k: abs(ys[k] - y))
                m["vertices"].append({"pos": (x, y, z), "normal": (0, 0, 1), "uv": (0, 0), "wtype": 0,
                                      "wbones": [idx["後裙%s%d" % (c, k)]], "wweights": [1.0], "edge": 0.0})
        faces = []
        for r in range(len(ys) - 1):
            for i in range(len(xs) - 1):
                a, b = v0 + r * len(xs) + i, v0 + (r + 1) * len(xs) + i
                faces += [a, b, b + 1, a, b + 1, a + 1]
        m["faces"] += faces
        m["materials"].append(dict(m["materials"][0], name="裙", faces=len(faces)))

        def rb(nm, bone, pos, mode, shape=0, size=(0.3, 0, 0)):
            return {"name": nm, "name_en": nm, "bone": idx[bone], "group": 0 if mode == 0 else 2, "mask": 0xFFFF,
                    "shape": shape, "size": size, "pos": pos, "rot": (0, 0, 0), "mass": 1.0, "move_damp": 0.5,
                    "rot_damp": 0.5, "repulsion": 0.0, "friction": 0.5, "mode": mode}
        m["rigid_bodies"].append(rb("左足", "左足", (0.9, 8.25, 0), 0, shape=2, size=(0.6, 3.5, 0)))
        for c, x in cols.items():
            for k in range(3):
                m["rigid_bodies"].append(rb("後裙%s%d" % (c, k), "後裙%s%d" % (c, k), (x, ys[k], z), 1))
        cls.pmx = os.path.join(cls.tmp, "src", "裙.pmx")
        pmxio.write_pmx(m, cls.pmx)

    def leg_shifts(self, out):
        """每条裙链用到的左腿碰撞体，相对原碰撞体（挂在左足上、半径最大的那个）的平移。"""
        g = vrmio.read_glb(out)[0]
        sb = g["extensions"]["VRMC_springBone"]
        leg = next(i for i, n in enumerate(g["nodes"]) if n.get("name") == "左足")
        caps = {i: c["shape"]["capsule"] for i, c in enumerate(sb["colliders"])
                if c["node"] == leg and "capsule" in c["shape"]}
        orig = caps[min(caps)]
        res = {}
        for sp in sb["springs"]:
            if not sp["name"].startswith("後裙"):
                continue
            ids = [i for gi in sp.get("colliderGroups", []) for i in sb["colliderGroups"][gi]["colliders"] if i in caps]
            self.assertEqual(len(ids), 1)
            res[sp["name"][2]] = [caps[ids[0]]["offset"][k] - orig["offset"][k] for k in range(3)]
        return res

    def test_leg_pushes_along_panel(self):
        out = self.convert("skirt")
        self.assertValid(out)
        d = self.leg_shifts(out)
        # 两条链各自的腿往自己那一侧挪（A 内侧 -x、B 外侧 +x），前后几乎不动：
        # 被腿碰到时两条链都沿裙片法线往后推，而不是被腿轴分到两边
        self.assertLess(d["A"][0], -0.03)
        self.assertGreater(d["B"][0], 0.03)
        for c in "AB":
            self.assertLess(abs(d[c][2]), 0.015)
            self.assertAlmostEqual(d[c][1], 0.0, places=6)

    def test_leg_proxy_off(self):
        d = self.leg_shifts(self.convert("skirt_off", spring_opts={"leg_proxy": False}))
        for c in "AB":
            self.assertEqual(d[c], [0.0, 0.0, 0.0])


class SnapshotTest(Base):
    """tools/qa/snapshots.py：无界面浏览器里用预览组件拍表情对照表（没有 Edge / Chrome 时跳过）。"""

    def test_sheet(self):
        import snapshots
        if not snapshots.find_browser():
            self.skipTest("没有 Edge / Chrome")
        out = os.path.join(self.tmp, "snap.png")
        a, b = self.convert("snap_a"), self.convert("snap_b", combo_expr=False)
        snapshots.shoot([(a, a), (b, b)], out, size=64, timeout=180)
        with open(out, "rb") as f:
            head = f.read(24)
        self.assertEqual(head[:8], b"\x89PNG\r\n\x1a\n")
        w, h = int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
        n = len(snapshots.SHOTS)
        self.assertEqual(w, 150 + n * (64 + 6) + 6)              # 一段放下标准的一组，两个模型两行
        self.assertEqual(h, 6 + 2 * (64 + 20 + 6) + 6)


class PipelineTest(Base):
    def _pack_files(self):
        src = os.path.dirname(self.pmx)
        return [(os.path.join(d, fn), os.path.join("包", os.path.relpath(os.path.join(d, fn), src)))
                for d, _, fs in os.walk(src) for fn in fs]

    # VRChat 风格的英文骨名（FBX 导出会把非 ASCII 的名字换成下划线）
    _EN = {"センター": "Root", "下半身": "Hips", "上半身": "Spine", "上半身2": "Chest", "首": "Neck", "頭": "Head",
           "左目": "Eye.L", "右目": "Eye.R", "髪1": "Hair1", "髪2": "Hair2", "髪3": "Hair3"}
    for _s, _t in (("左", "L"), ("右", "R")):
        _EN.update({_s + "肩": "Shoulder." + _t, _s + "腕": "Upper_arm." + _t, _s + "ひじ": "Lower_arm." + _t,
                    _s + "手首": "Hand." + _t, _s + "足": "Upper_leg." + _t, _s + "ひざ": "Lower_leg." + _t,
                    _s + "足首": "Foot." + _t, _s + "つま先": "Toe." + _t})

    def _variant_pmx(self, name, outfit=False):
        """synth 模型换成英文骨名；outfit=True 时改成「只有衣服」：头部（头骨和头发）的顶点都改挂到上半身。"""
        m = pmxio.read_pmx(self.pmx)
        names = [b["name"] for b in m["bones"]]
        if outfit:
            head = {names.index("頭")}
            for i, b in enumerate(m["bones"]):
                if b["parent"] in head or names[i].startswith("髪"):
                    head.add(i)
            spine = names.index("上半身")
            for v in m["vertices"]:
                if set(v["wbones"]) & head:
                    v.update(wtype=0, wbones=[spine], wweights=[])
        for b in m["bones"]:
            b["name"] = b["name_en"] = self._EN.get(b["name"], b["name"])
        out = os.path.join(self.tmp, "variant_src", name + ".pmx")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        pmxio.write_pmx(m, out)
        return out

    def _outfit_pmx(self):
        return self._variant_pmx("Outfit", outfit=True)

    def test_not_an_avatar(self):
        self.assertIsNone(pipeline.not_an_avatar(self.pmx))
        self.assertIn("头部", pipeline.not_an_avatar(self._outfit_pmx()))

    def test_fbx_output_is_only_vrm(self):
        """FBX → VRM：中间的 PMX 和贴图放临时目录，输出目录只有 VRM；只转 PMX 时才留 PMX + 贴图。"""
        import fbxout
        fbx = os.path.join(self.tmp, "fbx_src", "体.fbx")
        os.makedirs(os.path.dirname(fbx), exist_ok=True)
        fbxout.export_pmx(self.pmx, fbx, log=lambda *a, **k: None)
        out = os.path.join(self.tmp, "fbx_out")
        outs = pipeline.convert_file(fbx, {}, out_dir=out)
        self.assertEqual([os.path.basename(p) for p in outs], ["体.vrm"])
        self.assertEqual(os.listdir(out), ["体.vrm"])
        out_pmx = os.path.join(self.tmp, "fbx_out_pmx")
        outs = pipeline.convert_file(fbx, {"target": "pmx"}, out_dir=out_pmx)
        self.assertEqual([os.path.basename(p) for p in outs], ["体.pmx"])
        self.assertIn("体.pmx", os.listdir(out_pmx))

    def test_unitypackage_skips_outfit(self):
        """unitypackage：解到临时目录、日志一行汇总；包里只有衣服的 FBX 跳过，输出目录只有角色的 VRM。"""
        import fbxout
        src = os.path.join(self.tmp, "up_src")
        os.makedirs(src, exist_ok=True)
        avatar, outfit = os.path.join(src, "Avatar.fbx"), os.path.join(src, "Outfit.fbx")
        fbxout.export_pmx(self._variant_pmx("Avatar"), avatar, log=lambda *a, **k: None)
        fbxout.export_pmx(self._outfit_pmx(), outfit, log=lambda *a, **k: None)
        pkg = os.path.join(self.tmp, "Avatar.unitypackage")
        with tarfile.open(pkg, "w:gz") as tf:
            for k, (fn, rel) in enumerate(((avatar, "Assets/Model/Avatar.fbx"), (outfit, "Assets/Model/Outfit.fbx"))):
                guid = "%032x" % (k + 1)
                tf.add(fn, guid + "/asset")
                info = tarfile.TarInfo(guid + "/pathname")
                data = rel.encode("utf-8")
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
        logs = []
        out = os.path.join(self.tmp, "up_out")
        outs = pipeline.convert_file(pkg, {}, out_dir=out, log=lambda m, t=None: logs.append((m, t)))
        self.assertEqual([os.path.basename(p) for p in outs], ["Avatar.vrm"])
        self.assertEqual(os.listdir(out), ["Avatar.vrm"])
        self.assertTrue(any("跳过 Outfit" in m and t == "warn" for m, t in logs), logs)
        self.assertTrue(any(m.startswith("解包 Avatar.unitypackage：") for m, _ in logs))
        self.assertFalse(any(" KB " in m for m, _ in logs))             # 不再逐个列文件

    def test_archive_kinds(self):
        for name, kind in (("a.zip", "zip"), ("a.7z", "7z"), ("a.7z.001", "7z"), ("a.7z.002", None),
                           ("a.rar", "rar"), ("a.part1.rar", "rar"), ("a.part02.rar", None),
                           ("a.tar.gz", "tar"), ("a.TGZ", "tar"), ("a.tar.xz", "tar"), ("a.gz", None)):
            self.assertEqual(pipeline.classify(name), kind, name)

    def test_tar_gz(self):
        t = os.path.join(self.tmp, "pack.tar.gz")
        with tarfile.open(t, "w:gz") as tf:
            for p, arc in self._pack_files():
                tf.add(p, arc)
            info = tarfile.TarInfo("../evil.txt")           # 跑到外面的路径：只能落在解压目录里
            tf.addfile(info, None)
        outs = pipeline.convert_file(t, {}, out_dir=os.path.join(self.tmp, "tar_out"))
        self.assertEqual(len(outs), 1)
        self.assertValid(outs[0])
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")))

    def test_nested_archive(self):
        """zip 里只有一个 tar.gz：再解一层。"""
        inner = os.path.join(self.tmp, "inner.tar.gz")
        with tarfile.open(inner, "w:gz") as tf:
            for p, arc in self._pack_files():
                tf.add(p, arc)
        z = os.path.join(self.tmp, "outer.zip")
        with zipfile.ZipFile(z, "w") as zf:
            zf.write(inner, "model.tar.gz")
        outs = pipeline.convert_file(z, {}, out_dir=os.path.join(self.tmp, "nested_out"))
        self.assertEqual([os.path.splitext(os.path.basename(o))[0] for o in outs], ["合成モデル"])

    def test_7z_via_bsdtar(self):
        tar = archive.tools()["bsdtar"]
        if not tar:
            self.skipTest("没有 bsdtar")
        src = os.path.dirname(self.pmx)
        s = os.path.join(self.tmp, "pack.7z")
        r = subprocess.run([tar, "-c", "-f", s, "--format", "7zip", "-C", os.path.dirname(src),
                            os.path.basename(src)], capture_output=True)
        if r.returncode:
            self.skipTest("bsdtar 写不了 7z：%s" % r.stderr[-200:])
        outs = pipeline.convert_file(s, {}, out_dir=os.path.join(self.tmp, "7z_out"))
        self.assertEqual(len(outs), 1)
        self.assertValid(outs[0])

    def test_no_tool_message(self):
        saved = archive._TOOLS
        archive._TOOLS = {"7z": None, "unrar": None, "bandizip": None, "bsdtar": None, "py7zr": False}
        try:
            p = os.path.join(self.tmp, "x.rar")
            with open(p, "wb") as f:
                f.write(b"Rar!" + bytes([0x1A, 0x07, 0x00]))
            with self.assertRaises(RuntimeError) as cm:
                pipeline.convert_file(p, {}, out_dir=os.path.join(self.tmp, "rar_out"))
            self.assertIn("7-Zip", str(cm.exception))
        finally:
            archive._TOOLS = saved

    def test_zip_with_edits(self):
        z = os.path.join(self.tmp, "pack.zip")
        src = os.path.dirname(self.pmx)
        with zipfile.ZipFile(z, "w") as zf:
            for d, _, fs in os.walk(src):
                for fn in fs:
                    p = os.path.join(d, fn)
                    zf.write(p, os.path.join("包", os.path.relpath(p, src)))
        out_dir = os.path.join(self.tmp, "zip_out")
        opts = copy.deepcopy(pipeline.DEFAULTS)
        opts["meta"]["author"] = "someone"
        outs = pipeline.convert_file(z, opts, out_dir=out_dir, edits={"title": "zip 模型"})
        self.assertEqual(len(outs), 1)
        self.assertValid(outs[0])
        m = vrmio.read_glb(outs[0])[0]["extensions"]["VRMC_vrm"]["meta"]
        self.assertEqual((m["name"], m["authors"]), ("zip 模型", ["someone"]))


if __name__ == "__main__":
    unittest.main()
