# -*- coding: utf-8 -*-
"""zip 以外的压缩包：7z / rar / tar（.tar .tar.gz .tgz .tar.bz2 .tar.xz）。

    kind(path)           → "7z" / "rar" / "tar" / None（分卷包只认第一卷）
    extract(path, dest)  → 解出的文件数；解不了抛 RuntimeError（说明原因和怎么办）
    tools()              → 找到的解压工具（界面显示用）

tar 用 Python 自带的 tarfile。7z / rar 没有可靠的纯 Python 实现，借用电脑上已有的解压工具，
按顺序找：
  1. 7-Zip（7z.exe / 7za / 7zz）            7z、rar 都行
  2. WinRAR（UnRAR.exe / Rar.exe）          rar（新版也能解 7z，失败了再换下一个）
  3. Bandizip（bz.exe）                     7z、rar 都行
  4. Windows 自带的 tar.exe（bsdtar / libarchive）
     Windows 11 的版本 7z（含 LZMA）和 rar / rar5 都能解；Windows 10 的老版本解不了 LZMA 的 7z。
     它按系统的 ANSI 编码处理文件名：简体中文系统上中文、假名没问题，GBK 里没有的字（emoji 等）
     会解压失败
  5. py7zr（装了才用，只管 7z）
都不行时提示装 7-Zip。

安全：只解到 dest 里（工具自己会去掉绝对路径和 ..；解完再检查一遍，跑到外面的文件删掉），
不解链接；有密码的包不会卡住等输入（stdin 关掉、给 7-Zip 一个假密码），直接报错。
"""
import os
import re
import shutil
import subprocess
import sys
import tarfile

_TAR = (".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tbz", ".tar.xz", ".txz")
EXTS = (".7z", ".rar") + _TAR             # 文件对话框的过滤、拖放识别都用这个
_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def kind(path):
    """→ "7z" / "rar" / "tar" / None。分卷包（.part2.rar、.7z.002、.r00）只认第一卷。"""
    n = os.path.basename(path).lower()
    m = re.search(r"\.part(\d+)\.rar$", n)
    if m:
        return "rar" if int(m.group(1)) == 1 else None
    if n.endswith(".rar"):
        return "rar"
    m = re.search(r"\.7z\.(\d{3})$", n)
    if m:
        return "7z" if int(m.group(1)) == 1 else None
    if n.endswith(".7z"):
        return "7z"
    if n.endswith(_TAR):
        return "tar"
    return None


# ---- 找工具

def _reg_paths():
    """注册表里 7-Zip / WinRAR / Bandizip 的安装目录。"""
    if sys.platform != "win32":
        return []
    import winreg
    out = []
    for key, vals in ((r"SOFTWARE\7-Zip", ("Path64", "Path")),
                      (r"SOFTWARE\WinRAR", ("exe64", "exe32")),
                      (r"SOFTWARE\Bandizip", ("ProgramFolder",))):
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as k:
                        for v in vals:
                            try:
                                p = winreg.QueryValueEx(k, v)[0]
                            except OSError:
                                continue
                            if p:
                                out.append(os.path.dirname(p) if p.lower().endswith(".exe") else p)
                except OSError:
                    continue
    return out


def _find(names, dirs):
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return p
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def _bsdtar():
    """Windows 自带的 tar.exe 是 bsdtar；Git 带的 GNU tar 解不了 7z / rar，不能用 PATH 里的。"""
    cands = []
    if sys.platform == "win32":
        cands.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "tar.exe"))
    for n in ("bsdtar",):
        p = shutil.which(n)
        if p:
            cands.append(p)
    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            r = subprocess.run([p, "--version"], capture_output=True, timeout=10,
                               stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if b"bsdtar" in r.stdout or b"libarchive" in r.stdout:
            return p
    return None


_TOOLS = None


def tools(refresh=False):
    """→ {"7z": 路径, "unrar": 路径, "bandizip": 路径, "bsdtar": 路径, "py7zr": bool}（找不到为 None）。"""
    global _TOOLS
    if _TOOLS is None or refresh:
        pf = [os.environ.get(k) for k in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)")]
        pf = [p for p in pf if p]
        reg = _reg_paths()
        try:
            import py7zr  # noqa: F401
            has_py7zr = True
        except Exception:  # noqa: BLE001
            has_py7zr = False
        _TOOLS = {
            "7z": _find(("7z.exe", "7za.exe", "7zz.exe", "7z", "7za", "7zz"),
                        reg + [os.path.join(p, "7-Zip") for p in pf]),
            "unrar": _find(("UnRAR.exe", "Rar.exe", "unrar", "rar"),
                           reg + [os.path.join(p, "WinRAR") for p in pf]),
            "bandizip": _find(("bz.exe",), reg + [os.path.join(p, "Bandizip") for p in pf]),
            "bsdtar": _bsdtar(),
            "py7zr": has_py7zr,
        }
    return _TOOLS


# ---- 解压

def _count_and_check(dest):
    """解完检查：不在 dest 里的、链接都删掉，返回普通文件数。"""
    root = os.path.realpath(dest)
    n = 0
    for d, dirs, files in os.walk(dest):
        for name in list(dirs) + files:
            p = os.path.join(d, name)
            if os.path.islink(p) or not os.path.realpath(p).startswith(root + os.sep):
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    try:
                        os.remove(p)
                    except OSError:
                        pass
                if name in dirs:
                    dirs.remove(name)
            elif name in files:
                n += 1
    return n


def _safe_tar(path, dest):
    n = 0
    root = os.path.realpath(dest)
    with tarfile.open(path) as tf:
        for m in tf:
            if not m.isfile():               # 目录随文件建；链接、设备文件不解
                continue
            parts = [p for p in m.name.replace("\\", "/").split("/") if p not in ("", ".", "..")]
            if parts and ":" in parts[0]:
                parts = parts[1:]
            if not parts:
                continue
            target = os.path.join(dest, *parts)
            if not os.path.realpath(target).startswith(root + os.sep):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(m)
            if src is None:
                continue
            with src, open(target, "wb") as f:
                shutil.copyfileobj(src, f)
            n += 1
    return n


def _run(cmd, timeout=1800):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return False, "超时"
    except OSError as e:
        return False, str(e)
    msg = (r.stderr or r.stdout or b"").decode("utf-8", "replace").strip()
    if not msg and r.returncode:
        msg = (r.stdout or b"").decode(sys.getfilesystemencoding() or "utf-8", "replace").strip()
    return r.returncode == 0, msg[-400:]


def _py7zr(path, dest):
    import py7zr
    with py7zr.SevenZipFile(path, "r") as z:
        z.extractall(dest)


def extract(path, dest, log=None):
    """把 7z / rar / tar 压缩包解到 dest，返回解出的文件数。"""
    k = kind(path)
    if k is None:
        raise ValueError("不是支持的压缩包：%s" % os.path.basename(path))
    os.makedirs(dest, exist_ok=True)
    if k == "tar":
        return _safe_tar(path, dest)
    t = tools()
    src = os.path.abspath(path)
    tries = []
    if t["7z"]:
        # -p 给个假密码：有密码的包直接失败，不会停在「输入密码」
        tries.append(("7-Zip", [t["7z"], "x", "-y", "-bd", "-pnopassword", "-o" + dest, "--", src]))
    if t["unrar"] and k == "rar":
        tries.append(("WinRAR", [t["unrar"], "x", "-y", "-o+", "-p-", "-idq", src, dest + os.sep]))
    if t["bandizip"]:
        tries.append(("Bandizip", [t["bandizip"], "x", "-y", "-o:" + dest, src]))
    if t["bsdtar"]:
        tries.append(("tar", [t["bsdtar"], "-xf", src, "-C", dest]))
    errors = []
    for name, cmd in tries:
        ok, msg = _run(cmd)
        n = _count_and_check(dest)
        if ok and n:
            if log:
                log("用 %s 解压" % name, "info")
            return n
        errors.append("%s：%s" % (name, msg or "没有解出文件"))
        for x in os.listdir(dest):               # 解了一半的清掉再换下一个工具
            p = os.path.join(dest, x)
            shutil.rmtree(p, ignore_errors=True) if os.path.isdir(p) else os.remove(p)
    if k == "7z" and t["py7zr"]:
        try:
            _py7zr(src, dest)
            n = _count_and_check(dest)
            if n:
                return n
        except Exception as e:  # noqa: BLE001
            errors.append("py7zr：%s" % e)
    hint = ("没有找到能解 %s 的工具，请安装 7-Zip（https://www.7-zip.org/）后重试，"
            "或者先手动解压再把文件夹拖进来" % k)
    if errors:
        hint = ("解压失败。常见原因：有密码、分卷不全、文件损坏；只用 Windows 自带的 tar 时，"
                "文件名里有系统编码（简体中文系统是 GBK）以外的字符、或者是 Windows 10 上 LZMA 压缩的 7z "
                "都解不了——装 7-Zip（https://www.7-zip.org/）可以解决：\n  " + "\n  ".join(errors))
    raise RuntimeError(hint)
