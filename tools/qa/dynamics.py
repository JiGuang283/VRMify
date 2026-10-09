# -*- coding: utf-8 -*-
"""SpringBone 动态测试：静止偏移 + 待机 / 带位移走路 / 跑步时的末端颤动。

    python tools/qa/dynamics.py 文件或目录 [...] [-j 4] [--motions idle,walk_real,run]
                                [--fps 60] [--jitter 0] [--json 结果.json] [--strict]

用 three.js + three-vrm 在 Node 里无界面地跑物理（和 UniVRM 同一套 SpringBone 规则），
每个文件一个进程并行。只测带 VRMC_springBone 的人形模型（占位骨补出来的道具跳过）。
第一次用要先装依赖：  cd tools/qa/harness && npm install

指标（越小越稳；参考范围来自 VRoid / VRM 官方示例模型在同样动作下的实测）：
  静止偏移  站着不动 2 秒后弹簧末端离绑定姿势的最大距离。正常 < 5 cm
  hf        末端 8 Hz 以上的颤动能量 / 链长 ×1000（平均/最大）。原生模型头发 2～16、裙摆 3～58
  swing     末端最大摆幅 / 链长
  穿腿      裙摆关节落在模型自己的腿部碰撞体里的比例（含静止时就重叠的，要和 idle 对比看）
超出范围的数值后面标「!」；--strict 时有标记就返回 1。
"""
import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "harness")
LIMITS = {"rest": 0.05, "hf": {"hair": 16.0, "skirt": 60.0, "torso": 60.0, "other": 60.0}}


def glb_json(path):
    with open(path, "rb") as f:
        head = f.read(20)
        n = struct.unpack_from("<I", head, 12)[0]
        return json.loads(f.read(n))


def testable(path):
    """有 SpringBone、humanoid 不是占位骨拼出来的 → True。"""
    try:
        g = glb_json(path)
    except (OSError, ValueError, struct.error):
        return False
    if "VRMC_springBone" not in g.get("extensionsUsed", []):
        return False
    hb = g.get("extensions", {}).get("VRMC_vrm", {}).get("humanoid", {}).get("humanBones", {})
    names = [g["nodes"][v["node"]].get("name", "") for v in hb.values()]
    return not any(n.startswith("vrm_") for n in names)


def iter_files(paths):
    for p in paths:
        if os.path.isdir(p):
            for d, _, fs in os.walk(p):
                for f in sorted(fs):
                    if f.lower().endswith(".vrm"):
                        yield os.path.join(d, f)
        else:
            yield p


def run_one(path, env):
    r = subprocess.run(["node", os.path.join(HARNESS, "measure.mjs"), os.path.abspath(path)],
                       cwd=HARNESS, env=env, capture_output=True, encoding="utf-8")
    lines = [l for l in r.stdout.splitlines() if l.startswith("{")]
    if r.returncode or not lines:
        return {"file": path, "error": (r.stderr or r.stdout).strip()[-800:]}
    return json.loads(lines[-1])


def fmt(res):
    """一个文件的结果 → (文本行, 是否有超范围)"""
    flag = False
    name = os.path.basename(res["file"])
    if "error" in res:
        return ["✗ %s\n    %s" % (name, res["error"].replace("\n", "\n    "))], True
    rest = res["rest"]
    bad = rest["max"] > LIMITS["rest"]
    flag |= bad
    out = ["%s %s" % ("!" if bad else "·", name),
           "    静止偏移 最大 %.1f cm%s（%s）· 超过 1 cm 的 %d / %d" % (
               rest["max"] * 100, " !" if bad else "", rest["maxName"], rest["over1cm"], rest["joints"])]
    for m, r in res["motions"].items():
        parts = []
        for cat in ("hair", "skirt", "torso", "other"):
            c = r["cats"].get(cat)
            if not c:
                continue
            over = c["hfMax"] > LIMITS["hf"][cat]
            flag |= over
            parts.append("%s hf %.1f/%.1f%s swing %.2f" % (
                {"hair": "头发", "skirt": "裙摆", "torso": "躯干", "other": "其它"}[cat],
                c["hf"], c["hfMax"], "!" if over else "", c["swing"]))
        if r.get("skirtInside") is not None:
            parts.append("穿腿 %.1f%%" % (r["skirtInside"] * 100))
        if r.get("skirtGap"):
            g = r["skirtGap"]
            parts.append("张开 %.1f/%.1f/%.1f cm" % (g["mean"] * 100, g["p90"] * 100, g["max"] * 100))
        out.append("    %-9s %s" % (m, " | ".join(parts)))
        if r.get("worst") and any(w["hf"] > LIMITS["hf"][w["cat"]] for w in r["worst"]):
            for w in r["worst"]:
                out.append("              %s:%s hf %.1f swing %.2f（%d 节，%.2f m）" % (
                    w["cat"], w["chain"], w["hf"], w["swing"], w["joints"], w["length"]))
    return out, flag


def main(argv=None):
    ap = argparse.ArgumentParser(description="SpringBone 动态测试（three-vrm，无界面）")
    ap.add_argument("paths", nargs="+", help=".vrm 文件或目录")
    ap.add_argument("-j", "--jobs", type=int, default=4, help="并行数（每个文件一个 Node 进程）")
    ap.add_argument("--motions", default="idle,walk_real,run",
                    help="动作：idle, walk_real, run, leg_raise, yaw_gentle, yaw_strong, walk, static")
    ap.add_argument("--fps", type=float, default=60, help="物理帧率（默认 60）")
    ap.add_argument("--jitter", type=float, default=0, help="帧时间抖动比例，如 0.3")
    ap.add_argument("--json", help="把原始结果写成 JSON")
    ap.add_argument("--strict", action="store_true", help="有超范围的数值时返回 1")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if not shutil.which("node"):
        sys.exit("需要 Node.js（https://nodejs.org）")
    if not os.path.isdir(os.path.join(HARNESS, "node_modules", "@pixiv", "three-vrm")):
        sys.exit("先安装依赖：cd %s && npm install" % HARNESS)
    files = list(iter_files(a.paths))
    todo = [f for f in files if testable(f)]
    if len(todo) < len(files):
        print("跳过 %d 个没有 SpringBone 或不是人形的文件" % (len(files) - len(todo)))
    env = dict(os.environ, MOTIONS=a.motions, FPS=str(a.fps), JITTER=str(a.jitter), DETAIL="3")
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        results = list(ex.map(lambda f: run_one(f, env), todo))
    n_flag = 0
    for res in results:
        lines, flag = fmt(res)
        n_flag += flag
        print("\n".join(lines))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
    print("测试 %d 个文件，%d 个有超出参考范围的数值（标「!」）" % (len(results), n_flag))
    return 1 if (a.strict and n_flag) else 0


if __name__ == "__main__":
    sys.exit(main())
