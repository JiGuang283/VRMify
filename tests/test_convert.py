# -*- coding: utf-8 -*-
"""不依赖真实模型的测试：用 tests/synth.py 合成的 PMX 跑转换、后调整（vrm_edit）、压缩包输入。

    python -m unittest discover -s tests -v

Schema 校验需要 jsonschema（requirements-dev.txt），贴图瘦身需要 Pillow；缺了对应的测试会跳过。
"""
import copy
import io
import os
import random
import shutil
import struct
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
from vrmconv import expressions, mesh, morphbake, textures  # noqa: E402

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
        # 只输出通用 VRM：不加某个程序私有的自定义表情；synth 没有眼睛高光、吐舌，不补
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

    def test_threads_same_output(self):
        """贴图在几个线程里同时处理（读文件、透明统计、瘦身），写出的文件和一张张处理时完全相同。"""
        runs = [{}]
        if textures.available():
            runs.append({"tex_max": 32, "tex_jpeg": True})
        for k, kw in enumerate(runs):
            a, b = self.convert("thr1_%d" % k, threads=1, **kw), self.convert("thr4_%d" % k, threads=4, **kw)
            with open(a, "rb") as fa, open(b, "rb") as fb:
                self.assertEqual(fa.read(), fb.read(), kw)

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


class AlphaStatsTest(unittest.TestCase):
    """贴图透明度统计：装了 Pillow 时走 Pillow（快），结论要和纯 Python 版一致。"""

    @staticmethod
    def png(alpha_of):
        w = h = 64
        rows = [bytes(v for x in range(w) for v in (200, 120, 90, alpha_of(x, y))) for y in range(h)]
        return vrmio.encode_png(w, h, rows)

    @unittest.skipUnless(textures.available(), "需要 Pillow")
    def test_pillow_matches_pure_python(self):
        from unittest import mock
        cases = {"不透明": lambda x, y: 255, "镂空": lambda x, y: 0 if (x // 4 + y // 4) % 2 else 255,
                 "半透明": lambda x, y: 128, "渐变": lambda x, y: x * 4}
        for name, f in cases.items():
            data = self.png(f)
            fast = vrmio.png_alpha_stats(data)
            with mock.patch.object(vrmio, "_png_alpha_stats_pil", return_value=NotImplemented):
                slow = vrmio.png_alpha_stats(data)
            self.assertEqual([round(fast[1] / fast[0], 3), round(fast[2] / fast[0], 3)],
                             [round(slow[1] / slow[0], 3), round(slow[2] / slow[0], 3)], name)
        self.assertIsNone(vrmio.png_alpha_stats(b"not a png"))


class DecodeImageTest(unittest.TestCase):
    """BMP / TGA 转 PNG：装了 Pillow 时走 Pillow，像素要和纯 Python 版一样（32 位 BMP 的第 4 个字节当 alpha）。"""

    @staticmethod
    def bmp32(w, h, top_down):
        px = bytes(v for i in range(w * h) for v in (i % 256, 40, 200, (i * 7) % 256))       # BGRA
        hdr = struct.pack("<IiiHHIIiiII", 40, w, -h if top_down else h, 1, 32, 0, len(px), 0, 0, 0, 0)
        return b"BM" + struct.pack("<IHHI", 14 + len(hdr) + len(px), 0, 0, 14 + len(hdr)) + hdr + px

    @unittest.skipUnless(textures.available(), "需要 Pillow")
    def test_pillow_matches_pure_python(self):
        from PIL import Image
        src = Image.frombytes("RGBA", (13, 7), bytes((x * 19 + y) % 256 for x in range(13 * 7 * 4) for y in (0,)))
        cases = {"bmp32": self.bmp32(13, 7, False), "bmp32 自上而下": self.bmp32(13, 7, True)}
        for name, im, fmt, kw in (("bmp24", src.convert("RGB"), "BMP", {}),
                                  ("tga32", src, "TGA", {}), ("tga24 rle", src.convert("RGB"), "TGA", {"compression": "tga_rle"})):
            buf = io.BytesIO()
            im.save(buf, fmt, **kw)
            cases[name] = buf.getvalue()
        for name, data in cases.items():
            kind = "BMP" if data[:2] == b"BM" else "TGA"
            fast = vrmio._decode_pil(data, kind)
            slow = (vrmio._decode_bmp if kind == "BMP" else vrmio._decode_tga)(data)
            a = Image.open(io.BytesIO(fast)).convert("RGBA")
            b = Image.open(io.BytesIO(slow)).convert("RGBA")
            self.assertEqual(a.tobytes(), b.tobytes(), name)
            self.assertEqual(vrmio.decode_image(data), fast, name)


class PmxReadTest(unittest.TestCase):
    """read_pmx：顶点 / UV 表情的偏移整段批量解，和写进去的一样（float 按 float32 取整）。"""

    def test_morph_offsets(self):
        tmp = tempfile.mkdtemp(prefix="m2v_read_")
        try:
            m = pmxio.read_pmx(synth.build(tmp, eyes=True))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

        def f32(x):
            return struct.unpack("<f", struct.pack("<f", x))[0]
        by = {mo["name"]: mo for mo in m["morphs"]}
        hv = 5 * 8
        self.assertEqual(by["まばたき"]["offsets"], [(hv + k, (0.0, f32(-0.2), 0.0)) for k in range(4)])
        v0 = by["怒マーク"]["offsets"][0][0]
        self.assertEqual(by["怒マーク"]["offsets"], [(v0 + k, (0.5, 0.25, 0.0, 0.0)) for k in range(4)])
        self.assertEqual(by["見開く"]["offsets"], [(0, -0.5), (5, 1.0)])
        self.assertEqual(len(by["寄り目"]["offsets"]), 2)
        self.assertEqual(len(m["rigid_bodies"]), 4)                  # 后面的段没有错位


class LegBonesTest(unittest.TestCase):
    """腿部的 D 骨合并、脚趾选骨、左右权重不一致的检查。"""

    @staticmethod
    def bone(name, parent, pos, grant=None):
        return {"name": name, "parent": parent, "pos": pos,
                "inherit_rot": (grant, 1.0) if grant is not None else None, "inherit_mov": None}

    def leg(self, ankle_d_offset):
        """0 腰 1 足 2 ひざ 3 足首 4 足D 5 ひざD 6 足首D（偏差 ankle_d_offset）7 足先EX 8 つま先 9 両目 10 左目"""
        b = self.bone
        return [b("腰", -1, (0, 10, 0)), b("左足", 0, (1, 10, 0)), b("左ひざ", 1, (1, 6, 0)), b("左足首", 2, (1, 1, 0)),
                b("左足D", 0, (1, 10, 0), 1), b("左ひざD", 4, (1, 6, 0), 2), b("左足首D", 5, (1, 1, ankle_d_offset), 3),
                b("左足先EX", 6, (1, 0.3, -0.8)), b("左つま先", 3, (1, 0.2, -1.2)),
                b("両目", 0, (0, 15, -1)), b("左目", 0, (0.3, 15, -0.2), 9)]

    def test_aliases(self):
        from vrmconv import humanoid
        for off, merged in ((0.0, True), (0.3, True), (1.5, False)):       # 足首 那一节长 5：15% 是 0.75
            bones = self.leg(off)
            alias = humanoid._append_aliases(bones, humanoid._clean_parents(bones), eps=0.01)
            self.assertEqual(alias.get(4), 1)
            self.assertEqual(alias.get(5), 2)
            self.assertEqual(alias.get(6), 3 if merged else None, off)
            self.assertNotIn(10, alias)                                    # humanoid 骨（左目）不并

    def test_toes(self):
        from vrmconv import humanoid
        bones = self.leg(0.3)
        alias = humanoid._append_aliases(bones, humanoid._clean_parents(bones), eps=0.01)
        human = {"leftToes": 8}
        humanoid._pick_toes(bones, human, alias, set())
        self.assertEqual(human["leftToes"], 7)                             # 有 足先EX 用它
        no_ex = [dict(b, name="左足先" if b["name"] == "左足先EX" else b["name"]) for b in bones]
        human = {"leftToes": 8}
        humanoid._pick_toes(no_ex, human, alias, set())
        self.assertEqual(human["leftToes"], 8)                             # つま先 没挂网格：留着
        humanoid._pick_toes(no_ex, human, alias, {8})
        self.assertNotIn("leftToes", human)                                # 挂了网格：不设脚趾

    def test_mirror_mismatch(self):
        human = {"leftUpperLeg": 1, "rightUpperLeg": 2, "leftLowerLeg": 3, "rightLowerLeg": 4}
        verts, weights = [], []
        for k in range(30):                           # 右侧绑大腿、左侧对称位置绑小腿
            verts += [{"pos": (-1.0, 1.0 + k * 0.01, 0.0)}, {"pos": (1.0, 1.0 + k * 0.01, 0.0)}]
            weights += [[(2, 0.8), (4, 0.1)], [(3, 0.8), (1, 0.1)]]
        for k in range(30):                           # 对称的不算
            verts += [{"pos": (-2.0, 1.0 + k * 0.01, 0.0)}, {"pos": (2.0, 1.0 + k * 0.01, 0.0)}]
            weights += [[(4, 1.0)], [(3, 1.0)]]
        self.assertEqual(mesh.mirror_mismatches(verts, weights, weights, human), [(30, 2, "UpperLeg", 3, "LowerLeg")])
        self.assertEqual(mesh.mirror_mismatches(verts[60:], weights[60:], weights[60:], human), [])


class WeightsTest(unittest.TestCase):
    """mesh.vertex_weights：1 / 2 根骨的顶点走快速分支，结果和通用算法完全相同；带付与别名的结果
    可以从不带别名的结果改出来（只重算挂在别名骨上的顶点）。"""

    @staticmethod
    def reference(v, nb, alias):
        """原来的通用算法（所有权重类型都走这里）。"""
        bs = [x for x in v["wbones"] if 0 <= x < nb] or [0]
        wt = v["wtype"]
        if wt == 0:
            wl = [(bs[0], 1.0)]
        elif wt in (1, 3):
            w0 = v["wweights"][0] if v["wweights"] else 1.0
            wl = [(bs[0], w0), (bs[1] if len(bs) > 1 else bs[0], 1.0 - w0)]
        else:
            ws = list(v["wweights"]) + [0.0] * 4
            wl = [(bs[k] if k < len(bs) else bs[0], ws[k]) for k in range(4)]
            if sum(x[1] for x in wl) <= 1e-9:
                wl = [(bs[0], 1.0)]
        acc = {}
        for b, w in wl:
            b = alias.get(b, b)
            acc[b] = acc.get(b, 0.0) + max(0.0, w)
        wl = sorted(acc.items(), key=lambda x: -x[1])[:4]
        tot = sum(w for _, w in wl)
        if tot <= 1e-9:
            return [(wl[0][0] if wl else bs[0], 1.0)]
        return [(b, w / tot) for b, w in wl]

    def test_cases(self):
        def vw(wt, bs, ws, alias=None):
            return mesh.vertex_weights([{"wtype": wt, "wbones": bs, "wweights": ws}], 4, alias or {})[0]
        self.assertEqual(vw(0, [2], []), [(2, 1.0)])
        self.assertEqual(vw(1, [1, 2], [0.25]), [(2, 0.75), (1, 0.25)])
        self.assertEqual(vw(1, [1, 2], [1.0]), [(1, 1.0), (2, 0.0)])         # 零权重也保留
        self.assertEqual(vw(1, [1, 2], [0.5]), [(1, 0.5), (2, 0.5)])         # 一样大时保持原顺序
        self.assertEqual(vw(1, [1, 2], [0.25], {2: 1}), [(1, 1.0)])          # 别名并到同一根骨
        self.assertEqual(vw(1, [9, 2], [0.25]), [(2, 1.0)])                  # 无效骨丢掉
        self.assertEqual(vw(2, [1, 2, 3, 0], [0.5, 0.25, 0.25, 0.0]), [(1, 0.5), (2, 0.25), (3, 0.25), (0, 0.0)])

    def test_random(self):
        rnd = random.Random(7)
        vals = [0.0, -0.0, 1.0, 0.5, 0.3, 0.7, -0.2, 1.2, 1e-12, 0.1, 0.9]
        verts = []
        for _ in range(5000):
            wt = rnd.choice([0, 1, 2, 3, 4])
            n = {0: 0, 1: 1, 2: 4, 3: 1, 4: 4}[wt]
            verts.append({"wtype": wt, "wbones": [rnd.choice([-1, 0, 1, 2, 3, 5, 9]) for _ in range(rnd.randint(0, 4))],
                          "wweights": [rnd.choice(vals) for _ in range(rnd.choice([n, 0]))]})
        base = mesh.vertex_weights(verts, 6, {})
        for alias in ({}, {2: 1}, {3: 0, 5: 2}, {0: 1}, {1: 2, 2: 3}):
            want = [self.reference(v, 6, alias) for v in verts]
            self.assertEqual(mesh.vertex_weights(verts, 6, alias), want, alias)
            self.assertEqual(mesh.vertex_weights(verts, 6, alias, base=base), want, alias)


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
    """VRM 里某个 morph target 的 [(顶点位置, 偏移)]（只要有偏移的顶点；按表情分了几个网格时合起来）。"""
    out = []
    for m in g["meshes"]:
        names = (m.get("extras") or {}).get("targetNames") or []
        if name not in names:
            continue
        t = names.index(name)
        for pr in m["primitives"]:
            pos = vrmio.read_accessor(g, b, pr["attributes"]["POSITION"])
            d = vrmio.read_accessor(g, b, pr["targets"][t]["POSITION"])
            for i in range(0, len(d), 3):
                if abs(d[i]) + abs(d[i + 1]) + abs(d[i + 2]) > 1e-7:
                    out.append((pos[i:i + 3], d[i:i + 3]))
    return out


def bind_names(g, binds, is1=True):
    """表情绑定 → [(morph target 名, 权重)]（一个 target 分在几个网格里时只列一次）。"""
    out = []
    for bd in binds:
        x = (vrm_edit._target_name(g, bd, is1), bd["weight"])
        if x not in out:
            out.append(x)
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
        ex = g["extensions"]["VRMC_vrm"]["expressions"]
        self.assertNotIn("lookLeftRight_depth", ex.get("custom", {}))          # 深度场只是 morph target
        h = vrm_edit.describe(out)["look"]["h"]
        binds = ex["preset"]["lookLeft"]["morphTargetBinds"]
        self.assertEqual([n for n, _ in bind_names(g, binds)], ["lookLeft", "lookLeftRight_depth"])
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


class ExtraExprTest(Base):
    """补充的自定义表情：hideHighlight（关掉眼睛高光）、tongue（吐舌）。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        m = pmxio.read_pmx(synth.build(os.path.join(cls.tmp, "hl"), "高光モデル", eyes=True))
        for mat in m["materials"]:
            if mat["name"] == "目":
                mat["name"] = "目光"                 # 眼珠方块当成高光材质
        head = next(i for i, b in enumerate(m["bones"]) if b["name"] == "頭")
        vs = [i for i, v in enumerate(m["vertices"]) if v["wbones"] == [head]][:4]
        m["morphs"].append({"name": "ぺろっ", "name_en": "tongue", "panel": 3, "kind": 1,
                            "offsets": [(i, (0.0, -0.1, 0.0)) for i in vs]})
        cls.pmx = os.path.join(cls.tmp, "hl", "高光モデル.pmx")
        pmxio.write_pmx(m, cls.pmx)

    def test_generated(self):
        out = self.convert("hl1")
        self.assertValid(out)
        g, b = vrmio.read_glb(out)
        cu = g["extensions"]["VRMC_vrm"]["expressions"]["custom"]
        self.assertIn("hideHighlight", cu)
        self.assertIn("tongue", cu)
        self.assertEqual(len(morph_deltas(g, b, "tongue")), 4)
        # 两个眼珠方块各自收成一点
        hl = morph_deltas(g, b, "hideHighlight")
        self.assertEqual(len(hl), 16)
        ends = {tuple(round(p[k] + d[k], 5) for k in range(3)) for p, d in hl}
        self.assertEqual(len(ends), 2)
        g0 = vrmio.read_glb(self.convert("hl0", spec="0.x"))[0]
        names = [gr["name"] for gr in g0["extensions"]["VRM"]["blendShapeMaster"]["blendShapeGroups"]]
        self.assertIn("hideHighlight", names)
        self.assertIn("tongue", names)
        off = vrmio.read_glb(self.convert("hl_off", extra_expr=False))[0]
        self.assertFalse({"hideHighlight", "tongue"} & set(off["extensions"]["VRMC_vrm"]["expressions"]["custom"]))

    def test_own_morphs_first(self):
        """模型自带关闭高光的表情（这里两组高光各一个）：合成一组用，不生成；材质名不像高光也行。"""
        m = pmxio.read_pmx(self.pmx)
        mi = [i for i, x in enumerate(m["materials"]) if x["name"] == "目光"][0]
        m["materials"][mi]["name"] = "eye"
        zero = [0.0] * 28
        for k, nm in enumerate(("eye hi1 off", "eye hi2 off")):
            m["morphs"].append({"name": nm, "name_en": nm, "panel": 4, "kind": 8, "offsets": [(mi, 0, zero)]})
        m["morphs"].append({"name": "eye hi change", "name_en": "", "panel": 4, "kind": 8, "offsets": [(mi, 0, zero)]})
        logs = []
        added = expressions.add_extras(m, lambda s, t=None: logs.append(s))
        self.assertEqual(added, ["hideHighlight", "tongue"])
        hl = m["morphs"][-2]
        self.assertEqual(hl["kind"], 0)
        self.assertEqual([m["morphs"][i]["name"] for i, _ in hl["offsets"]], ["eye hi1 off", "eye hi2 off"])
        self.assertEqual(expressions._highlight_materials(m), [])
        # 已经有同名表情：不补
        self.assertEqual(expressions.add_extras(m, lambda *a: None), [])

    def test_material_names(self):
        def mats(*names):
            return {"materials": [{"name": n, "faces": 3} for n in names], "textures": []}
        hl = expressions._highlight_materials
        self.assertEqual(hl(mats("目光", "ハイライト", "MI_Eye hi", "eye hi1", "MI_Eye1+hi2", "eye+hi")), [0, 1, 2, 3, 4, 5])
        self.assertEqual(hl(mats("髪高光", "目", "白目", "hip", "Chihiro", "hi")), [])


class SplitMeshTest(Base):
    """按表情分网格：和全放在一个网格里比，每个材质上的顶点、每个表情的偏移、表情绑定都一样。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pmx = synth.build(os.path.join(cls.tmp, "eyes"), "眼モデル", eyes=True)

    def convert_with(self, name, cost, **kw):
        old = mesh.MESH_COST
        mesh.MESH_COST = cost
        try:
            return self.convert(name, **kw)
        finally:
            mesh.MESH_COST = old

    @staticmethod
    def by_material(g, b):
        """材质号 → (顶点属性, {morph target 名: 偏移})，偏移全零的不列。"""
        out = {}
        for m in g["meshes"]:
            names = (m.get("extras") or {}).get("targetNames") or []
            for pr in m["primitives"]:
                attrs = {k: vrmio.read_accessor(g, b, a) for k, a in pr["attributes"].items()}
                attrs["indices"] = vrmio.read_accessor(g, b, pr["indices"])
                self_t = {}
                for t, n in zip(pr.get("targets", []), names):
                    d = list(vrmio.read_accessor(g, b, t["POSITION"]))
                    if any(d):
                        self_t[n] = d
                out[pr["material"]] = (attrs, self_t)
        return out

    def check_same(self, one, split, is1):
        self.assertValid(split)
        ga, ba = vrmio.read_glb(one)
        gb, bb = vrmio.read_glb(split)
        self.assertEqual(len(ga["meshes"]), 1)
        self.assertGreater(len(gb["meshes"]), 1)
        self.assertEqual(self.by_material(ga, ba), self.by_material(gb, bb))
        for m in gb["meshes"]:                        # 每个网格里的 primitive 带同样多的 target
            n = len((m.get("extras") or {}).get("targetNames") or [])
            self.assertEqual({len(pr.get("targets", [])) for pr in m["primitives"]}, {n})
        if is1:
            ea, eb = (g["extensions"]["VRMC_vrm"]["expressions"] for g in (ga, gb))
            for grp in ("preset", "custom"):
                self.assertEqual(set(ea[grp]), set(eb[grp]))
                for k, x in ea[grp].items():
                    y = eb[grp][k]
                    self.assertEqual(sorted(bind_names(ga, x.get("morphTargetBinds", []))),
                                     sorted(bind_names(gb, y.get("morphTargetBinds", []))), k)
            nodes = [n["mesh"] for n in gb["nodes"] if "mesh" in n]
            self.assertEqual(sorted(nodes), list(range(len(gb["meshes"]))))
            fp = gb["extensions"]["VRMC_vrm"]["firstPerson"]["meshAnnotations"]
            self.assertEqual(len(fp), len(gb["meshes"]))
        else:
            ea, eb = (g["extensions"]["VRM"]["blendShapeMaster"]["blendShapeGroups"] for g in (ga, gb))
            self.assertEqual([x["name"] for x in ea], [x["name"] for x in eb])
            for x, y in zip(ea, eb):
                self.assertEqual(sorted(bind_names(ga, x["binds"], False)), sorted(bind_names(gb, y["binds"], False)))
            self.assertEqual(len(gb["extensions"]["VRM"]["firstPerson"]["meshAnnotations"]), len(gb["meshes"]))
        return gb

    def test_vrm1(self):
        g = self.check_same(self.convert_with("one1", 10 ** 12), self.convert_with("split1", 0), True)
        # 没有表情动到的 primitive 不带 morph target
        self.assertTrue(any("extras" not in m for m in g["meshes"]))

    def test_vrm0(self):
        self.check_same(self.convert_with("one0", 10 ** 12, spec="0.x"),
                        self.convert_with("split0", 0, spec="0.x"), False)

    def test_tune_look_split(self):
        """视线表情的横向场 / 深度场分在几个网格里时，调整视线角度照样按名字改权重。"""
        out = self.convert_with("split_tune", 0)
        vrm_edit.apply(out, {"lookAt": {"h": 30}})
        self.assertValid(out)
        g = vrmio.read_glb(out)[0]
        wt, wd = morphbake.gaze_weights(30, "h")
        binds = g["extensions"]["VRMC_vrm"]["expressions"]["preset"]["lookLeft"]["morphTargetBinds"]
        self.assertEqual(sorted(bind_names(g, binds)), sorted([("lookLeft", wt), ("lookLeftRight_depth", wd)]))


class GroupMeshesTest(unittest.TestCase):
    def test_group(self):
        def pr(v, ts):
            return {"vcount": v, "tmap": dict.fromkeys(ts, 0)}
        prims = [pr(1000, (0, 1, 2)), pr(1000, (0, 1, 2)), pr(50000, ()), pr(50000, (5,)), pr(10, (3,))]
        # 同样的 target 放一起；小块并进表情多的；大块之间只差一个 target 也并（多一个网格不划算）；
        # 哪儿都没动到的 target 4 放进 target 最多的网格
        self.assertEqual(mesh.group_meshes(prims, 6), [([0, 1, 4], [0, 1, 2, 3, 4]), ([2, 3], [5])])
        old = mesh.MESH_COST
        mesh.MESH_COST = 0
        try:
            self.assertEqual(mesh.group_meshes(prims, 6),
                             [([0, 1], [0, 1, 2, 4]), ([2], []), ([3], [5]), ([4], [3])])
        finally:
            mesh.MESH_COST = old
        self.assertEqual(mesh.group_meshes([pr(5, ())], 0), [([0], [])])


class GlbWriteTest(unittest.TestCase):
    def test_roundtrip(self):
        d = tempfile.mkdtemp(prefix="m2v_glb_")
        try:
            for data in (bytearray(b"abcde"), memoryview(b"abcdefgh"), b""):
                p = os.path.join(d, "x.glb")
                size = vrmio.write_glb({"asset": {"version": "2.0"}}, data, p)
                self.assertEqual(size, os.path.getsize(p))
                g, b = vrmio.read_glb(p)
                self.assertEqual(g, {"asset": {"version": "2.0"}})
                self.assertEqual(bytes(b[:len(data)]), bytes(data))
                self.assertEqual(len(b) % 4, 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class FormatOptsTest(unittest.TestCase):
    def test_morphs_follow_vrm_switch(self):
        """转 VRM 时格式自己的「导出表情」跟着 VRM 的「表情」开关（界面里只显示那一个）；只转 PMX 时用格式自己的。"""
        opts = pipeline.merged({"vrm": {"morphs": False}, "fbx": {"morphs": True}, "psk": {"morphs": True}})
        self.assertFalse(pipeline._fmt_opts(opts, "fbx")["morphs"])
        self.assertFalse(pipeline._fmt_opts(opts, "psk")["morphs"])
        self.assertTrue(opts["fbx"]["morphs"])                       # 存着的设置不变
        opts["target"] = "pmx"
        self.assertTrue(pipeline._fmt_opts(opts, "fbx")["morphs"])
        self.assertEqual(pipeline._fmt_opts(opts, "uemodel"), opts["uemodel"])


class JobsBudgetTest(unittest.TestCase):
    """同时转几个：按每个任务的内存估计，最大的几个加起来不超过可用内存的 MEM_SHARE。"""

    def jobs(self, n, mem=None, free=4.0, cpu=8):
        from unittest import mock
        par = pipeline.parallel
        with mock.patch.object(par, "free_memory_gb", return_value=free),                 mock.patch.object(par, "cores", return_value=cpu),                 mock.patch.dict(os.environ, {par.WORKER_ENV: ""}):
            return par.jobs_for(n, 0, mem)

    def test_jobs(self):
        self.assertEqual(self.jobs(4), 2)                                   # 不知道多大：每个 1.5 GB
        self.assertEqual(self.jobs(4, [0.5] * 4), 4)
        self.assertEqual(self.jobs(4, [1.0] * 4), 3)
        self.assertEqual(self.jobs(4, [0.25, 1.5, 0.25, 1.5]), 2)           # 两个大的可能同时在跑
        self.assertEqual(self.jobs(4, [1.5] * 4, free=1.0), 1)
        self.assertEqual(self.jobs(4, [0.2] * 4, cpu=3), 3)                # 物理核数
        self.assertEqual(self.jobs(20, [0.2] * 20, free=30, cpu=16), 16)    # 不设上限，只看核数和内存
        self.assertEqual(self.jobs(20, [1.0] * 20, free=10, cpu=16), 8)
        self.assertEqual(self.jobs(1, [0.2]), 1)

    def test_estimate(self):
        d = tempfile.mkdtemp(prefix="m2v_mem_")
        try:
            pmx = os.path.join(d, "a.pmx")
            with open(pmx, "wb") as f:
                f.write(b"\0" * (4 << 20))
            self.assertAlmostEqual(pipeline.memory_estimate(pmx), 0.15 + 4 * 35 / 1024)
            z = os.path.join(d, "a.zip")
            with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("m/body.pmx", b"\0" * (16 << 20))                 # 按解压后的大小估
                zf.writestr("m/prop.pmx", b"\0" * 1000)
                zf.writestr("m/tex.png", b"\0" * (64 << 20))
            self.assertAlmostEqual(pipeline.memory_estimate(z), 0.15 + 16 * 35 / 1024)
            with zipfile.ZipFile(z, "w") as zf:
                zf.writestr("m/a.unitypackage", b"x")
            self.assertEqual(pipeline.memory_estimate(z), pipeline.UNKNOWN_GB)
            self.assertEqual(pipeline.memory_estimate(os.path.join(d, "x.7z")), pipeline.UNKNOWN_GB)
            big = os.path.join(d, "big.fbx")
            with open(big, "wb") as f:
                f.truncate(200 << 20)
            self.assertEqual(pipeline.memory_estimate(big), pipeline.parallel.GB_PER_JOB)
        finally:
            shutil.rmtree(d, ignore_errors=True)


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
        return bind_names(g, obj["morphTargetBinds"])

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
        pre = g["extensions"]["VRMC_vrm"]["expressions"]["preset"]

        def binds(p):
            return bind_names(g, pre[p]["morphTargetBinds"])
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
        self.assertIn("未找到：", rep[0])


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


class PruneTest(Base):
    """不起作用的骨（IK、末端骨）去掉；collapse_bones 时没有权重的中间骨（全ての親 / センター）也去掉。"""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        m = pmxio.read_pmx(cls.pmx)
        B = m["bones"]
        idx = {b["name"]: i for i, b in enumerate(B)}
        for v in m["vertices"]:                       # センター 不带顶点
            v["wbones"] = [idx["下半身"] if b == idx["センター"] else b for b in v["wbones"]]
        B.append({"name": "全ての親", "name_en": "", "pos": (0, 0, 0), "parent": -1, "layer": 0,
                  "flag": 0x001F, "tail": -1})
        B[idx["センター"]]["parent"] = len(B) - 1
        B.append({"name": "左足ＩＫ", "name_en": "", "pos": (0.9, 1.2, 0), "parent": len(B) - 1, "layer": 0,
                  "flag": 0x001F, "tail": -1})
        B.append({"name": "左手先", "name_en": "", "pos": (5.5, 12, 0), "parent": idx["左手首"], "layer": 0,
                  "flag": 0x0001 | 0x0002 | 0x0008 | 0x0010, "tail": -1})
        cls.pmx = os.path.join(cls.tmp, "src", "prune.pmx")
        pmxio.write_pmx(m, cls.pmx)

    def bone_weights(self, g, b):
        """骨名 → 挂在上面的权重之和（和节点序号无关，比较两种输出用）。"""
        J, out = g["skins"][0]["joints"], {}
        for m in g["meshes"]:
            for pr in m["primitives"]:
                jn = vrmio.read_accessor(g, b, pr["attributes"]["JOINTS_0"])
                wt = vrmio.read_accessor(g, b, pr["attributes"]["WEIGHTS_0"])
                for j, w in zip(jn, wt):
                    if w > 0:
                        nm = g["nodes"][J[j]]["name"]
                        out[nm] = round(out.get(nm, 0) + w, 4)
        return out

    def test_prune(self):
        out = self.convert("prune", collapse_bones=False)
        self.assertValid(out)
        g, b = vrmio.read_glb(out)
        names = [n["name"] for n in g["nodes"]]
        self.assertNotIn("左足ＩＫ", names)
        self.assertNotIn("左手先", names)
        self.assertIn("全ての親", names)                # センター → 下半身 之间的祖先留着
        self.assertIn("センター", names)
        J = g["skins"][0]["joints"]
        self.assertEqual(len(J), len(set(J)))
        out2 = self.convert("collapse", collapse_bones=True)
        self.assertValid(out2)
        g2, b2 = vrmio.read_glb(out2)
        names2 = [n["name"] for n in g2["nodes"]]
        self.assertFalse({"全ての親", "センター", "左足ＩＫ"} & set(names2))
        hips = g2["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["hips"]["node"]
        root = g2["scenes"][0]["nodes"][0]
        self.assertIn(hips, g2["nodes"][root]["children"])
        self.assertEqual(self.bone_weights(g, b), self.bone_weights(g2, b2))
        # 节点世界坐标不变
        w1 = {g["nodes"][j]["name"]: SkeletonTest.world(self, g, j) for j in J}
        for j in g2["skins"][0]["joints"]:
            nm = g2["nodes"][j]["name"]
            for a, c in zip(SkeletonTest.world(self, g2, j), w1[nm]):
                self.assertAlmostEqual(a, c, places=5)


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
        # 浏览器连同子进程（GPU、渲染进程）都结束了，临时配置目录也删了
        self.assertEqual(snapshots.browser_procs(), [])
        self.assertFalse([d for d in os.listdir(tempfile.gettempdir()) if d.startswith(snapshots.PROFILE_PREFIX)])

    def test_cleanup_stale(self):
        """遗留的截图浏览器（配置目录里记的进程已经不在了）连同配置目录一起清掉；正在跑的不动。"""
        import snapshots
        tmp = tempfile.mkdtemp(prefix="m2v_snaptmp_")
        sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
        owner = subprocess.Popen(sleeper)                       # 「正在跑的截图」的主进程
        gone = subprocess.Popen([sys.executable, "-c", "pass"])
        gone.wait()                                              # 已经退出的主进程
        procs = []
        try:
            for name, pid in (("stale", gone.pid), ("live", owner.pid)):
                d = os.path.join(tmp, snapshots.PROFILE_PREFIX + name)
                os.makedirs(d)
                with open(os.path.join(d, snapshots.OWNER_FILE), "w") as f:
                    f.write(str(pid))
                # 冒充浏览器：命令行里带配置目录
                procs.append(subprocess.Popen(sleeper + ["--user-data-dir=" + d]))
            snapshots.cleanup_stale(tmp)
            self.assertIsNotNone(procs[0].wait(30))                       # 遗留的结束了
            self.assertIsNone(procs[1].poll())                            # 正在跑的还在
            self.assertEqual(os.listdir(tmp), [snapshots.PROFILE_PREFIX + "live"])
        finally:
            for p in procs + [owner]:
                p.kill()
                p.wait()
            shutil.rmtree(tmp, ignore_errors=True)


class PipelineTest(Base):
    def _pack_files(self):
        src = os.path.dirname(self.pmx)
        return [(os.path.join(d, fn), os.path.join("包", os.path.relpath(os.path.join(d, fn), src)))
                for d, _, fs in os.walk(src) for fn in fs]

    def _variant_pmx(self, name, outfit=False):
        """synth 模型换成英文骨名（synth.EN，导出 FBX 后还认得出人形骨架）；outfit=True 时改成「只有衣服」：
        头部（头骨和头发）的顶点都改挂到上半身。"""
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
            b["name"] = b["name_en"] = synth.EN.get(b["name"], b["name"])
        out = os.path.join(self.tmp, "variant_src", name + ".pmx")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        pmxio.write_pmx(m, out)
        return out

    def _outfit_pmx(self):
        return self._variant_pmx("Outfit", outfit=True)

    def _prop_pmx(self, folder):
        """道具：一根骨、一个方块（剑、椅子这类）。"""
        m = pmxio.new_model("剑")
        m["bones"].append({"name": "センター", "name_en": "center", "pos": (0, 0, 0), "parent": -1,
                           "layer": 0, "flag": 0x001F, "tail": -1})
        m["faces"] = synth._box(m, (0, 1, 0), 0.5, 0)
        m["materials"] = [synth._material("剣", -1, len(m["faces"]))]
        os.makedirs(folder, exist_ok=True)
        out = os.path.join(folder, "剑.pmx")
        pmxio.write_pmx(m, out)
        return out

    def test_not_an_avatar(self):
        self.assertIsNone(pipeline.not_an_avatar(self.pmx))
        self.assertIn("头部", pipeline.not_an_avatar(self._outfit_pmx()))
        self.assertIn("道具", pipeline.not_an_avatar(self._prop_pmx(os.path.join(self.tmp, "prop_a"))))

    def test_avatar_only(self):
        """「只转换人物」（默认开）：道具跳过（只有它的文件返回 []），压缩包里只转人物；关掉就都转。"""
        prop = self._prop_pmx(os.path.join(self.tmp, "prop_b"))
        logs = []
        out = os.path.join(self.tmp, "prop_out")
        self.assertEqual(pipeline.convert_file(prop, {}, out_dir=out, log=lambda m, t=None: logs.append((m, t))), [])
        self.assertTrue(any(m.startswith("跳过 剑：未识别到人形骨架") and t == "warn" for m, t in logs), logs)
        self.assertEqual(len(pipeline.convert_file(prop, {"vrm": {"avatar_only": False}}, out_dir=out)), 1)
        z = os.path.join(self.tmp, "with_prop.zip")
        with zipfile.ZipFile(z, "w") as zf:
            for p, arc in self._pack_files():
                zf.write(p, arc)
            zf.write(prop, "包/剑.pmx")
        stem = os.path.splitext(os.path.basename(self.pmx))[0]
        outs = pipeline.convert_file(z, {}, out_dir=os.path.join(self.tmp, "with_prop_a"))
        self.assertEqual([os.path.basename(p) for p in outs], [stem + ".vrm"])
        outs = pipeline.convert_file(z, {"vrm": {"avatar_only": False}}, out_dir=os.path.join(self.tmp, "with_prop_b"))
        self.assertEqual(sorted(os.path.basename(p) for p in outs), sorted([stem + ".vrm", "剑.vrm"]))

    @unittest.skipUnless(textures.available(), "需要 Pillow")
    def test_fbx_texture_stats_reused(self):
        """FBX → VRM：判断透明时顺便算好的贴图统计交给转 VRM 那一步，贴图不再解第二遍；结果和再解一遍完全相同。"""
        import fbx2pmx
        import fbxout
        from unittest import mock
        src = os.path.join(self.tmp, "stats_src")
        shutil.copytree(os.path.join(os.path.dirname(self.pmx), "tex"), os.path.join(src, "tex"))
        fbx = os.path.join(src, "体.fbx")
        fbxout.export_pmx(self._variant_pmx("Body"), fbx, log=lambda *a, **k: None)
        with mock.patch.object(vrmio, "png_alpha_stats", wraps=vrmio.png_alpha_stats) as stats:
            a = pipeline.convert_file(fbx, {}, out_dir=os.path.join(self.tmp, "stats_a"))[0]
        self.assertEqual(len(vrmio.read_glb(a)[0]["images"]), 2)
        self.assertEqual(stats.call_count, 0)
        load = fbx2pmx._load_alpha
        with mock.patch.object(fbx2pmx, "_load_alpha", lambda p: (load(p)[0], NotImplemented)):
            b = pipeline.convert_file(fbx, {}, out_dir=os.path.join(self.tmp, "stats_b"))[0]
        with open(a, "rb") as fa, open(b, "rb") as fb:
            self.assertEqual(fa.read(), fb.read())

    def test_fbx_output_is_only_vrm(self):
        """FBX → VRM：中间的 PMX 和贴图放临时目录，输出目录只有 VRM；只转 PMX 时才留 PMX + 贴图。"""
        import fbxout
        fbx = os.path.join(self.tmp, "fbx_src", "体.fbx")
        os.makedirs(os.path.dirname(fbx), exist_ok=True)
        fbxout.export_pmx(self._variant_pmx("Body"), fbx, log=lambda *a, **k: None)
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

    def test_archive_models_in_parallel(self):
        """压缩包里有几个模型：同时转（子进程）；不同文件夹下的同名模型输出名加 _2，不会互相覆盖。"""
        from unittest import mock
        z = os.path.join(self.tmp, "two.zip")
        with zipfile.ZipFile(z, "w") as zf:
            for p, arc in self._pack_files():
                zf.write(p, arc.replace("包", "红", 1))
                zf.write(p, arc.replace("包", "蓝", 1))
        logs = []
        with mock.patch.object(pipeline, "SMALL_MODEL", 0):         # 合成模型很小，照样算「值得并行」
            outs = pipeline.convert_file(z, {"jobs": 2}, out_dir=os.path.join(self.tmp, "two_out"),
                                         log=lambda m, t=None: logs.append(m))
        stem = os.path.splitext(os.path.basename(self.pmx))[0]
        self.assertEqual(sorted(os.path.basename(p) for p in outs), sorted([stem + ".vrm", stem + "_2.vrm"]))
        for p in outs:
            self.assertValid(p)
        self.assertIn("压缩包中共有 2 个模型，同时转换 2 个", logs)

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
