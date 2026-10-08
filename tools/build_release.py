# -*- coding: utf-8 -*-
"""打包发布用的 zip（绿色版：解压后双击 Model-To-VRM.exe）。本地和 GitHub Actions 用的是同一个脚本。

    python tools/build_release.py                        → dist/Model-To-VRM-v0.1.0-win64.zip
    python tools/build_release.py --expect-version v0.1.0   tag 和 ui/version.py 不一致时报错退出

步骤：PyInstaller 按 Model-To-VRM.spec 打包 → 把 LICENSE、README 放进程序文件夹 → 压成 zip。
需要 requirements-dev.txt 里的包（pyinstaller、pywebview、Pillow）。
"""
import argparse
import os
import platform
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "ui"))
from version import __version__  # noqa: E402

APP = "Model-To-VRM"
# 随程序一起发的文档：GPL-3.0 要求附上许可证；README 里有用法和源码出处
DOCS = ["LICENSE", "README_SC.md", "README.md"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="打包发布用的 zip")
    ap.add_argument("--expect-version", default="", help="tag（如 v0.1.0），必须和 ui/version.py 一致")
    ap.add_argument("--dist", default=os.path.join(ROOT, "dist"))
    ap.add_argument("--work", default=os.path.join(ROOT, "build"))
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    want = a.expect_version.lstrip("vV")
    if want and want != __version__:
        print("版本号不一致：tag 是 %s，ui/version.py 里是 %s（先改 version.py 再打 tag）"
              % (a.expect_version, __version__))
        return 1

    subprocess.run([sys.executable, "-m", "PyInstaller", os.path.join(ROOT, "Model-To-VRM.spec"),
                    "--noconfirm", "--clean", "--distpath", a.dist, "--workpath", a.work],
                   cwd=ROOT, check=True)
    app_dir = os.path.join(a.dist, APP)
    for fn in DOCS:
        shutil.copy2(os.path.join(ROOT, fn), os.path.join(app_dir, fn))

    arch = "win64" if sys.platform.startswith("win") and platform.machine().endswith("64") else \
        "%s-%s" % (sys.platform, platform.machine().lower())
    out = os.path.join(a.dist, "%s-v%s-%s.zip" % (APP, __version__, arch))
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for d, _, fs in os.walk(app_dir):
            for fn in fs:
                p = os.path.join(d, fn)
                zf.write(p, os.path.join(APP, os.path.relpath(p, app_dir)))
    print("已生成 %s（%.1f MB）" % (out, os.path.getsize(out) / 1048576.0))
    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:                                   # 给 Actions 后面的步骤用
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write("zip=%s\nversion=%s\n" % (out, __version__))
    return 0


if __name__ == "__main__":
    sys.exit(main())
