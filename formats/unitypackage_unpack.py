"""Unpack a .unitypackage without Unity.

一个 .unitypackage 只是一个经过 gzip 压缩的 tar 归档文件。每个资源都存放在一个以其 32 位十六进制 GUID 命名的文件夹中，该文件夹包含：
    asset        the real file (FBX, prefab, png, ...)
    asset.meta   Unity metadata (importer type, guid)
    pathname     original path inside Assets/
    preview.png  optional thumbnail

解包后的落盘规则：
    asset       → <输出目录>/<Assets 里的相对路径>          例：.../FBX/model.fbx
    辅助文件    → 与资产同层，名字带上资产名                例：.../FBX/model.fbx.meta
                  （asset.meta → <名>.meta，pathname / preview.png → <名>.pathname 等）
    文件夹资源  → 建出同名空文件夹（tar 里没有它的 asset 本体）
辅助文件之所以带上资产名，是因为同一目录下常有两个以上资产，若统一叫
asset.meta / pathname 就会互相覆盖，只剩最后一个。

Usage:
    python unitypackage_unpack.py <file.unitypackage> [-o OUTDIR] [--list]
	
# 先看包里有什么（不解包）
python unitypackage_unpack.py "Sexy Sailor_Sapphy_v1.00.unitypackage" --list

# 解包到默认目录（<文件名>_extracted）
python unitypackage_unpack.py "Sexy Sailor_Sapphy_v1.00.unitypackage"

# 解包到指定目录
python unitypackage_unpack.py "Sexy Sailor_Sapphy_v1.00.unitypackage" -o "D:/output/Sapphy"

只要路径里带空格或中文，就套一层双引号，否则 shell 会按空格切参数。
"""
import argparse
import os
import shutil
import sys
import tarfile

# Windows 超长路径支持：Unity 包里的目录树很深，再叠上输出目录很容易超 260
# 字符而报 FileNotFoundError。这个模块导入即生效，短路径行为不变。
# 单独把本文件拷出去用时可能找不到，所以吞掉 ImportError。
try:
    import longpath  # noqa: F401
except ImportError:                                  # pragma: no cover
    pass


def _members(src):
    """按包里的顺序逐个给出 (GUID, 文件名, 内容)，整个包只解压一遍（流式读 tar）。

    以前先 getmembers() 走一遍、再按成员回头读 pathname、再逐个解出：gzip 不能往回跳，
    每次回头都从头再解，161 MB 的包要解三遍。"""
    with tarfile.open(src, 'r|gz') as t:
        for m in t:
            if m.isfile():
                guid, fn = m.name.split('/', 1)
                yield guid, fn, t.extractfile(m).read()


def _target(outdir, raw, guid, fn):
    """一个成员的落盘路径，以及它是不是「文件夹」资源的辅助文件（要建出空文件夹）。"""
    # pathname 以 '/' 结尾 = 这是一个「文件夹」资源（Unity 里文件夹也是资源）。
    # tar 里没有它的 asset 本体，只有 asset.meta / pathname / preview.png。
    is_folder = raw.endswith('/') or not raw or raw == guid
    # 去掉开头的 "Assets/"（以前用 lstrip('Assets/')，那是按字符集剥：
    # "Assets/shaders/x" 会被剥成 "haders/x"）
    rel = (raw[len('Assets/'):] if raw.startswith('Assets/') else raw).rstrip('/') or guid
    if fn == 'asset':
        return os.path.join(outdir, rel), rel, False
    # asset.meta / pathname / preview.png 是辅助文件：跟资产放在同一层，
    # 名字里带上资产名（asset.meta 按 Unity 习惯写成 <名>.meta）。
    # 早先直接写 <父目录>/<文件名>，同一目录下有两个以上资产时，
    # 后一个会把前一个**静默覆盖**（实测 172 个输出里 51 个碰撞、
    # 168 个成员被覆盖，其中 92 个 asset.meta + 92 个 pathname）。
    name = os.path.basename(rel) or guid
    aux = name + '.meta' if fn == 'asset.meta' else name + '.' + fn
    return os.path.join(outdir, os.path.dirname(rel), aux), rel, is_folder


def unpack(src, outdir, list_only=False, verbose=True):
    """解包到 outdir；verbose=False 时不逐个列出文件（界面 / 流水线自己记一行汇总）。

    一个资源的成员在包里是挨着的（asset、asset.meta、pathname、preview.png），落盘路径要等
    读到 pathname 才知道：之前的成员先留在内存里（最多一个资源的大小），读到它再按原顺序写出。"""
    if list_only:
        paths, sizes = {}, []
        for guid, fn, data in _members(src):
            if fn == 'pathname':
                paths[guid] = data.decode('utf-8', 'replace').strip()
            elif fn == 'asset':
                sizes.append((guid, len(data)))
        for guid, size in sizes:
            print('%10d  %s' % (size, paths.get(guid, '?')))
        return 0

    if os.path.isdir(outdir):
        shutil.rmtree(outdir)
    os.makedirs(outdir)
    paths, pending = {}, {}
    n = 0

    def write(guid, fn, data):
        dst, rel, folder = _target(outdir, paths.get(guid, ''), guid, fn)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if folder:
            # 文件夹资源的 asset 本体不存在，不在这里建的话空文件夹会丢
            os.makedirs(os.path.join(outdir, rel), exist_ok=True)
        with open(dst, 'wb') as w:
            w.write(data)
        if verbose:
            print('  %8.1f KB  %s' % (len(data) / 1024, os.path.relpath(dst, outdir)))

    for guid, fn, data in _members(src):
        if fn == 'pathname':
            paths[guid] = data.decode('utf-8', 'replace').strip()
        if guid in paths:
            for item in pending.pop(guid, ()):
                write(guid, *item)
            write(guid, fn, data)
        else:
            pending.setdefault(guid, []).append((fn, data))
        n += 1
    for guid, items in pending.items():          # 没有 pathname 的资源：按 GUID 落盘
        for item in items:
            write(guid, *item)
    if verbose:
        print('\n%d files -> %s' % (n, outdir))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('-o', '--out', default=None)
    ap.add_argument('--list', action='store_true')
    a = ap.parse_args()
    out = a.out or os.path.splitext(a.src)[0] + '_extracted'
    return unpack(a.src, out, a.list)


if __name__ == '__main__':
    sys.exit(main())
