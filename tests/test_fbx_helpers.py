# -*- coding: utf-8 -*-
"""FBX / Unity 资源相关的小函数：英文骨名、英文表情名、Unity 材质找贴图、欧拉角顺序、替换网格。

不需要真实模型（Unity 酱、Query 酱实测中发现的问题都在这里留了用例）。
"""
import math
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (os.path.join(ROOT, "convert"), os.path.join(ROOT, "formats")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
import fbx2pmx  # noqa: E402
import unityasset  # noqa: E402
from vrmconv.expressions import _preset_en  # noqa: E402
from vrmconv.humanoid import _en_bone, _map_humanoid  # noqa: E402


class BoneNameTest(unittest.TestCase):
    def test_conventions(self):
        cases = {
            "Character1_Hips": ("slot", "hips"), "Character1_Spine1": ("slot", "chest"),
            "Character1_LeftUpLeg": ("slot", "leftUpperLeg"), "Character1_LeftLeg": ("slot", "leftLowerLeg"),
            "Chr_RightForeArm": ("slot", "rightLowerArm"), "mixamorig:LeftHand": ("slot", "leftHand"),
            "J_Bip_C_UpperChest": ("slot", "upperChest"), "J_Bip_L_UpperArm": ("slot", "leftUpperArm"),
            "thigh.L": ("slot", "leftUpperLeg"), "Bip001 R Calf": ("slot", "rightLowerLeg"),
            "Bip001 Pelvis": ("slot", "hips"), "Eye_R": ("slot", "rightEye"),
            "Character1_LeftHandThumb1": ("finger", ("left", "Thumb", 0)),
            "Character1_RightHandIndex3": ("finger", ("right", "Index", 3)),
        }
        for name, want in cases.items():
            self.assertEqual(_en_bone(name), want, name)

    def test_not_humanoid(self):
        for name in ("J_L_HairTail_00", "skirt_L_01", "Pai_L", "J_Mune_root_00", "Root", "H02_L",
                     "Character1_RightHandPinky4", "J_L_Sode_A00"):
            self.assertIsNone(_en_bone(name), name)

    def test_leg_without_upleg(self):
        # 只有 Leg（没有 UpLeg / Thigh）时 Leg 是大腿
        names = ["Hips", "Spine", "Leg_L", "Knee_L", "Foot_L"]
        bones = [{"name": n, "name_en": n} for n in names]
        human = _map_humanoid(bones, True, {})
        self.assertEqual(human.get("leftUpperLeg"), 2)
        self.assertEqual(human.get("leftLowerLeg"), 3)

    def test_mmd_names_win(self):
        bones = [{"name": "下半身", "name_en": ""}, {"name": "Hips", "name_en": ""}]
        self.assertEqual(_map_humanoid(bones, True, {})["hips"], 0)


class ExpressionNameTest(unittest.TestCase):
    def test_presets(self):
        cases = {"MTH_A": "aa", "Fcl_MTH_E": "ee", "vrc.v_oh": "oh", "EYE_DEF_C": "blink",
                 "Fcl_EYE_Close_L": "blinkLeft", "Blink_R": "blinkRight", "Fcl_ALL_Joy": "happy",
                 "Head_02_MouthOpen": "aa", "happy": "happy"}
        for name, want in cases.items():
            self.assertEqual(_preset_en(name, True), want, name)
        self.assertEqual(_preset_en("Fcl_ALL_Joy", False), "joy")
        self.assertEqual(_preset_en("vrc.blink_left", False), "blink_l")

    def test_not_presets(self):
        for name in ("MTH_SMILE1", "Fcl_BRW_Angry", "Key 1", "eye_a", "Basis", "EYE_SMILE1"):
            self.assertIsNone(_preset_en(name, True), name)


MAT_U5 = """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!21 &2100000
Material:
  m_Name: mat_Body
  m_SavedProperties:
    m_TexEnvs:
    - _BumpMap:
        m_Texture: {fileID: 2800000, guid: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, type: 3}
    - _MainTex:
        m_Texture: {fileID: 2800000, guid: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, type: 3}
    m_Floats: []
"""
MAT_U4 = """%YAML 1.1
--- !u!21 &2100000
Material:
  m_Name: mat_Hair
  m_SavedProperties:
    m_TexEnvs:
      data:
        first:
          name: _MainTex
        second:
          m_Texture: {fileID: 0}
      data:
        first:
          name: _Main_Texture
        second:
          m_Texture: {fileID: 2800000, guid: cccccccccccccccccccccccccccccccc, type: 3}
    m_Floats:
      data:
        first:
          name: _Cutoff
"""


def _write(path, data):
    with open(path, "wb" if isinstance(data, bytes) else "w", **({} if isinstance(data, bytes) else {"encoding": "utf-8"})) as f:
        f.write(data)


class UnityAssetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="m2v_unity_")
        a = os.path.join(self.tmp, "Assets")
        for sub in ("Chara/Models", "Chara/Materials", "Chara/Textures", "Other/Materials"):
            os.makedirs(os.path.join(a, sub))

        def tex(name, guid):
            p = os.path.join(a, "Chara/Textures", name)
            open(p, "wb").write(b"x")
            open(p + ".meta", "w").write("fileFormatVersion: 2\nguid: %s\n" % guid)
        tex("tex_body.tga", "a" * 32)
        tex("tex_body_n.tga", "b" * 32)
        tex("tex_hair.png", "c" * 32)
        tex("tex_other.png", "d" * 32)
        _write(os.path.join(a, "Chara/Materials/mat_Body.mat"), MAT_U5)
        _write(os.path.join(a, "Chara/Materials/mat_Hair.mat"), MAT_U4)
        # 别处的同名材质：不在 FBX 的上级链上，不该被选中
        _write(os.path.join(a, "Other/Materials/mat_Body.mat"), MAT_U5.replace("a" * 32, "d" * 32))
        self.models = os.path.join(a, "Chara/Models")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lookup(self):
        m = unityasset.material_textures(self.models)
        self.assertEqual(os.path.basename(m["mat_Body"]), "tex_body.tga")     # _MainTex，不是法线
        self.assertEqual(os.path.basename(m["mat_Hair"]), "tex_hair.png")     # Unity 4 格式、空的 _MainTex 跳过

    def test_root(self):
        self.assertEqual(os.path.basename(unityasset.find_root(self.models)), "Assets")


class FbxMathTest(unittest.TestCase):
    def test_rotation_order(self):
        # 默认（XYZ）和以前一样：Rz·Ry·Rx
        r = (30.0, 40.0, 50.0)
        m_x = fbx2pmx.m_trs([0, 0, 0], (r[0], 0, 0), [1, 1, 1])
        m_y = fbx2pmx.m_trs([0, 0, 0], (0, r[1], 0), [1, 1, 1])
        m_z = fbx2pmx.m_trs([0, 0, 0], (0, 0, r[2]), [1, 1, 1])
        want_xyz = fbx2pmx.m_mul(m_z, fbx2pmx.m_mul(m_y, m_x))
        want_zxy = fbx2pmx.m_mul(m_y, fbx2pmx.m_mul(m_x, m_z))      # 4 = ZXY：先 Z 再 X 再 Y
        for got, want in ((fbx2pmx.m_rot(r), want_xyz), (fbx2pmx.m_rot(r, 4), want_zxy)):
            for a, b in zip(got, want):
                self.assertAlmostEqual(a, b, places=9)

    def test_pivot_is_translation(self):
        class N:
            def get(self, k):
                return None
        sc = fbx2pmx.Scene.__new__(fbx2pmx.Scene)
        sc._props70 = lambda node: {"Lcl Rotation": [0.0, 0.0, 90.0], "RotationPivot": [1.0, 0.0, 0.0]}
        m = fbx2pmx.Scene._local_matrix(sc, N())
        # 绕 (1,0,0) 转 90°：原点 → (1,-1,0)
        p = fbx2pmx.m_point(m, (0.0, 0.0, 0.0))
        self.assertAlmostEqual(p[0], 1.0, places=6)
        self.assertAlmostEqual(p[1], -1.0, places=6)
        self.assertTrue(math.isfinite(p[2]))


class AlternateMeshTest(unittest.TestCase):
    def rec(self, name, nv=100, lo=(0, 0, 0), hi=(1, 1, 1)):
        return {"name": name, "nv": nv, "nt": nv * 2, "lo": list(lo), "hi": list(hi), "segs": [len(name)]}

    def test_groups(self):
        recs = [self.rec("Body", 500), self.rec("Finger_Pa"), self.rec("Finger_Nomal"), self.rec("Finger_Gu"),
                self.rec("Head_01_Normal", 300, (0, 2, 0), (1, 3, 1)),
                self.rec("Head_02_MouthOpen", 300, (0, 2, 0), (1, 3, 1)),
                self.rec("Glove_L", 100, (5, 0, 0), (6, 1, 1))]          # 同样的顶点数，但位置不同
        groups = fbx2pmx._alternate_groups(recs)
        got = {p["name"]: sorted(a["name"] for a in alts) for p, alts in groups}
        self.assertEqual(got, {"Finger_Nomal": ["Finger_Gu", "Finger_Pa"],
                               "Head_01_Normal": ["Head_02_MouthOpen"]})


if __name__ == "__main__":
    unittest.main()
