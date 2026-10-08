# -*- coding: utf-8 -*-
"""Unity 工程 / 解开的 unitypackage 里的材质 → 底色贴图。

Unity 导出的 FBX 常常不带贴图文件名（Texture 节点的 FileName 是空的），贴图是在 Unity 的
材质（.mat，YAML 文本）里按 GUID 指定的：

    m_Name: mat_Body
    m_TexEnvs:  _MainTex / _Main_Texture / _BaseMap … → m_Texture: {fileID: 2800000, guid: 1db2…}

而 GUID 写在贴图旁边的 .meta 里（`guid: 1db2…`）。这里把两边对起来：材质名 → 贴图路径。
FBX 里的材质名和 .mat 的 m_Name 一致（Unity 导入 FBX 时按材质名找 / 建 .mat）。

新旧两种 .mat 序列化格式都认：
  * Unity 5+：  m_TexEnvs:\\n    - _MainTex:\\n        m_Texture: {…}
  * Unity 4.x： m_TexEnvs:\\n      data:\\n        first:\\n          name: _MainTex\\n        second:\\n          m_Texture: {…}
二进制序列化的 .mat 读不了，跳过。
"""
import os
import re

IMAGE_EXT = (".png", ".tga", ".jpg", ".jpeg", ".bmp", ".dds", ".psd", ".tif", ".tiff")
# 当底色用的槽位，按优先级
MAIN_SLOTS = ("_maintex", "_main_texture", "_basemap", "_basecolormap", "_basetexture", "_albedo",
              "_albedomap", "_diffuse", "_diffusemap", "_colortex", "_tex")
NOT_MAIN = ("normal", "bump", "spec", "metal", "rough", "occlusion", "emission", "emissive", "mask",
            "matcap", "sphere", "ramp", "shade", "outline", "rim", "detail", "parallax", "height", "noise")

_GUID = re.compile(r"guid:\s*([0-9a-fA-F]{32})")
_NAME = re.compile(r"^\s*m_Name:\s*(.*?)\s*$", re.M)


def find_root(start, max_up=8):
    """Unity 资源的根：往上找 Assets 文件夹；没有（解开的 unitypackage 去掉了 Assets/）时，
    只要上一层还像 Unity 资源文件夹（有 .meta 文件）就继续往上，最后至少往上两层。"""
    d = os.path.abspath(start)
    for _ in range(max_up):
        if os.path.basename(d).lower() == "assets":
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    d = os.path.abspath(start)
    for k in range(max_up):
        parent = os.path.dirname(d)
        if parent == d:
            break
        try:
            unityish = any(n.lower().endswith(".meta") for n in os.listdir(parent))
        except OSError:
            unityish = False
        if not unityish and k >= 2:
            break
        d = parent
    return d


def _walk(root, limit):
    n = 0
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if x.lower() not in ("library", "temp", "obj", ".git", "node_modules")]
        for f in files:
            n += 1
            if n > limit:
                return
            yield d, f


def _slots(text):
    """→ [(槽位名, guid)]，按文件里的顺序。"""
    out = []
    i = text.find("m_TexEnvs:")
    if i < 0:
        return out
    end = len(text)
    for key in ("m_Floats:", "m_Ints:", "m_Colors:"):
        j = text.find(key, i)
        if j > 0:
            end = min(end, j)
    block = text[i:end]
    # Unity 5+：  - _MainTex:      Unity 4：  name: _MainTex
    marks = [(m.start(), m.group(1)) for m in re.finditer(r"^\s*-\s*([A-Za-z_][\w]*):\s*$", block, re.M)]
    marks += [(m.start(), m.group(1)) for m in re.finditer(r"^\s*name:\s*([A-Za-z_][\w]*)\s*$", block, re.M)]
    marks.sort()
    for k, (pos, name) in enumerate(marks):
        seg = block[pos:marks[k + 1][0] if k + 1 < len(marks) else len(block)]
        m = re.search(r"m_Texture:\s*\{[^}]*\}", seg)
        if not m:
            continue
        g = _GUID.search(m.group(0))          # 空槽位是 {fileID: 0}，没有 guid
        if g:
            out.append((name, g.group(1).lower()))
    return out


def _main_guid(slots):
    if not slots:
        return None
    by = {n.lower(): g for n, g in slots}
    for s in MAIN_SLOTS:
        if s in by:
            return by[s]
    for n, g in slots:
        if not any(k in n.lower() for k in NOT_MAIN):
            return g
    return None


def material_textures(start, limit=50000):
    """→ {材质名: 底色贴图的绝对路径}。start 是 FBX 所在文件夹。找不到 Unity 资源时返回 {}。"""
    root = find_root(start)
    guid_path = {}
    mats = []
    for d, f in _walk(root, limit):
        low = f.lower()
        if low.endswith(".meta"):
            asset = f[:-5]
            if asset.lower().endswith(IMAGE_EXT):
                try:
                    with open(os.path.join(d, f), encoding="utf-8", errors="replace") as fh:
                        m = _GUID.search(fh.read(2000))
                except OSError:
                    continue
                if m and os.path.isfile(os.path.join(d, asset)):
                    guid_path[m.group(1).lower()] = os.path.join(d, asset)
        elif low.endswith(".mat"):
            mats.append(os.path.join(d, f))
    if not mats or not guid_path:
        return {}
    # 同名材质可能有好几份（每套衣服一个文件夹，各有一个 mat_Hair）。按 Unity 导入 FBX 时的
    # 查找顺序（Recursive-Up）挑：FBX 所在文件夹及其 Materials/，再往上一层……直到 Assets；
    # 都不在这条链上的（别的角色 / 别的衣服的材质）排最后
    chain = []
    d = os.path.normcase(os.path.abspath(start))
    while True:
        chain.append(d)
        if os.path.basename(d).lower() == "assets" or os.path.dirname(d) == d:
            break
        d = os.path.dirname(d)

    def rank(mat_path):
        md = os.path.normcase(os.path.dirname(os.path.abspath(mat_path)))
        for k, a in enumerate(chain):
            if md.lower() == a.lower() or md.lower() == os.path.join(a, "materials").lower():
                return k
        return 1000

    best = {}
    for p in mats:
        try:
            with open(p, "rb") as fh:
                raw = fh.read(200000)
        except OSError:
            continue
        if not raw.startswith(b"%YAML"):
            continue                     # 二进制序列化的材质
        text = raw.decode("utf-8", "replace")
        nm = _NAME.search(text)
        name = nm.group(1).strip().strip('"') if nm else os.path.splitext(os.path.basename(p))[0]
        g = _main_guid(_slots(text))
        if not (g and g in guid_path):
            continue
        r = rank(p)
        for key in {name, os.path.splitext(os.path.basename(p))[0]}:
            if key not in best or r < best[key][0]:
                best[key] = (r, guid_path[g])
    return {k: v[1] for k, v in best.items()}
