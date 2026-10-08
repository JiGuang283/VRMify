# -*- coding: utf-8 -*-
"""不依赖真实模型的测试：用 tests/synth.py 合成的 PMX 跑转换、后调整（vrm_edit）、压缩包输入。

    python -m unittest discover -s tests -v

Schema 校验需要 jsonschema（requirements-dev.txt），贴图瘦身需要 Pillow；缺了对应的测试会跳过。
"""
import copy
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
from vrmconv import textures  # noqa: E402

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
        # 脸色（Dream Select Studio 的 faceRed / faceBlue）：只给脸部皮肤材质（口型「あ」动到的）
        # 的颜色、阴影色乘色调，不带形态键（游戏只读材质颜色绑定）
        for alias, chan in (("faceRed", 0), ("faceBlue", 2)):
            obj = ex["custom"][alias]
            self.assertNotIn("morphTargetBinds", obj)
            self.assertEqual([b["type"] for b in obj["materialColorBinds"]], ["color", "shadeColor"])
            for b in obj["materialColorBinds"]:
                self.assertEqual(b["targetValue"][chan], 1.0)       # 红 / 蓝通道不降
                self.assertEqual(b["targetValue"][3], 1.0)          # alpha 不动
                self.assertEqual(g["materials"][b["material"]]["name"], "体")

    def test_vrm0_lowercase_presets(self):
        out = self.convert("v0", spec="0.x")
        self.assertValid(out)
        g, _ = vrmio.read_glb(out)
        groups = g["extensions"]["VRM"]["blendShapeMaster"]["blendShapeGroups"]
        presets = [gr["presetName"] for gr in groups]
        self.assertTrue(all(p == p.lower() for p in presets), presets)
        self.assertEqual(presets.count("joy"), 1)          # にこり 和 笑い 只能有一个是 joy
        self.assertIn("blink", presets)
        self.assertNotIn("faceRed", [gr["name"] for gr in groups])    # 脸色只做 1.0

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
                                ("∧", 3, (4, 5)), ("喜び", 2, (2, 3)), ("上", 1, (6,)), ("びっくり", 2, (7,)),
                                ("ぺろっ", 3, (5,))):
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
        self.assertEqual(self.binds(g, ex["preset"]["angry"]), [("怒り", 1.0), ("ｷﾘｯ", 0.7), ("∧", 0.8)])
        self.assertEqual(ex["preset"]["angry"]["extras"]["source"], "怒り+ｷﾘｯ+∧")
        self.assertIn("怒り", ex["custom"])                        # 原来单个对应的还在
        self.assertNotIn("overrideBlink", ex["preset"]["angry"])    # 生气时照样眨眼
        self.assertEqual(ex["preset"]["happy"]["overrideBlink"], "blend")   # 笑眼不再叠加眨眼
        # synth 的「にこり」「笑い」在口面板，不当眉毛 / 眼睛：眉毛用「上」、眼睛用「喜び」
        self.assertEqual(self.binds(g, ex["preset"]["happy"]), [("上", 0.3), ("喜び", 1.0)])
        self.assertIn("にこり", ex["custom"])
        self.assertEqual(ex["custom"]["tongue"], ex["custom"]["ぺろっ"])     # 按名字找的别名
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
        self.assertEqual([b["weight"] for b in by["怒り+ｷﾘｯ+∧"]["binds"]], [100, 70, 80])
        self.assertEqual(by["怒り"]["presetName"], "unknown")
        # 0.x 没有 surprised 预设：和 VRoid 一样是 unknown + 名字 Surprised
        self.assertEqual(by["Surprised"]["presetName"], "unknown")
        self.assertEqual([b["weight"] for b in by["Surprised"]["binds"]], [100, 100])
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


class PipelineTest(Base):
    def _pack_files(self):
        src = os.path.dirname(self.pmx)
        return [(os.path.join(d, fn), os.path.join("包", os.path.relpath(os.path.join(d, fn), src)))
                for d, _, fs in os.walk(src) for fn in fs]

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
