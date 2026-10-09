# -*- coding: utf-8 -*-
"""XPS / XNALara 模型读取器（纯 Python 标准库，不需要 Blender / XPS 本体）。

格式来源
--------
XNALara（后被 XPS / XNA Posing Studio 继承）的二进制模型格式。字段布局以
GLLara 的公开文档 `Documentation/XNALara Model File Format.md` 为准，并由本项目
实测样本（轩辕剑柒 chu hong，XPS 11.8 导出）逐字段复核过。

两种变体
--------
1) **Generic Item 2**（XPS 自己的格式，现在最常遇到）
   第一个 uint32 是魔数 323232（0x0004EEA0），据此识别：

       uint32   magic = 323232
       uint16   majorVersion
       uint16   minorVersion
       string   toolAuthor          // 固定 "XNAaraL"
       uint32   countOfUnknownInts
       string   unknownString1      // 作者名 / 机器名，不需要
       string   unknownString2
       string   unknownString3      // 含原始文件路径
       uint32   unknownInts[countOfUnknownInts]
       uint32   numBones
       Bone     bones[numBones]
       uint32   numMeshes
       Mesh     meshes[numMeshes]
       ...                           // 之后还有一段与转换无关的尾部数据

2) **旧版 XNALara 二进制**（没有魔数，直接就是 numBones）
   结构与上面相同，只是没有 header 那一段；顶点里**多一套切线**。

   要判断是哪种，只看文件头 4 字节是不是 323232 即可（文档明确这么写）。

骨骼
----
    string   name
    uint16   parentIndex          // 65535(0xFFFF) = 没有父级
    float    defaultPositionX     // **世界坐标**，不是相对父级的位移
    float    defaultPositionY
    float    defaultPositionZ

网格
----
    string   name
    uint32   numUVLayers
    uint32   numTextures
    Texture  textures[numTextures]      // { string filename; uint32 uvLayer; }
    uint32   numVertices
    Vertex   vertices[numVertices]
    uint32   numTriangles               // ⚠ 是**三角面数**，不是索引数
    uint32   indices[numTriangles * 3]  // 索引数组长度是三角面数的 3 倍

顶点（随版本/UV 层数/是否有骨骼而变）
-------------------------------------
    通用项：
        float    vertex[3]
        float    normal[3]
        uint8    color[4]
        float    texCoord[2][numUVLayers]
    只有旧版 XNALara（非 Generic Item 2）**且** majorVersion < 2 时才有：
        float    tangent[4][numUVLayers]
    有骨骼时：
        // Generic Item 2 且 majorVersion >= 3：
        uint16   boneCountForVertex          // 每顶点独立，通常 4，也可能更高
        uint16   boneIndex[boneCountForVertex]
        float    boneWeight[boneCountForVertex]
        // 其余情况：固定 4 根
        uint16   boneIndex[4]
        float    boneWeight[4]

实测记录（轩辕剑柒 chu hong，majorVersion=3，minorVersion=15）
-----------------------------------------------------------
* 136 根骨骼 / 9 个网格 / 57415 顶点 / 82592 三角面，全部自洽。
* 每个顶点 boneCount 恒为 4，顶点步长 62 字节（1 套 UV）。
* 轴向实测：`head eyeball left` 在 +X、`head eyeball right` 在 -X → **+X = 左**；
  `head nose tip` 与 `leg left toes` 的 Z 都**大于**各自的父级（鼻尖 1.030 > 头根
  0.925，脚尖 0.974 > 脚踝 0.879）→ **+Z = 正面**；`root ground` 在 y=0、头顶
  y≈1.71（米）→ **+Y = 上**。也就是「左手 +X、正面 +Z、上 +Y」的**右手系**。
  MMD/PMX 是「左手 +X、正面 -Z、上 +Y」，所以只要**把 Z 取反**即可对齐
  （详见 convert/xps2pmx.py 里 xf() 的说明）。
* 尾部还有 129 字节与模型无关的数据，读完最后一个网格直接停即可。
"""
import os
import struct

__all__ = ["Xps", "XpsBone", "XpsMesh", "read_xps", "XPS_MAGIC"]

# Generic Item 2 的魔数：0x0004EEA0 == 323232
XPS_MAGIC = 323232

# 顶点里索引/权重最多取多少根（PMX 上限就是 4，这里读进来后由转换器再裁）
MAX_BONES_PER_VERTEX = 64


class XpsBone(object):
    __slots__ = ("name", "parent_index", "position")

    def __init__(self, name, parent_index, position):
        self.name = name
        self.parent_index = parent_index      # 65535 = 无父级
        self.position = position              # (x, y, z) 世界坐标


class XpsMesh(object):
    __slots__ = ("name", "uv_layers", "textures", "positions", "normals",
                 "colors", "uvs", "bone_indices", "bone_weights",
                 "triangles", "bone_counts")

    def __init__(self, name, uv_layers):
        self.name = name
        self.uv_layers = uv_layers
        self.textures = []          # [(filename, uv_layer), ...]
        self.positions = []         # [(x, y, z), ...]
        self.normals = []           # [(x, y, z), ...]
        self.colors = []            # [(r, g, b, a), ...]
        self.uvs = []               # [(u, v), ...] 第 0 套（MMD 只吃第 0 套）
        self.bone_indices = []      # [[bone_index, ...], ...]
        self.bone_weights = []      # [[weight, ...], ...]
        self.bone_counts = []       # [int, ...] 每个顶点的权重根数
        self.triangles = []         # [(i0, i1, i2), ...]

    @property
    def n_vertices(self):
        return len(self.positions)

    @property
    def n_triangles(self):
        return len(self.triangles)

    @property
    def diffuse(self):
        """基础色贴图文件名：XPS 约定 textures[0] 就是 diffuse。

        实测样本里 4 张一组时顺序是 `_d / _l / _n / _s`（漫反射 / 光照图 /
        法线 / 高光），只有 1 张时就是 `_d`，所以取第 0 个总是对的。
        """
        return self.textures[0][0] if self.textures else ""


class Xps(object):
    def __init__(self):
        self.magic = 0
        self.version_major = 0
        self.version_minor = 0
        self.author = ""
        self.paths = []             # header 里那三条字符串（作者名/机器名/路径）
        self.is_generic2 = False    # True = XPS 的 Generic Item 2
        self.bones = []             # [XpsBone, ...]
        self.meshes = []            # [XpsMesh, ...]
        self.tail = b""             # 网格之后的剩余字节（与转换无关）

    @property
    def n_vertices(self):
        return sum(len(m.positions) for m in self.meshes)

    @property
    def n_triangles(self):
        return sum(len(m.triangles) for m in self.meshes)


# ------------------------------------------------------------------ 读取 ------
class _R(object):
    """把整份文件读进内存后按偏移取数（比逐字段 read() 快得多）。"""

    __slots__ = ("d", "o", "n")

    def __init__(self, data):
        self.d = data
        self.o = 0
        self.n = len(data)

    def need(self, k):
        if self.o + k > self.n:
            raise ValueError("文件在偏移 %d 处提前结束（还需 %d 字节，仅剩 %d）"
                             % (self.o, k, self.n - self.o))

    def u8(self):
        v = self.d[self.o]
        self.o += 1
        return v

    def u16(self):
        self.need(2)
        v = struct.unpack_from("<H", self.d, self.o)[0]
        self.o += 2
        return v

    def u32(self):
        self.need(4)
        v = struct.unpack_from("<I", self.d, self.o)[0]
        self.o += 4
        return v

    def vec(self, k):
        self.need(4 * k)
        v = struct.unpack_from("<%df" % k, self.d, self.o)
        self.o += 4 * k
        return v

    def raw(self, k):
        self.need(k)
        v = self.d[self.o:self.o + k]
        self.o += k
        return v

    def string(self):
        """XPS 的字符串：长度用「每字节 7 位、低位在前」的变长编码，再接原文。

        文档原话：`Only the bottom seven bits of a byte are considered. If the
        top bit is set, the next byte is length too.` 实测 'XNAaraL' 是单字节
        长度 7；152 字节的长路径是 `0x98 0x01` → 0x18 | (1<<7) = 152，确认无误。
        字符串不带结束符。
        """
        ln = 0
        shift = 0
        while True:
            b = self.u8()
            ln |= (b & 0x7f) << shift
            if not (b & 0x80):
                break
            shift += 7
            if shift > 28:
                raise ValueError("字符串长度编码异常（偏移 %d）" % (self.o - 1))
        if ln == 0:
            return ""
        raw = self.raw(ln)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("latin-1")


def _read_bones(r):
    n = r.u32()
    if n > 100000:
        raise ValueError("骨骼数异常（%d），文件可能不是 XPS 或已损坏" % n)
    bones = []
    for _ in range(n):
        name = r.string()
        parent = r.u16()
        pos = r.vec(3)
        bones.append(XpsBone(name, 0xFFFF if parent == 0xFFFF else parent, pos))
    return bones


def _read_vertex(r, nuv, has_bones, has_tangent, variable_bone_count):
    """读一个顶点，返回 (pos, nrm, col, uv0, idxs, wts)。"""
    pos = r.vec(3)
    nrm = r.vec(3)
    col = struct.unpack_from("<4B", r.d, r.o)
    r.o += 4
    uvs = r.vec(2 * nuv) if nuv else ()
    if has_tangent:
        r.o += 16 * nuv                     # 旧版才有切线，MMD 用不上，跳过
    idxs = ()
    wts = ()
    if has_bones:
        if variable_bone_count:
            bc = r.u16()
            if bc > MAX_BONES_PER_VERTEX:
                raise ValueError("顶点权重根数异常（%d）" % bc)
        else:
            bc = 4
        if bc:
            r.need(2 * bc + 4 * bc)
            idxs = struct.unpack_from("<%dH" % bc, r.d, r.o)
            r.o += 2 * bc
            wts = struct.unpack_from("<%df" % bc, r.d, r.o)
            r.o += 4 * bc
    uv0 = (uvs[0], uvs[1]) if nuv else (0.0, 0.0)
    return pos, nrm, col, uv0, idxs, wts


def _read_meshes(r, has_bones, has_tangent, variable_bone_count, log):
    n_mesh = r.u32()
    if n_mesh > 100000:
        raise ValueError("网格数异常（%d）" % n_mesh)
    meshes = []
    for mi in range(n_mesh):
        name = r.string()
        nuv = r.u32()
        ntex = r.u32()
        if nuv > 8 or ntex > 64:
            raise ValueError("网格 %d 的 UV 层数/贴图数异常（%d / %d）"
                             % (mi, nuv, ntex))
        mesh = XpsMesh(name, nuv)
        for _ in range(ntex):
            fn = r.string()
            ul = r.u32()
            mesh.textures.append((fn, ul))
        nv = r.u32()
        if nv > 40000000:
            raise ValueError("网格 %d 的顶点数异常（%d）" % (mi, nv))
        mesh.positions = [None] * nv
        mesh.normals = [None] * nv
        mesh.colors = [None] * nv
        mesh.uvs = [None] * nv
        mesh.bone_indices = [None] * nv
        mesh.bone_weights = [None] * nv
        mesh.bone_counts = [0] * nv
        for vi in range(nv):
            pos, nrm, col, uv0, idxs, wts = _read_vertex(
                r, nuv, has_bones, has_tangent, variable_bone_count)
            mesh.positions[vi] = pos
            mesh.normals[vi] = nrm
            mesh.colors[vi] = col
            mesh.uvs[vi] = uv0
            mesh.bone_indices[vi] = idxs
            mesh.bone_weights[vi] = wts
            mesh.bone_counts[vi] = len(idxs)
        ntri = r.u32()
        if ntri > 40000000:
            raise ValueError("网格 %d 的三角面数异常（%d）" % (mi, ntri))
        r.need(12 * ntri)
        flat = struct.unpack_from("<%dI" % (3 * ntri), r.d, r.o)
        r.o += 12 * ntri
        if nv:
            bad = 0
            tris = []
            for k in range(0, len(flat), 3):
                a, b, c = flat[k], flat[k + 1], flat[k + 2]
                if a >= nv or b >= nv or c >= nv:
                    bad += 1
                    continue
                tris.append((a, b, c))
            mesh.triangles = tris
            if bad and log:
                log("网格 %d 有 %d 个三角面索引越界，已跳过" % (mi, bad), "warn")
        meshes.append(mesh)
    return meshes


def read_xps(path, log=None):
    """读取 .xps（XPS Generic Item 2 或旧版 XNALara 二进制）→ Xps。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 8:
        raise ValueError("文件太小，不是 XPS 模型")
    r = _R(data)
    m = Xps()
    m.magic = r.u32()
    m.is_generic2 = (m.magic == XPS_MAGIC)

    if m.is_generic2:
        m.version_major = r.u16()
        m.version_minor = r.u16()
        m.author = r.string()
        n_unk = r.u32()
        if n_unk > 1000000:
            raise ValueError("XPS 头部异常（countOfUnknownInts=%d）" % n_unk)
        m.paths = [r.string(), r.string(), r.string()]
        r.o += 4 * n_unk                       # 一段与模型无关的整数设置
        has_bones = True
    else:
        # 旧版 XNALara：没有 header，第一个 uint32 就是骨骼数。
        # 把已经读掉的 4 字节退回去，后面按同一套结构读。
        r.o = 0
        m.version_major = 1
        has_bones = True
        if log:
            log("XPS 格式：旧版 XNALara 二进制格式（无 Generic Item 2 头）", "info")

    # 切线：只有旧版 XNALara 且 majorVersion < 2 才有
    has_tangent = (not m.is_generic2) and m.version_major < 2
    # 变长权重根数：Generic Item 2 且 majorVersion >= 3
    variable_bone_count = m.is_generic2 and m.version_major >= 3

    if log:
        if m.is_generic2:
            log("XPS 格式：Generic Item 2 · 版本 %d.%d · 作者 %s"
                % (m.version_major, m.version_minor, m.author or "?"), "info")
        if has_tangent:
            log("XPS 格式：旧版格式含切线数据（转换时忽略）", "info")

    m.bones = _read_bones(r)
    if not m.bones:
        raise ValueError("文件中没有骨骼数据")
    m.meshes = _read_meshes(r, has_bones, has_tangent, variable_bone_count, log)
    m.tail = data[r.o:]
    if not m.meshes:
        raise ValueError("文件中没有网格数据")
    return m


# ------------------------------------------------------------------- CLI ------
def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="查看 XPS / XNALara 模型结构")
    ap.add_argument("src")
    a = ap.parse_args(argv)
    m = read_xps(a.src, log=lambda s, t=None: print(s))
    print("骨骼 %d · 网格 %d · 顶点 %d · 三角面 %d"
          % (len(m.bones), len(m.meshes), m.n_vertices, m.n_triangles))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
