# -*- coding: utf-8 -*-
r"""Windows 超长路径（MAX_PATH 260）兼容层 —— 纯标准库。

问题
----
Windows 默认把路径限制在 260 字符（MAX_PATH）。Unity 资源包解出来的目录树
本身就很深，例如::

    Assets/NFD_Costume/HatsukoiVampire/Gimmick/Resource/Animator/
    Animation/Toggle/Accessory/NeckRibbon/Accessory_NeckRibbon_ON.anim

再叠上「输出目录 / 文件名_日期_序号 / 文件名_extracted」这几层，很容易超过
260。一旦超出，open() / os.makedirs() 就抛::

    FileNotFoundError: [Errno 2] No such file or directory: 'F:\...'

而且报错里那串路径看着完全正常，非常难排查。

注意：这**不是中文路径的问题**。Python 3 的 Unicode 路径本来就是好的，中文、
日文、空格、方括号、全角符号都没问题，纯粹是「总长度」超限。所以同一台机器
上路径浅就能转、路径深就报错，换台电脑因为存放位置不同结果又不一样。

绕过办法
--------
给路径加 ``\?\`` 前缀（Win32 扩展长度路径），直接跳过 MAX_PATH 检查。它
**不依赖**注册表 LongPathsEnabled，也不需要管理员权限或重启，所以在别人的
电脑上同样有效。

本模块把常用的文件 API 包一层：
  * 路径短（< 240）→ 原样透传，行为和以前完全一致；
  * 路径长        → 自动补 ``\?\``。

因此对现有用户零影响，只在「本来必然失败」的长路径上生效。

用法
----
``import longpath`` 即可，模块导入时自动安装（重复导入无副作用）。
"""

import builtins
import os
import os.path as _path
import shutil

# 文件路径上限 260、目录上限 248。这里留余量：超过 240 就上前缀，
# 免得刚好卡在 258 那种「有时行有时不行」的位置。
_THRESHOLD = 240

_PREFIX = '\\\\?\\'          # \?\
_UNC_PREFIX = '\\\\?\\UNC\\'  # \?\UNC\

_installed = False
_originals = {}


def long_path(path):
    r"""路径过长时返回带 ``\?\`` 前缀的绝对路径；否则原样返回。

    短路径一律原样返回（连 abspath 都不做），保证不改变任何现有行为。
    """
    if os.name != 'nt':
        return path
    if not isinstance(path, str) or not path:
        return path                      # 只处理 str；fd / PathLike 原样放行
    if path.startswith(_PREFIX):
        return path                      # 已经加过前缀，别重复加
    if len(path) < _THRESHOLD:
        return path                      # 短路径不碰
    try:
        p = _path.abspath(path)
    except (OSError, ValueError):
        return path
    if len(p) < _THRESHOLD:
        return path
    if p.startswith('\\\\'):
        # UNC：\\server\share\x → \?\UNC\server\share\x
        return _UNC_PREFIX + p[2:]
    return _PREFIX + p


def _install():
    """给 os / builtins / shutil 的相关函数套上长路径包装。幂等。"""
    global _installed
    if _installed or os.name != 'nt':
        return
    _installed = True

    # ---- 单路径参数 ------------------------------------------------------
    for name in ('makedirs', 'mkdir', 'rmdir', 'remove', 'unlink',
                 'stat', 'lstat', 'chmod', 'utime', 'listdir', 'scandir',
                 'access', 'truncate', 'readlink', 'startfile'):
        fn = getattr(os, name, None)
        if fn is None:
            continue
        _originals[name] = fn

        def make(fn):
            def wrapper(path, *a, **kw):
                return fn(long_path(path), *a, **kw)
            return wrapper
        setattr(os, name, make(fn))

    # ---- 双路径参数（源、目标都要处理）------------------------------------
    for name in ('rename', 'replace'):
        fn = getattr(os, name, None)
        if fn is None:
            continue
        _originals[name] = fn

        def make2(fn):
            def wrapper(src, dst, *a, **kw):
                return fn(long_path(src), long_path(dst), *a, **kw)
            return wrapper
        setattr(os, name, make2(fn))

    # ---- 内置 open -------------------------------------------------------
    _originals['open'] = builtins.open

    def _open(file, *a, **kw):
        return _originals['open'](long_path(file), *a, **kw)
    builtins.open = _open

    # ---- shutil：显式包一层（虽然底层最终也会走到上面的 open/stat）---------
    for name in ('copyfile', 'copy', 'copy2', 'copymode', 'copystat',
                 'rmtree'):
        fn = getattr(shutil, name, None)
        if fn is None:
            continue
        _originals['shutil.' + name] = fn

        def makes(fn):
            def wrapper(path, *a, **kw):
                return fn(long_path(path), *a, **kw)
            return wrapper
        setattr(shutil, name, makes(fn))

    for name in ('move',):
        fn = getattr(shutil, name, None)
        if fn is None:
            continue
        _originals['shutil.' + name] = fn

        def makes2(fn):
            def wrapper(src, dst, *a, **kw):
                return fn(long_path(src), long_path(dst), *a, **kw)
            return wrapper
        setattr(shutil, name, makes2(fn))


_install()


def probe(base):
    """自检：在 base 下建一条超长路径并写入/读回，返回 (是否成功, 说明)。

    给界面或命令行排查用；平时不调用。
    """
    deep = os.path.join(base, '_longpath_probe')
    while len(deep) < 300:
        deep = os.path.join(deep, 'LongSegmentName_0123456789')
    target = os.path.join(deep, 'probe.txt')
    try:
        os.makedirs(deep, exist_ok=True)
        with open(target, 'w', encoding='utf-8') as f:
            f.write('ok')
        with open(target, encoding='utf-8') as f:
            got = f.read()
        return got == 'ok', '长度 %d，写入并读回成功' % len(target)
    except Exception as e:                                  # pragma: no cover
        return False, '%s: %s' % (type(e).__name__, e)


if __name__ == '__main__':                                  # pragma: no cover
    import sys
    base = sys.argv[1] if len(sys.argv) > 1 else os.getcwd()
    ok, msg = probe(base)
    print(('[OK]  ' if ok else '[FAIL] ') + msg)
