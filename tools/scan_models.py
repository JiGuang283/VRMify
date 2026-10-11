# -*- coding: utf-8 -*-
"""用本机的真实模型跑一遍骨骼处理和自检（不写 VRM，几秒一个），看哪些模型会在转换日志里报问题。

    python tools/scan_models.py 目录或文件 ...           # 目录递归找 PMX 和压缩包（压缩包在临时目录里解开）
    python tools/scan_models.py ~/Downloads -o 结果.txt

每个 PMX 一行：合并的付与骨数、按比例拆权重的骨数、自检问题；有问题的下面列出来。
改骨骼相关的代码前后各跑一次对比，发版前跑一次。有自检问题时退出码为 1。
"""
import argparse
import glob
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in ("convert", "formats"):
    sys.path.insert(0, os.path.join(ROOT, _p))
import pipeline  # noqa: E402
import pmxio  # noqa: E402
from vrmconv import checks, mesh, skeleton  # noqa: E402


def pmx_files(paths, tmp):
    """→ (PMX 路径, 显示名)；压缩包解到 tmp 下，包里套包再解一层。"""
    def inside(arc, d):
        for q in pipeline.find_inputs(d):
            k = pipeline.classify(q)
            if k == "pmx":
                yield q, os.path.basename(arc) + "/" + os.path.relpath(q, d)
            elif k in pipeline.ARCHIVES:
                sub = tempfile.mkdtemp(dir=tmp)
                pipeline.extract_archive(q, k, sub)
                yield from ((f, os.path.basename(arc) + "/" + os.path.relpath(f, sub))
                            for f in glob.glob(os.path.join(sub, "**", "*.pmx"), recursive=True))

    for p in paths:
        if os.path.isdir(p):
            for f in sorted(glob.glob(os.path.join(p, "**", "*"), recursive=True)):
                k = pipeline.classify(f) if os.path.isfile(f) else None
                if k == "pmx":
                    yield f, os.path.relpath(f, p)
                elif k in pipeline.ARCHIVES:
                    yield from pmx_files([f], tmp)
        elif pipeline.classify(p) == "pmx":
            yield p, os.path.basename(p)
        elif pipeline.classify(p) in pipeline.ARCHIVES:
            d = tempfile.mkdtemp(dir=tmp)
            try:
                pipeline.extract_archive(p, pipeline.classify(p), d)
                yield from inside(p, d)
            except Exception as e:  # noqa: BLE001
                print("跳过 %s：%s" % (p, e), file=sys.stderr)


def scan(path):
    """→ (合并数, 拆分骨数, [自检问题文字])，和转换时一样的骨骼处理（VRM 1.0、T-pose）。"""
    model = pmxio.read_pmx(path)
    lines = []
    log = lambda m, t=None: lines.append((m, t))                         # noqa: E731
    skeleton.fix_facing(model, log)
    verts, bones = model["vertices"], model["bones"]
    ys = [v["pos"][1] for v in verts] or [0.0]
    scale = 1.6 / (max(ys) - min(ys)) if max(ys) > min(ys) else 0.08
    raw = mesh.vertex_weights(verts, len(bones), {})
    bs = skeleton.prepare_bones(bones, True, log, weighted={b for wl in raw for b, w in wl if w > 0})
    vw = mesh.vertex_weights(verts, len(bones), bs.alias, base=raw)
    _, bpos, vpos, _, _ = skeleton.apply_tpose(verts, bones, bs, vw, True, log)
    vw = skeleton.split_partial(bones, bs, vw, model.get("rigid_bodies"), log)
    split = next((int(m.split("：")[1].split(" ")[0]) for m, _ in lines if m.startswith("付与骨：")), 0)
    checks.report(bones, bs, raw, vw, bpos, vpos, scale, log)
    return len(bs.alias), split, [m for m, t in lines if m.startswith("自检")]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("paths", nargs="+")
    ap.add_argument("-o", "--out", help="结果另存一份到文件")
    a = ap.parse_args()
    out = [sys.stdout] + ([open(a.out, "w", encoding="utf-8")] if a.out else [])

    def say(s):
        for f in out:
            print(s, file=f, flush=True)
    tmp = tempfile.mkdtemp(prefix="vrmify_scan_")
    seen, n, bad, t0 = set(), 0, 0, time.time()
    try:
        for f, label in pmx_files(a.paths, tmp):
            key = (os.path.basename(f), os.path.getsize(f))
            if key in seen:
                continue
            seen.add(key)
            n += 1
            try:
                merged, split, issues = scan(f)
            except Exception as e:  # noqa: BLE001
                say("错误  %s：%s: %s" % (label, type(e).__name__, e))
                continue
            bad += bool(issues)
            say("%s  合并 %3d · 拆分 %3d  %s" % ("!!" if issues else "ok", merged, split, label))
            for m in issues:
                say("      " + m)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    say("%d 个 PMX，%d 个有自检问题（%.0f 秒）" % (n, bad, time.time() - t0))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
