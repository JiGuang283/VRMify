# -*- coding: utf-8 -*-
"""VRM 文件静态检查：结构、SpringBone、官方 JSON Schema、导入器会踩的坑。

    python tools/qa/check_vrm.py 文件或目录 [...] [--no-schema] [-v]

逐个文件检查（目录会递归找 .vrm），有问题的打印出来，最后汇总；有错误时退出码为 1。

* humanoid：骨位名合法、必需骨位齐全、每根骨的 VRM 父骨位确实是它的祖先、
  用占位骨（vrm_*）补的骨位（道具 / 非人形模型会有，记为提示）。
* 网格：JOINTS_0 不越界；同一网格里 morph target 不重名（Unity 遇到重名的
  BlendShape 会抛异常，UniVRM 导入直接中断）。
* 表情：materialColorBinds 的材质号、morphTargetBinds 的 node / index 在范围内。
* SpringBone（VRM 1.0）：相邻关节是祖先关系、关节不落在 humanoid 骨上、参数有限、
  碰撞组号合法、同一节点不重复当关节。
* Schema（VRM 1.0）：用 vrm-c/vrm-specification 的官方 JSON Schema 校验 VRMC_vrm、
  VRMC_springBone、VRMC_materials_mtoon。需要 jsonschema 包；第一次运行时从 GitHub
  下载固定版本的 schema，缓存在 tools/qa/.schema-cache/。UniVRM 对不合规的数据很少
  容错（例如 targetValue 少一个分量就 IndexOutOfRange），所以这一项很有用。
"""
import argparse
import json
import math
import os
import sys
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path[:0] = [os.path.join(ROOT, "formats"), os.path.join(ROOT, "convert")]
import vrmio  # noqa: E402
import pmx2vrm  # noqa: E402

# ---------------------------------------------------------------- humanoid --
_B10 = set("hips spine chest upperChest neck head leftEye rightEye jaw leftUpperLeg leftLowerLeg "
           "leftFoot leftToes rightUpperLeg rightLowerLeg rightFoot rightToes leftShoulder "
           "leftUpperArm leftLowerArm leftHand rightShoulder rightUpperArm rightLowerArm "
           "rightHand".split())
_B0 = set(_B10)
for _s in ("left", "right"):
    _B10 |= {_s + "ThumbMetacarpal", _s + "ThumbProximal", _s + "ThumbDistal"}
    _B0 |= {_s + "ThumbProximal", _s + "ThumbIntermediate", _s + "ThumbDistal"}
    for _f in ("Index", "Middle", "Ring", "Little"):
        for _j in ("Proximal", "Intermediate", "Distal"):
            _B10.add(_s + _f + _j)
            _B0.add(_s + _f + _j)
_REQ = ("hips spine head leftUpperArm leftLowerArm leftHand rightUpperArm rightLowerArm "
        "rightHand leftUpperLeg leftLowerLeg leftFoot rightUpperLeg rightLowerLeg "
        "rightFoot").split()

# ------------------------------------------------------------------ schema --
SPEC_COMMIT = "94e82dd346fa6cf0337c4421728640e5252dd38e"     # vrm-c/vrm-specification
SPEC_URL = "https://raw.githubusercontent.com/vrm-c/vrm-specification/%s/specification/%s/schema/%s"
SPEC_FILES = {
    "VRMC_vrm-1.0": [
        "VRMC_vrm.schema.json", "VRMC_vrm.meta.schema.json", "VRMC_vrm.humanoid.schema.json",
        "VRMC_vrm.humanoid.humanBones.schema.json",
        "VRMC_vrm.humanoid.humanBones.humanBone.schema.json",
        "VRMC_vrm.firstPerson.schema.json", "VRMC_vrm.firstPerson.meshAnnotation.schema.json",
        "VRMC_vrm.lookAt.schema.json", "VRMC_vrm.lookAt.rangeMap.schema.json",
        "VRMC_vrm.expressions.schema.json", "VRMC_vrm.expressions.expression.schema.json",
        "VRMC_vrm.expressions.expression.materialColorBind.schema.json",
        "VRMC_vrm.expressions.expression.morphTargetBind.schema.json",
        "VRMC_vrm.expressions.expression.textureTransformBind.schema.json"],
    "VRMC_springBone-1.0": [
        "VRMC_springBone.schema.json", "VRMC_springBone.collider.schema.json",
        "VRMC_springBone.colliderGroup.schema.json", "VRMC_springBone.joint.schema.json",
        "VRMC_springBone.shape.schema.json", "VRMC_springBone.spring.schema.json"],
    "VRMC_materials_mtoon-1.0": [
        "VRMC_materials_mtoon.schema.json", "mtoon.shadingShiftTexture.schema.json"],
}
# glTF 本体的几个 schema 只被引用、不需要逐项校验，用最小定义代替
_GLTF_STUBS = {
    "glTFProperty.schema.json": {"type": "object"},
    "glTFid.schema.json": {"type": "integer", "minimum": 0},
    "textureInfo.schema.json": {"type": "object", "required": ["index"],
                                "properties": {"index": {"type": "integer", "minimum": 0},
                                               "texCoord": {"type": "integer", "minimum": 0}}},
}
_BASE = "https://vrm-spec.invalid/"


def load_validators():
    """→ {扩展名: validator}；缺 jsonschema 或下载失败时返回 (None, 原因)。"""
    try:
        import jsonschema
        from referencing import Registry, Resource
        from referencing.jsonschema import DRAFT4
    except ImportError:
        return None, "未安装 jsonschema（pip install jsonschema），跳过 Schema 校验"
    cache = os.path.join(HERE, ".schema-cache", SPEC_COMMIT[:12])
    docs = dict(_GLTF_STUBS)
    for d, files in SPEC_FILES.items():
        for fn in files:
            path = os.path.join(cache, fn)
            if not os.path.isfile(path):
                os.makedirs(cache, exist_ok=True)
                try:
                    with urllib.request.urlopen(SPEC_URL % (SPEC_COMMIT, d, fn), timeout=30) as r:
                        data = r.read()
                except OSError as e:
                    return None, "下载 schema 失败（%s），跳过 Schema 校验" % e
                with open(path, "wb") as f:
                    f.write(data)
            with open(path, encoding="utf-8") as f:
                docs[fn] = json.load(f)
    resources = []
    for fn, doc in docs.items():
        # draft-04 用 id（不是 $id）定基准 URI，相对的 $ref 才解析得到
        doc = dict({k: v for k, v in doc.items() if k not in ("$schema", "$id")}, id=_BASE + fn)
        resources.append((_BASE + fn, Resource.from_contents(doc, default_specification=DRAFT4)))
    registry = Registry().with_resources(resources)
    out = {}
    for ext in ("VRMC_vrm", "VRMC_springBone", "VRMC_materials_mtoon"):
        schema = registry.contents(_BASE + ext + ".schema.json")
        out[ext] = jsonschema.Draft4Validator(schema, registry=registry)
    return out, None


def schema_errors(g, validators):
    errs = []

    def run(ext, obj, where):
        for e in validators[ext].iter_errors(obj):
            path = "/".join(str(p) for p in e.absolute_path)
            errs.append("schema %s/%s: %s" % (where, path, e.message[:120]))

    ext = g.get("extensions", {})
    if "VRMC_vrm" in ext:
        run("VRMC_vrm", ext["VRMC_vrm"], "VRMC_vrm")
    if "VRMC_springBone" in ext:
        run("VRMC_springBone", ext["VRMC_springBone"], "VRMC_springBone")
    for i, m in enumerate(g.get("materials", [])):
        mt = m.get("extensions", {}).get("VRMC_materials_mtoon")
        if mt is not None:
            run("VRMC_materials_mtoon", mt, "materials/%d/mtoon" % i)
    return errs


# ------------------------------------------------------------------ checks --
def check(path, validators=None):
    """→ (错误列表, 提示列表, 摘要 dict)"""
    errs, notes = [], []
    g, b = vrmio.read_glb(path)
    ext = g.get("extensions", {})
    nodes = g["nodes"]
    par = {c: i for i, n in enumerate(nodes) for c in n.get("children", [])}
    is1 = "VRMC_vrm" in ext
    if not is1 and "VRM" not in ext:
        return ["不是 VRM（没有 VRMC_vrm / VRM 扩展）"], [], {}

    # humanoid
    if is1:
        hb = {k: v["node"] for k, v in ext["VRMC_vrm"]["humanoid"]["humanBones"].items()}
        valid, req = _B10, _REQ
    else:
        hb = {x["bone"]: x["node"] for x in ext["VRM"]["humanoid"]["humanBones"]}
        valid, req = _B0, _REQ + ["chest", "neck"]
    for k in sorted(set(hb) - valid):
        errs.append("humanoid：非法骨位 %s" % k)
    for k in req:
        if k not in hb:
            errs.append("humanoid：缺少必需骨位 %s" % k)
    for h, n in hb.items():
        a = pmx2vrm._vrm_anchor(h, hb)
        if a is None:
            continue
        p = par.get(n)
        while p is not None and p != hb[a]:
            p = par.get(p)
        if p is None:
            errs.append("humanoid：%s 不在 %s 之下" % (h, a))
    placeholders = sorted(h for h, n in hb.items() if nodes[n].get("name", "").startswith("vrm_"))
    if placeholders:
        notes.append("humanoid：%d 根占位骨（%s）" % (len(placeholders), ", ".join(placeholders[:6])
                                                   + (" …" if len(placeholders) > 6 else "")))

    # mesh
    njoints = len(g["skins"][0]["joints"]) if g.get("skins") else 0
    for mi, m in enumerate(g.get("meshes", [])):
        for pr in m["primitives"]:
            if "JOINTS_0" in pr["attributes"]:
                if max(vrmio.read_accessor(g, b, pr["attributes"]["JOINTS_0"])) >= njoints:
                    errs.append("mesh %d：JOINTS_0 超出 skin.joints" % mi)
                    break
        names = (m.get("extras") or {}).get("targetNames") or []
        for k, v in Counter(names).items():
            if v > 1:
                errs.append("mesh %d：morph target 重名「%s」×%d（Unity 导入会中断）" % (mi, k, v))

    # expressions
    nm = len(g.get("materials", []))
    if is1:
        groups = ext["VRMC_vrm"].get("expressions", {})
        for grp in ("preset", "custom"):
            for name, e in (groups.get(grp) or {}).items():
                for bnd in e.get("materialColorBinds", []):
                    if not 0 <= bnd["material"] < nm:
                        errs.append("表情 %s：材质号 %d 越界" % (name, bnd["material"]))
                for bnd in e.get("morphTargetBinds", []):
                    node = nodes[bnd["node"]] if 0 <= bnd["node"] < len(nodes) else {}
                    if "mesh" not in node:
                        errs.append("表情 %s：node %d 没有网格" % (name, bnd["node"]))
                        continue
                    nt = len(g["meshes"][node["mesh"]]["primitives"][0].get("targets", []))
                    if not 0 <= bnd["index"] < nt:
                        errs.append("表情 %s：morph 索引 %d 越界（共 %d）" % (name, bnd["index"], nt))

    # SpringBone
    summary = {"spec": "1.0" if is1 else "0.x", "humanBones": len(hb),
               "placeholders": len(placeholders)}
    sb = ext.get("VRMC_springBone")
    if sb:
        hum = set(hb.values())
        seen = Counter()
        ncg = len(sb.get("colliderGroups", []))
        for sp in sb["springs"]:
            js = sp["joints"]
            for a, c in zip(js, js[1:]):
                q = par.get(c["node"])
                while q is not None and q != a["node"]:
                    q = par.get(q)
                if q is None:
                    errs.append("SpringBone %s：%s 不是 %s 的后代" % (
                        sp.get("name"), nodes[c["node"]]["name"], nodes[a["node"]]["name"]))
            for j in js:
                seen[j["node"]] += 1
                if j["node"] in hum:
                    errs.append("SpringBone %s：关节落在 humanoid 骨 %s 上"
                                % (sp.get("name"), nodes[j["node"]]["name"]))
                for k in ("hitRadius", "stiffness", "gravityPower", "dragForce"):
                    if k in j and not math.isfinite(j[k]):
                        errs.append("SpringBone %s：%s 不是有限值" % (sp.get("name"), k))
            for cg in sp.get("colliderGroups", []):
                if not 0 <= cg < ncg:
                    errs.append("SpringBone %s：碰撞组号 %d 越界" % (sp.get("name"), cg))
        for n, c in seen.items():
            if c > 1:
                errs.append("SpringBone：节点 %s 在 %d 条链里当关节" % (nodes[n]["name"], c))
        summary.update(springs=len(sb["springs"]),
                       joints=sum(len(sp["joints"]) for sp in sb["springs"]),
                       colliders=len(sb.get("colliders", [])))

    if validators and is1:
        errs += schema_errors(g, validators)
    return errs, notes, summary


def iter_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for d, _, fs in os.walk(p):
                for f in sorted(fs):
                    if f.lower().endswith(".vrm"):
                        yield os.path.join(d, f)
        else:
            yield p


def main(argv=None):
    ap = argparse.ArgumentParser(description="VRM 文件静态检查")
    ap.add_argument("paths", nargs="+", help=".vrm 文件或目录")
    ap.add_argument("--no-schema", action="store_true", help="不做官方 JSON Schema 校验")
    ap.add_argument("-v", "--verbose", action="store_true", help="通过的文件也打印摘要和提示")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    validators = None
    if not a.no_schema:
        validators, why = load_validators()
        if why:
            print("提示：" + why)
    n = bad = 0
    for path in iter_files(a.paths):
        n += 1
        try:
            errs, notes, summ = check(path, validators)
        except Exception as e:  # noqa: BLE001  坏文件也要继续往下查
            errs, notes, summ = ["读取失败：%s: %s" % (type(e).__name__, e)], [], {}
        if errs:
            bad += 1
            print("✗ %s" % path)
            shown = Counter()
            for e in errs:                       # 同类错误只列前几条
                key = e.split("：")[0] if "：" in e else e[:40]
                shown[key] += 1
                if shown[key] <= 5:
                    print("    " + e)
            for k, c in shown.items():
                if c > 5:
                    print("    …… %s 还有 %d 条" % (k, c - 5))
        elif a.verbose:
            print("✓ %s  %s" % (path, " ".join("%s=%s" % kv for kv in summ.items())))
            for t in notes:
                print("    提示：" + t)
    print("检查 %d 个文件，%d 个有错误" % (n, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
