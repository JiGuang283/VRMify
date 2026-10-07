# -*- coding: utf-8 -*-
"""不依赖真实模型的测试：用 tests/synth.py 合成的 PMX 跑转换、后调整（vrm_edit）、zip 输入。

    python -m unittest discover -s tests -v

Schema 校验需要 jsonschema（requirements-dev.txt），贴图瘦身需要 Pillow；缺了对应的测试会跳过。
"""
import copy
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (HERE, os.path.join(ROOT, "convert"), os.path.join(ROOT, "formats"), os.path.join(ROOT, "tools", "qa")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import check_vrm  # noqa: E402
import pipeline  # noqa: E402
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


class PipelineTest(Base):
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
