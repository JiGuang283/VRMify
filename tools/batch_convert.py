# -*- coding: utf-8 -*-
"""批量把一个目录下的 PMX 转成 VRM。

    python tools/batch_convert.py [模型目录] [-o 输出目录] [--only 关键字 ...]
                                  [--variants both|spring|nospring] [--spec 1.0|0.x]
                                  [-j 进程数] [--report 结果.json]

* 模型目录下每个子文件夹算一个角色，递归找其中所有 .pmx；子文件夹里的 .zip
  （作者常把道具、差分模型打包在里面）也会解出其中的 .pmx 一起转。解压在临时
  目录里进行（连同角色文件夹的贴图一起复制过去，相对路径才找得到），不改动
  模型目录本身。
* 输出：<输出目录>/有SpringBone/<角色>/<名>.vrm 和 <输出目录>/无SpringBone/<角色>/<名>.vrm
  （--variants 只要其中一种时不分这一层）。
* 角色文件夹名形如「<名字>_by_<游戏>_<哈希>」（模之屋的下载格式）时整理成
  「<游戏>—<名字>」，并去掉「(修模绑骨后)」这类说明和结尾的版本号；其它名字原样使用。
* 最后打印每个模型的结果；用占位骨补齐 humanoid 的（道具、非人形）会标出来。
"""
import argparse
import glob
import json
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GAMES = {"崩坏：星穹铁道": "星穹铁道"}          # 下载站的游戏名 → 文件夹里用的简称


def char_name(folder):
    """角色文件夹名 → 输出用的角色名。"""
    name, sep, rest = folder.partition("_by_")
    if not sep:
        return folder
    game = rest.rsplit("_", 1)[0]
    game = GAMES.get(game, game)
    name = name.strip("【】")
    m = re.search(r"[（(][^（(]*$", name)          # 「(修模绑骨后)」之类的说明；「（星）」保留
    if m and re.search(r"修|绑|更新|版", m.group()):
        name = name[:m.start()]
    name = re.sub(r"\d+(\.\d+)*$", "", name)      # 结尾的版本号
    for pre in (game + "—", game + "_"):
        if name.startswith(pre):
            name = name[len(pre):]
    return game + "—" + name.strip("_— ")


def zip_members(zf):
    """zip 成员 → (ZipInfo, 解码后的文件名)。没设 UTF-8 标志的按 cp932 / gbk 依次试。"""
    for info in zf.infolist():
        raw = (info.filename.encode("utf-8") if info.flag_bits & 0x800
               else info.filename.encode("cp437"))
        for enc in ("utf-8", "cp932", "gbk"):
            try:
                yield info, raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue


def collect(model_dir, extract_dir, only=None):
    """→ [(角色文件夹名, pmx 路径, 来源)]；only = 文件夹名须包含其中之一的关键字。"""
    jobs = []
    for d in sorted(glob.glob(os.path.join(model_dir, "*"))):
        if not os.path.isdir(d):
            continue
        folder = os.path.basename(d)
        if only and not any(k in folder for k in only):
            continue
        have = set()
        for p in sorted(glob.glob(os.path.join(d, "**", "*.pmx"), recursive=True)):
            jobs.append((folder, p, "folder"))
            have.add(os.path.basename(p))
        zips = sorted(glob.glob(os.path.join(d, "*.zip")))
        if not zips:
            continue
        copy = os.path.join(extract_dir, folder)
        for z in zips:
            with zipfile.ZipFile(z) as zf:
                for info, name in zip_members(zf):
                    base = os.path.basename(name)
                    if not base.lower().endswith(".pmx") or base in have:
                        continue
                    if not os.path.isdir(copy):
                        shutil.copytree(d, copy, ignore=shutil.ignore_patterns(
                            "*.zip", "*.vrm", "*.pmx"))
                    dst = os.path.join(copy, base)
                    with zf.open(info) as src, open(dst, "wb") as f:
                        shutil.copyfileobj(src, f)
                    jobs.append((folder, dst, "zip:" + os.path.basename(z)))
                    have.add(base)
    return jobs


def run(job):
    folder, pmx, src, out_dir, variants, spec = job
    sys.path[:0] = [os.path.join(ROOT, "convert"), os.path.join(ROOT, "formats")]
    import pmx2vrm
    stem = os.path.splitext(os.path.basename(pmx))[0]
    res = {"folder": folder, "char": char_name(folder), "pmx": pmx, "src": src, "stem": stem}
    for label, spring in variants:
        sub = [out_dir] + ([label] if len(variants) > 1 else []) + [res["char"], stem + ".vrm"]
        out = os.path.join(*sub)
        logs = []
        t = time.time()
        try:
            st = pmx2vrm.convert(pmx, out, spec=spec, spring=spring, meta={"title": stem},
                                 log=lambda m, tag=None: logs.append((tag, m)))
            res[label] = {"ok": True, "out": out, "mb": st["bytes"] / 1048576.0,
                          "sec": time.time() - t, "logs": logs}
        except Exception as e:  # noqa: BLE001  一个模型失败不影响其它
            import traceback
            res[label] = {"ok": False, "err": "%s: %s" % (type(e).__name__, e),
                          "tb": traceback.format_exc()[-2000:], "logs": logs}
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="批量把目录下的 PMX 转成 VRM")
    ap.add_argument("models", nargs="?", default=os.path.join(ROOT, "model"),
                    help="模型目录（每个子文件夹一个角色），默认 ./model")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "vrm"), help="输出目录，默认 ./vrm")
    ap.add_argument("--only", nargs="+", metavar="关键字",
                    help="只转文件夹名包含这些关键字的角色，如 --only 穗穗 爱弥斯")
    ap.add_argument("--variants", choices=("both", "spring", "nospring"), default="both",
                    help="同时输出有 / 无 SpringBone 两份（默认），或只要其中一种")
    ap.add_argument("--spec", choices=("1.0", "0.x"), default="1.0", help="VRM 版本，默认 1.0")
    ap.add_argument("-j", "--jobs", type=int, default=max(1, min(6, (os.cpu_count() or 2) - 1)),
                    help="并行进程数")
    ap.add_argument("--report", help="把每个模型的结果和日志写成 JSON（给 tools/qa 用）")
    a = ap.parse_args(argv)

    variants = {"both": [("有SpringBone", True), ("无SpringBone", False)],
                "spring": [("有SpringBone", True)],
                "nospring": [("无SpringBone", False)]}[a.variants]
    if a.spec != "1.0" and a.variants != "nospring":
        print("提示：VRM 0.x 不输出 SpringBone，「有SpringBone」那份会和「无」一样")
    extract = tempfile.mkdtemp(prefix="model2vrm_zip_")
    try:
        jobs = collect(a.models, extract, a.only)
        names = [(char_name(f), os.path.basename(p)) for f, p, _ in jobs]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            sys.exit("输出文件重名：%s" % sorted(dup))
        print("共 %d 个 PMX → %s" % (len(jobs), a.out))
        args = [(f, p, s, a.out, variants, a.spec) for f, p, s in jobs]
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            results = list(ex.map(run, args))
    finally:
        shutil.rmtree(extract, ignore_errors=True)

    n_fail = 0
    for r in results:
        oks = [r[label]["ok"] for label, _ in variants]
        n_fail += oks.count(False)
        first = r[variants[0][0]]
        ph = sum(1 for _, m in first.get("logs", []) if "占位骨骼" in m)
        print("%s %-16s %-28s %s%s" % (
            "OK  " if all(oks) else "FAIL", r["char"][:16], r["stem"][:28],
            ("占位骨 %d 根" % ph) if ph else "",
            "" if all(oks) else "  " + "; ".join(r[l].get("err", "") for l, _ in variants)))
    if a.report:
        with open(a.report, "w", encoding="utf-8") as f:
            json.dump({"variants": [l for l, _ in variants], "results": results}, f,
                      ensure_ascii=False, indent=1)
    print("完成：%d 个模型，失败 %d 次" % (len(results), n_fail))
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
