# -*- coding: utf-8 -*-
"""vrmio.py - GLB / glTF 容器读写 + 纯 Python 图片编解码。

只依赖标准库。提供：
    GLBBuilder   —— 构造 glTF JSON + 单一 BIN chunk
    read_glb     —— 拆开 .vrm/.glb 得到 (json dict, bin bytes)
    write_glb    —— 打包成 .vrm/.glb
    to_png / decode_image / save_image —— 贴图处理
"""
import json
import os
import struct
import zlib

GLB_MAGIC = 0x46546C67          # "glTF"
CHUNK_JSON = 0x4E4F534A         # "JSON"
CHUNK_BIN = 0x004E4942          # "BIN\0"

FLOAT = 5126
UNSIGNED_BYTE = 5121
SHORT = 5122
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125

_COMP_FMT = {FLOAT: "<f", UNSIGNED_BYTE: "<B", SHORT: "<h",
             UNSIGNED_SHORT: "<H", UNSIGNED_INT: "<I"}
_COMP_SIZE = {FLOAT: 4, UNSIGNED_BYTE: 1, SHORT: 2,
              UNSIGNED_SHORT: 2, UNSIGNED_INT: 4}
_TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4,
               "MAT4": 16}


# --------------------------------------------------------------- glb build --
class GLBBuilder:
    """边填数据边生成 bufferViews / accessors / images。"""

    def __init__(self):
        self.bin = bytearray()
        self.bufferViews = []
        self.accessors = []
        self.images = []            # (mime, bytes) 占位，稍后统一写入
        self.samplers = []
        self.textures = []

    # -- raw
    def add_bytes(self, data, target=None):
        while len(self.bin) % 4:
            self.bin.append(0)
        off = len(self.bin)
        self.bin += data
        bv = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
        if target:
            bv["target"] = target
        self.bufferViews.append(bv)
        return len(self.bufferViews) - 1

    # -- accessor
    def add_accessor(self, values, comp_type, type_str,
                     target=None, minmax=True):
        """values: 展平的 python 数字列表。"""
        n = _TYPE_COUNT[type_str]
        count = len(values) // n
        fmt = _COMP_FMT[comp_type]
        data = struct.pack("<%d%s" % (len(values), fmt[1:]), *values)
        bv = self.add_bytes(data, target)
        acc = {"bufferView": bv, "componentType": comp_type,
               "count": count, "type": type_str}
        if minmax and count:
            lo = [min(values[i::n]) for i in range(n)]
            hi = [max(values[i::n]) for i in range(n)]
            if comp_type == UNSIGNED_SHORT:
                acc["min"] = [int(x) & 0xFFFF for x in lo]
                acc["max"] = [int(x) & 0xFFFF for x in hi]
            else:
                acc["min"] = [float(x) for x in lo]
                acc["max"] = [float(x) for x in hi]
        self.accessors.append(acc)
        return len(self.accessors) - 1

    def add_sparse_accessor(self, count, indices, values, type_str):
        """稀疏 FLOAT accessor：没列出的元素一律为 0。

        indices: 非零元素的下标（升序）；values: 对应的展平值。
        用于 morph target——大多数表情只动几百个顶点，密集存储
        每个表情都要「顶点数 × 12 字节」，稀疏存储只存动到的那些。
        glTF 要求 sparse.count ≥ 1，全零的 target 也写 1 个零元素。
        POSITION 类 accessor 必须带 min/max，按「含隐式 0」的全集计算。
        """
        n = _TYPE_COUNT[type_str]
        if not indices:
            indices, values = [0], [0.0] * n
        ib = self.add_bytes(struct.pack("<%dI" % len(indices), *indices))
        vb = self.add_bytes(struct.pack("<%df" % len(values), *values))
        lo = [min([0.0] + values[i::n]) for i in range(n)]
        hi = [max([0.0] + values[i::n]) for i in range(n)]
        self.accessors.append({
            "componentType": FLOAT, "count": count, "type": type_str,
            "min": lo, "max": hi,
            "sparse": {"count": len(indices),
                       "indices": {"bufferView": ib,
                                   "componentType": UNSIGNED_INT},
                       "values": {"bufferView": vb}}})
        return len(self.accessors) - 1

    # -- image / texture
    def add_image(self, data, mime):
        bv = self.add_bytes(data)
        self.images.append({"bufferView": bv, "mimeType": mime})
        return len(self.images) - 1

    def add_texture(self, image_index, sampler=0):
        self.textures.append({"sampler": sampler, "source": image_index})
        return len(self.textures) - 1


def default_sampler():
    return {"magFilter": 9729, "minFilter": 9987,
            "wrapS": 10497, "wrapT": 10497}


def write_glb(gltf, bin_bytes, path):
    js = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    while len(js) % 4:
        js += b" "
    b = bytes(bin_bytes)
    while len(b) % 4:
        b += b"\x00"
    total = 12 + 8 + len(js) + (8 + len(b) if b else 0)
    out = bytearray()
    out += struct.pack("<III", GLB_MAGIC, 2, total)
    out += struct.pack("<II", len(js), CHUNK_JSON)
    out += js
    if b:
        out += struct.pack("<II", len(b), CHUNK_BIN)
        out += b
    with open(path, "wb") as f:
        f.write(out)
    return total


def read_glb(path):
    with open(path, "rb") as f:
        d = f.read()
    if len(d) < 20 or struct.unpack_from("<I", d, 0)[0] != GLB_MAGIC:
        raise ValueError("不是 GLB/VRM 文件：%s" % path)
    total = struct.unpack_from("<I", d, 8)[0]
    js = None
    bin_data = b""
    p = 12
    while p + 8 <= min(len(d), total):
        clen, ctype = struct.unpack_from("<II", d, p)
        p += 8
        chunk = d[p:p + clen]
        p += clen
        if ctype == CHUNK_JSON:
            js = json.loads(chunk.decode("utf-8"))
        elif ctype == CHUNK_BIN:
            bin_data = chunk
    if js is None:
        raise ValueError("GLB 里没有 JSON chunk")
    return js, bin_data


# ------------------------------------------------------------- gltf utils --
def read_accessor(gltf, bin_data, index):
    """把 accessor 读成展平的 python 数字列表，支持稀疏存储。"""
    acc = gltf["accessors"][index]
    return accessor_sparse(gltf, bin_data, index)


def accessor_sparse(gltf, bin_data, index):
    """返回 (values, sparse_pairs) —— 稀疏 accessor 支持。"""
    acc = gltf["accessors"][index]
    n = _TYPE_COUNT[acc["type"]]
    count = acc["count"]
    comp = acc["componentType"]
    fmt = _COMP_FMT[comp]
    size = _COMP_SIZE[comp]
    out = [0.0] * (count * n)
    if "bufferView" in acc:
        bv = gltf["bufferViews"][acc["bufferView"]]
        off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = bv.get("byteStride") or (size * n)
        for i in range(count):
            base = off + i * stride
            for k in range(n):
                out[i * n + k] = struct.unpack_from(fmt, bin_data,
                                                    base + k * size)[0]
    sp = acc.get("sparse")
    if sp:
        icount = sp["count"]
        ibv = gltf["bufferViews"][sp["indices"]["bufferView"]]
        icomp = sp["indices"]["componentType"]
        ifmt = _COMP_FMT[icomp]
        isize = _COMP_SIZE[icomp]
        ioff = ibv.get("byteOffset", 0) + sp["indices"].get("byteOffset", 0)
        vbv = gltf["bufferViews"][sp["values"]["bufferView"]]
        voff = vbv.get("byteOffset", 0) + sp["values"].get("byteOffset", 0)
        for i in range(icount):
            vi = struct.unpack_from(ifmt, bin_data, ioff + i * isize)[0]
            for k in range(n):
                out[vi * n + k] = struct.unpack_from(
                    fmt, bin_data, voff + (i * n + k) * size)[0]
    return out


def node_world_matrices(gltf):
    """返回每个 node 的世界矩阵（列主序 4x4 列表）。"""
    nodes = gltf.get("nodes") or []
    n = len(nodes)
    world = [None] * n
    parent = [-1] * n
    for i, nd in enumerate(nodes):
        for c in nd.get("children") or []:
            if 0 <= c < n:
                parent[c] = i

    def local(nd):
        if "matrix" in nd:
            return list(nd["matrix"])
        t = nd.get("translation") or [0.0, 0.0, 0.0]
        r = nd.get("rotation") or [0.0, 0.0, 0.0, 1.0]
        s = nd.get("scale") or [1.0, 1.0, 1.0]
        x, y, z, w = r
        x2, y2, z2 = x + x, y + y, z + z
        xx, xy, xz = x * x2, x * y2, x * z2
        yy, yz, zz = y * y2, y * z2, z * z2
        wx, wy, wz = w * x2, w * y2, w * z2
        # 列主序
        return [s[0] * (1 - (yy + zz)), s[0] * (xy + wz), s[0] * (xz - wy), 0.0,
                s[1] * (xy - wz), s[1] * (1 - (xx + zz)), s[1] * (yz + wx), 0.0,
                s[2] * (xz + wy), s[2] * (yz - wx), s[2] * (1 - (xx + yy)), 0.0,
                t[0], t[1], t[2], 1.0]

    def mul(a, b):
        o = [0.0] * 16
        for c in range(4):
            for r_ in range(4):
                o[c * 4 + r_] = (a[r_] * b[c * 4] + a[4 + r_] * b[c * 4 + 1] +
                                 a[8 + r_] * b[c * 4 + 2] +
                                 a[12 + r_] * b[c * 4 + 3])
        return o

    order = sorted(range(n), key=lambda i: _depth(i, parent))
    for i in order:
        loc = local(nodes[i])
        p = parent[i]
        world[i] = mul(world[p], loc) if p >= 0 and world[p] else loc
    return world


def _depth(i, parent):
    d = 0
    while parent[i] >= 0:
        d += 1
        i = parent[i]
    return d


def world_translation(m):
    return (m[12], m[13], m[14])


# ----------------------------------------------------------------- images --
def encode_png(width, height, rows_rgba):
    """rows_rgba: 每行为 bytes，长度 width*4。"""
    raw = bytearray()
    for r in rows_rgba:
        raw.append(0)
        raw += r

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height,
                                      8, 6, 0, 0, 0))
    out += chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    out += chunk(b"IEND", b"")
    return bytes(out)


def _png_from_rgba(w, h, pix):
    """pix: bytes，长度 w*h*4，按行自上而下。"""
    rows = [pix[y * w * 4:(y + 1) * w * 4] for y in range(h)]
    return encode_png(w, h, rows)


def _decode_bmp(data):
    if data[:2] != b"BM":
        return None
    off = struct.unpack_from("<I", data, 10)[0]
    dib = struct.unpack_from("<I", data, 14)[0]
    if dib < 40:
        return None
    w = struct.unpack_from("<i", data, 18)[0]
    h = struct.unpack_from("<i", data, 22)[0]
    bpp = struct.unpack_from("<H", data, 28)[0]
    comp = struct.unpack_from("<I", data, 30)[0]
    if bpp not in (24, 32) or comp not in (0, 3):
        return None
    flip = h > 0
    h = abs(h)
    stride = ((w * bpp + 31) // 32) * 4
    pix = bytearray(w * h * 4)
    src = off
    for y in range(h):
        sy = (h - 1 - y) if flip else y
        row = data[src + sy * stride: src + sy * stride + stride]
        for x in range(w):
            if bpp == 24:
                b, g, r = row[x * 3], row[x * 3 + 1], row[x * 3 + 2]
                a = 255
            else:
                b, g, r, a = row[x * 4], row[x * 4 + 1], row[x * 4 + 2], row[x * 4 + 3]
            o = (y * w + x) * 4
            pix[o] = r
            pix[o + 1] = g
            pix[o + 2] = b
            pix[o + 3] = a
    return _png_from_rgba(w, h, bytes(pix))


def _decode_tga(data):
    idlen = data[0]
    cmap_type = data[1]
    img_type = data[2]
    w = struct.unpack_from("<H", data, 12)[0]
    h = struct.unpack_from("<H", data, 14)[0]
    depth = data[16]
    desc = data[17]
    if img_type not in (2, 10) or depth not in (24, 32):
        return None
    p = 18 + idlen
    bpp = depth // 8
    pix = bytearray(w * h * 4)
    top = bool(desc & 0x20)
    idx = 0
    if img_type == 2:
        raw = data[p:p + w * h * bpp]
        for i in range(w * h):
            b = raw[i * bpp]
            g = raw[i * bpp + 1]
            r = raw[i * bpp + 2]
            a = raw[i * bpp + 3] if bpp == 4 else 255
            o = i * 4
            pix[o] = r
            pix[o + 1] = g
            pix[o + 2] = b
            pix[o + 3] = a
    else:
        while idx < w * h and p < len(data):
            rep = data[p]
            p += 1
            count = (rep & 0x7F) + 1
            if rep & 0x80:
                px = data[p:p + bpp]
                p += bpp
                chunkpx = [px] * count
            else:
                chunkpx = [data[p + i * bpp:p + (i + 1) * bpp]
                           for i in range(count)]
                p += count * bpp
            for px in chunkpx:
                if idx >= w * h:
                    break
                b, g, r = px[0], px[1], px[2]
                a = px[3] if bpp == 4 else 255
                o = idx * 4
                pix[o] = r
                pix[o + 1] = g
                pix[o + 2] = b
                pix[o + 3] = a
                idx += 1
    if not top:                                   # 底到顶存储，翻过来
        rows = [bytes(pix[y * w * 4:(y + 1) * w * 4]) for y in range(h)]
        rows.reverse()
        pix = bytearray(b"".join(rows))
    return _png_from_rgba(w, h, bytes(pix))


# ------------------------------------------------------------------- DDS ----
# XPS / XNALara 的贴图大量是 DDS（实测轩辕剑柒的样本是 2048×2048 的 DXT1 与
# 512×512 的 DXT5）。MMD 本体虽然认 .dds（见 VPVP wiki 的「拡張子」表，所以
# 转换时原样拷过去就行），但 GUI 预览的图片解码走的是纯标准库路线，没装
# Pillow 时只认 PNG/TGA/BMP，DDS 会解不出来、预览变灰模。这里补一个
# DXT1/DXT3/DXT5 与未压缩 RGB(A) 的解码，解成 PNG 后预览就能正常显示。

# 一个索引字节 → 4 个 2 位索引（低位在前），DXT 全系都用这个位序
_DXT_Q = tuple(((b & 3), ((b >> 2) & 3), ((b >> 4) & 3), ((b >> 6) & 3))
               for b in range(256))


def _rgb565(v):
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _dxt_palette(c0, c1, allow_transparent):
    """(c0, c1) → 4 个 RGBA 字节串。

    allow_transparent（只有 DXT1 传 True）时，c0 <= c1 表示用「3 色 + 全透明」
    档，第 4 个是透明黑 —— 这是 DXT1 唯一的 alpha 来源。
    """
    r0, g0, b0 = _rgb565(c0)
    r1, g1, b1 = _rgb565(c1)
    p0 = bytes((r0, g0, b0, 255))
    p1 = bytes((r1, g1, b1, 255))
    if allow_transparent and c0 <= c1:
        return (p0, p1,
                bytes(((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255)),
                b"\x00\x00\x00\x00")
    return (p0, p1,
            bytes(((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3,
                   255)),
            bytes(((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3,
                   255)))


def _dxt5_alpha_ramp(a0, a1):
    """DXT5 的 8 个 alpha 值：a0 > a1 是 6 插值档，否则 4 插值 + 0/255。"""
    if a0 > a1:
        return (a0, a1) + tuple(
            ((7 - i) * a0 + i * a1) // 7 for i in range(1, 7))
    return (a0, a1) + tuple(
        ((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)) + (0, 255)


def _decode_dds(data):
    """DDS → PNG 字节；不支持的子格式返回 None。"""
    if data[:4] != b"DDS " or len(data) < 128:
        return None
    hdr = struct.unpack_from("<7I", data, 4)
    height, width = hdr[2], hdr[3]
    if not width or not height or width > 16384 or height > 16384:
        return None
    fourcc = data[84:88]
    rgb_bits = struct.unpack_from("<I", data, 88)[0]
    pix = bytearray(width * height * 4)
    stride = width * 4

    if fourcc in (b"DXT1", b"DXT3", b"DXT5"):
        block = 8 if fourcc == b"DXT1" else 16
        bw = (width + 3) // 4
        bh = (height + 3) // 4
        if len(data) < 128 + bw * bh * block:
            return None
        off = 128
        Q = _DXT_Q
        if fourcc == b"DXT1":
            for by in range(bh):
                y0 = by * 4
                for bx in range(bw):
                    c0 = data[off] | (data[off + 1] << 8)
                    c1 = data[off + 2] | (data[off + 3] << 8)
                    bits = struct.unpack_from("<I", data, off + 4)[0]
                    pal = _dxt_palette(c0, c1, True)
                    off += 8
                    x0 = bx * 4
                    for r in range(4):
                        if y0 + r >= height:
                            break
                        q = Q[(bits >> (8 * r)) & 0xFF]
                        row = (pal[q[0]] + pal[q[1]] + pal[q[2]] + pal[q[3]])
                        o = (y0 + r) * stride + x0 * 4
                        if x0 + 4 <= width:
                            pix[o:o + 16] = row
                        else:
                            pix[o:o + (width - x0) * 4] = \
                                row[:(width - x0) * 4]
        elif fourcc == b"DXT3":
            for by in range(bh):
                y0 = by * 4
                for bx in range(bw):
                    al = data[off:off + 8]
                    c0 = data[off + 8] | (data[off + 9] << 8)
                    c1 = data[off + 10] | (data[off + 11] << 8)
                    bits = struct.unpack_from("<I", data, off + 12)[0]
                    pal = _dxt_palette(c0, c1, False)
                    off += 16
                    x0 = bx * 4
                    for r in range(4):
                        if y0 + r >= height:
                            break
                        o = (y0 + r) * stride + x0 * 4
                        for k in range(4):
                            if x0 + k >= width:
                                break
                            i = r * 4 + k
                            n = (al[i >> 1] >> (4 * (i & 1))) & 0xF
                            c = pal[(bits >> (2 * i)) & 3]
                            pix[o + k * 4] = c[0]
                            pix[o + k * 4 + 1] = c[1]
                            pix[o + k * 4 + 2] = c[2]
                            pix[o + k * 4 + 3] = n * 17
        else:                                       # DXT5
            for by in range(bh):
                y0 = by * 4
                for bx in range(bw):
                    ramp = _dxt5_alpha_ramp(data[off], data[off + 1])
                    abits = int.from_bytes(data[off + 2:off + 8], "little")
                    c0 = data[off + 8] | (data[off + 9] << 8)
                    c1 = data[off + 10] | (data[off + 11] << 8)
                    bits = struct.unpack_from("<I", data, off + 12)[0]
                    pal = _dxt_palette(c0, c1, False)
                    off += 16
                    x0 = bx * 4
                    for r in range(4):
                        if y0 + r >= height:
                            break
                        o = (y0 + r) * stride + x0 * 4
                        for k in range(4):
                            if x0 + k >= width:
                                break
                            i = r * 4 + k
                            c = pal[(bits >> (2 * i)) & 3]
                            pix[o + k * 4] = c[0]
                            pix[o + k * 4 + 1] = c[1]
                            pix[o + k * 4 + 2] = c[2]
                            pix[o + k * 4 + 3] = ramp[(abits >> (3 * i)) & 7]
        return _png_from_rgba(width, height, bytes(pix))

    if fourcc == b"\x00\x00\x00\x00" and rgb_bits in (24, 32):
        rmask, gmask, bmask, amask = struct.unpack_from("<4I", data, 92)
        bpp = rgb_bits // 8
        if len(data) < 128 + width * height * bpp:
            return None
        # 常见布局走快路：24 位 BGR / 32 位 BGRA
        if (rgb_bits == 24 and rmask == 0xFF0000 and gmask == 0xFF00
                and bmask == 0xFF):
            src = data[128:128 + width * height * 3]
            for i in range(width * height):
                o = i * 4
                pix[o] = src[i * 3 + 2]
                pix[o + 1] = src[i * 3 + 1]
                pix[o + 2] = src[i * 3]
                pix[o + 3] = 255
            return _png_from_rgba(width, height, bytes(pix))
        if (rgb_bits == 32 and rmask == 0xFF0000 and gmask == 0xFF00
                and bmask == 0xFF and amask == 0xFF000000):
            src = data[128:128 + width * height * 4]
            for i in range(width * height):
                o = i * 4
                pix[o] = src[i * 4 + 2]
                pix[o + 1] = src[i * 4 + 1]
                pix[o + 2] = src[i * 4]
                pix[o + 3] = src[i * 4 + 3]
            return _png_from_rgba(width, height, bytes(pix))
        # 其它掩码：按位段通用抽取
        def _shift(mask):
            if not mask:
                return 0, 0
            s = 0
            while not (mask >> s) & 1:
                s += 1
            bits = bin(mask >> s).count("1")
            return s, bits

        rs, rb = _shift(rmask)
        gs, gb = _shift(gmask)
        bs, bb = _shift(bmask)
        as_, ab = _shift(amask)

        def _comp(v, s, n, default=255):
            if not n:
                return default
            x = (v >> s) & ((1 << n) - 1)
            return x * 255 // ((1 << n) - 1)

        src = data[128:128 + width * height * bpp]
        for i in range(width * height):
            v = int.from_bytes(src[i * bpp:(i + 1) * bpp], "little")
            o = i * 4
            pix[o] = _comp(v, rs, rb)
            pix[o + 1] = _comp(v, gs, gb)
            pix[o + 2] = _comp(v, bs, bb)
            pix[o + 3] = _comp(v, as_, ab, 255)
        return _png_from_rgba(width, height, bytes(pix))
    return None


def decode_image(data):
    """任意常见贴图字节 → PNG 字节；无法处理时返回 None。"""
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data
    if data[:3] == b"\xff\xd8\xff":
        return data                                # JPEG 直接透传
    if data[:2] == b"BM":
        try:
            return _decode_bmp(data)
        except Exception:
            return None
    if data[:4] == b"DDS ":
        try:
            return _decode_dds(data)
        except Exception:
            return None
    if len(data) > 18 and data[2] in (2, 3, 10, 11) and data[1] in (0, 1):
        try:
            return _decode_tga(data)
        except Exception:
            return None
    return None


def decode_rgba(data, max_pixels=4 << 20):
    """贴图字节 → (宽, 高, 行列表)，每行是 (r, g, b, a) 元组的列表；失败返回 None。

    纯 Python，给 toon 渐变这类小图取色用（BMP / TGA / DDS 先转成 PNG）。
    只支持 8 位、非隔行的 PNG；超过 max_pixels 的大图不解（太慢）。
    """
    if not data:
        return None
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        data = decode_image(data)
        if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
    p = 8
    idat = []
    w = h = bd = ct = il = 0
    plte = trns = None
    while p + 8 <= len(data):
        ln, ty = struct.unpack(">I4s", data[p:p + 8])
        body = data[p + 8:p + 8 + ln]
        p += 12 + ln
        if ty == b"IHDR":
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", body[:13])
        elif ty == b"IDAT":
            idat.append(body)
        elif ty == b"PLTE":
            plte = body
        elif ty == b"tRNS":
            trns = body
        elif ty == b"IEND":
            break
    if bd != 8 or il or ct not in (0, 2, 3, 4, 6) or w * h > max_pixels:
        return None
    if ct == 3 and not plte:
        return None
    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ct]
    stride = w * bpp
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    if len(raw) < h * (stride + 1):
        return None
    rows = []
    prev = bytearray(stride)
    for y in range(h):
        base = y * (stride + 1)
        f = raw[base]
        out = bytearray(raw[base + 1:base + 1 + stride])
        if f == 1:
            for x in range(bpp, stride):
                out[x] = (out[x] + out[x - bpp]) & 255
        elif f == 2:
            for x in range(stride):
                out[x] = (out[x] + prev[x]) & 255
        elif f == 3:
            for x in range(stride):
                left = out[x - bpp] if x >= bpp else 0
                out[x] = (out[x] + ((left + prev[x]) >> 1)) & 255
        elif f == 4:
            for x in range(stride):
                a = out[x - bpp] if x >= bpp else 0
                b = prev[x]
                c = prev[x - bpp] if x >= bpp else 0
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                out[x] = (out[x] + (a if pa <= pb and pa <= pc
                                    else (b if pb <= pc else c))) & 255
        prev = out
        if ct == 6:
            rows.append([tuple(out[i:i + 4]) for i in range(0, stride, 4)])
        elif ct == 2:
            rows.append([tuple(out[i:i + 3]) + (255,) for i in range(0, stride, 3)])
        elif ct == 0:
            rows.append([(v, v, v, 255) for v in out])
        elif ct == 4:
            rows.append([(out[i], out[i], out[i], out[i + 1]) for i in range(0, stride, 2)])
        else:
            rows.append([tuple(plte[3 * v:3 * v + 3]) +
                         ((trns[v] if trns and v < len(trns) else 255),) for v in out])
    return w, h, rows


def png_alpha_stats(data, max_rows=512):
    """统计 PNG 的透明度分布，用来决定材质的 alphaMode。

    返回 (采样像素数, 透明像素数 a<250, 半透明像素数 10<a<245)；
    图片没有 alpha（RGB / 灰度 / 无 tRNS 的调色板 / JPEG）返回 None。
    只解 alpha 通道：PNG 的行滤波按「同一通道的左边/上边字节」做，
    alpha 字节只依赖 alpha 字节，不必把 RGB 也算一遍。
    大图只抽约 max_rows 行（从最近的「不依赖上一行」的行开始连续解到目标行），
    纯 Python 也能在一两秒内处理 2048² 的贴图。
    """
    if not data or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    p = 8
    idat = []
    w = h = bd = ct = il = 0
    trns = None
    while p + 8 <= len(data):
        ln, ty = struct.unpack(">I4s", data[p:p + 8])
        body = data[p + 8:p + 8 + ln]
        p += 12 + ln
        if ty == b"IHDR":
            w, h, bd, ct, _, _, il = struct.unpack(">IIBBBBB", body[:13])
        elif ty == b"IDAT":
            idat.append(body)
        elif ty == b"tRNS":
            trns = body
        elif ty == b"IEND":
            break
    if ct == 3:
        if not trns:
            return None
        lut = bytes(trns) + b"\xff" * (256 - len(trns))
    elif ct not in (4, 6):
        return None                                # tRNS 色键这种少见情况忽略
    if il or bd not in (8, 16) and ct != 3:
        return (1, 1, 1)                           # 隔行/罕见位深：保守地当半透明
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return (1, 1, 1)
    if ct == 3:
        if bd != 8:
            return (1, 1, 1)
        bpp, aoff = 1, 0
    else:
        ch = 4 if ct == 6 else 2
        bpp = ch * (bd // 8)
        aoff = bpp - (bd // 8)                     # 16 位取 alpha 的高字节
    stride = w * bpp
    if len(raw) < h * (stride + 1):
        return (1, 1, 1)
    step = max(1, h // max_rows)
    filt = [raw[y * (stride + 1)] for y in range(h)]

    def unfilter(y, prev):
        """只还原第 y 行 alpha（调色板图还原索引），prev 为上一行结果或 None。"""
        base = y * (stride + 1) + 1
        src = raw[base + aoff:base + stride:bpp]
        f = filt[y]
        if f == 0:
            return bytearray(src)
        out = bytearray(len(src))
        if f == 1:
            a = 0
            for x, v in enumerate(src):
                a = (v + a) & 255
                out[x] = a
            return out
        up = prev if prev is not None else bytes(len(src))
        if f == 2:
            for x, v in enumerate(src):
                out[x] = (v + up[x]) & 255
            return out
        a = c = 0
        for x, v in enumerate(src):
            b = up[x]
            if f == 3:
                a = (v + ((a + b) >> 1)) & 255
            else:
                pa = b - c
                pb = a - c
                pc = abs(pa + pb)
                pa = abs(pa)
                pb = abs(pb)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                a = (v + pr) & 255
            out[x] = a
            c = b
        return out

    n = trans = mid = 0
    last_y, last_row = -1, None
    for t in range(0, h, step):
        if last_y >= 0 and t - last_y <= step:
            y, prev = last_y + 1, last_row
        else:
            y = t
            while y > 0 and filt[y] > 1 and y > last_y + 1:
                y -= 1
            prev = last_row if y == last_y + 1 else None
        row = prev
        while y <= t:
            row = unfilter(y, prev)
            prev = row
            y += 1
        last_y, last_row = t, row
        if ct == 3:
            row = row.translate(lut)
        n += len(row)
        for v in row:
            if v < 250:
                trans += 1
                if 10 < v < 245:
                    mid += 1
    return (n, trans, mid)


def load_texture(path):
    """读取磁盘贴图，返回 (bytes, mime)；失败返回 (None, None)。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None, None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return data, "image/jpeg"
    out = decode_image(data)
    if out is None:
        return None, None
    return out, ("image/png" if out[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg")


def save_image(data, path):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        p = os.path.splitext(path)[0] + ".png"
    elif data[:3] == b"\xff\xd8\xff":
        p = os.path.splitext(path)[0] + ".jpg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        p = os.path.splitext(path)[0] + ".webp"
    elif data[:4] == b"\xabKTX":
        p = os.path.splitext(path)[0] + ".ktx2"
    else:
        p = path
    with open(p, "wb") as f:
        f.write(data)
    return p
