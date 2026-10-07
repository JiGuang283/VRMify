# -*- coding: utf-8 -*-
"""真实模型的回归检查：改转换代码前后，同样的输入输出是否一致。

    python tools/regress.py record 模型.pmx|目录 ... [--spec 0x]   记下基准（golden）
    python tools/regress.py check                                   用当前代码重转、对比基准
    python tools/regress.py check --code 另一份代码的目录            用别的代码（例如 git worktree）转

基准存在 tests/.golden/（不进仓库：模型的许可不允许再分发，衍生的数据也只留在本机）：
manifest.json 记样本路径和选项，每个样本一个 JSON（glTF JSON 全文 + 二进制块 sha256）。
check 时每个样本都会：转换 → check_vrm 结构 / Schema 校验 → 和基准对比，列出 JSON 里
不同的路径（最多 20 条）和二进制块是否一致；有错误或差异时退出码为 1。
对比只是提示「哪里变了」，变化是不是预期的要自己判断，确认后重新 record。
"""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GOLDEN = os.path.join(ROOT, "tests", ".golden")
sys.path.insert(0, os.path.join(HERE, "qa"))


def _use_code(root):
    """让 import pmx2vrm / vrmio 来自 root 这份代码。"""
    for p in (os.path.join(root, "formats"), os.path.join(root, "convert")):
        sys.path.insert(0, p)


def _convert(pmx, out, spec):
    import pmx2vrm
    pmx2vrm.convert(pmx, out, spec="1.0" if spec == "1.0" else "0.x",
                    meta={"title": os.path.splitext(os.path.basename(pmx))[0]})


def _fingerprint(path):
    import vrmio
    g, b = vrmio.read_glb(path)
    return {"json": g, "bin_sha256": hashlib.sha256(b).hexdigest(), "bin_len": len(b),
            "size": os.path.getsize(path)}


def _diff(a, b, path="", out=None, limit=20):
    out = [] if out is None else out
    if len(out) >= limit:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        for k in list(a) + [k for k in b if k not in a]:
            if k not in b:
                out.append("- %s/%s" % (path, k))
            elif k not in a:
                out.append("+ %s/%s" % (path, k))
            else:
                _diff(a[k], b[k], "%s/%s" % (path, k), out, limit)
            if len(out) >= limit:
                break
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append("~ %s 长度 %d → %d" % (path, len(a), len(b)))
        for i, (x, y) in enumerate(zip(a, b)):
            _diff(x, y, "%s/%d" % (path, i), out, limit)
            if len(out) >= limit:
                break
    elif a != b:
        out.append("~ %s：%s → %s" % (path, json.dumps(a, ensure_ascii=False)[:60],
                                     json.dumps(b, ensure_ascii=False)[:60]))
    return out


def _samples(paths):
    out = []
    for p in paths:
        if os.path.isdir(p):
            for d, _, fs in os.walk(p):
                out += [os.path.join(d, f) for f in sorted(fs) if f.lower().endswith(".pmx")]
        elif p.lower().endswith(".pmx"):
            out.append(p)
    return [os.path.abspath(p) for p in out]


def _key(path, spec):
    return hashlib.md5(("%s|%s" % (os.path.normcase(path), spec)).encode("utf-8")).hexdigest()[:12]


def record(args):
    samples = _samples(args.paths)
    if not samples:
        print("没有找到 PMX")
        return 1
    os.makedirs(GOLDEN, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="m2v_regress_")
    manifest = {"spec": args.spec, "samples": samples, "recorded": time.strftime("%Y-%m-%d %H:%M"),
                "code": os.path.abspath(args.code)}
    try:
        for n, pmx in enumerate(samples, 1):
            out = os.path.join(tmp, "%d.vrm" % n)
            t0 = time.time()
            _convert(pmx, out, args.spec)
            with open(os.path.join(GOLDEN, _key(pmx, args.spec) + ".json"), "w", encoding="utf-8") as f:
                json.dump(_fingerprint(out), f, ensure_ascii=False)
            print("[%d/%d] %.1fs  %s" % (n, len(samples), time.time() - t0, pmx))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    with open(os.path.join(GOLDEN, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print("基准已记录：%d 个样本 → %s" % (len(samples), GOLDEN))
    return 0


def check(args):
    import check_vrm
    try:
        with open(os.path.join(GOLDEN, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
    except OSError:
        print("还没有基准：先运行 python tools/regress.py record <模型或目录>")
        return 1
    validators, why = (None, "--no-schema") if args.no_schema else check_vrm.load_validators()
    if why:
        print("提示：" + why)
    spec = manifest["spec"]
    tmp = tempfile.mkdtemp(prefix="m2v_regress_")
    bad = 0
    try:
        for n, pmx in enumerate(manifest["samples"], 1):
            name = os.path.basename(pmx)
            gpath = os.path.join(GOLDEN, _key(pmx, spec) + ".json")
            if not os.path.isfile(pmx) or not os.path.isfile(gpath):
                print("[%d] 跳过（找不到样本或基准）：%s" % (n, pmx))
                continue
            out = os.path.join(tmp, "%d.vrm" % n)
            t0 = time.time()
            try:
                _convert(pmx, out, spec)
            except Exception as e:  # noqa: BLE001
                print("✗ [%d] %s  转换失败：%s: %s" % (n, name, type(e).__name__, e))
                bad += 1
                continue
            errs, _, _ = check_vrm.check(out, validators)
            with open(gpath, encoding="utf-8") as f:
                gold = json.load(f)
            cur = _fingerprint(out)
            diffs = _diff(gold["json"], cur["json"])
            same_bin = gold["bin_sha256"] == cur["bin_sha256"]
            ok = not errs and not diffs and same_bin
            bad += not ok
            print("%s [%d] %.1fs  %s  %.2f → %.2f MB" % ("✓" if ok else "✗", n, time.time() - t0, name,
                                                      gold["size"] / 1048576.0, cur["size"] / 1048576.0))
            for e in errs[:10]:
                print("    校验：" + e)
            for d in diffs:
                print("    JSON " + d)
            if not same_bin:
                print("    二进制块不同（%d → %d 字节）" % (gold["bin_len"], cur["bin_len"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("对比 %d 个样本，%d 个有差异或错误" % (len(manifest["samples"]), bad))
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="真实模型回归检查")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record", help="记录基准")
    r.add_argument("paths", nargs="+", help="PMX 文件或目录")
    r.add_argument("--spec", default="1.0", choices=["1.0", "0x"])
    c = sub.add_parser("check", help="用当前代码重转并对比基准")
    c.add_argument("--no-schema", action="store_true")
    for p in (r, c):
        p.add_argument("--code", default=ROOT, help="用哪份代码转换（默认本仓库）")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    _use_code(a.code)
    import pmx2vrm  # noqa: F401  先从 --code 导入（check_vrm 会把本仓库的路径插到最前面）
    return record(a) if a.cmd == "record" else check(a)


if __name__ == "__main__":
    sys.exit(main())
