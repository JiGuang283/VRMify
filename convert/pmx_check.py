"""pmx_check.py - validate a PMX 2.0 file and render a preview PNG.

Pure standard library: no numpy, no PIL. Parses the PMX structure, reports
counts, sanity-checks every index, and software-rasterises two orthographic
views so you can eyeball whether the conversion is intact.

Usage:
    python pmx_check.py "model.pmx"
    python pmx_check.py "model.pmx" -o preview.png --size 640
"""
import argparse
import math
import os
import struct
import sys
import zlib


class Reader:
    def __init__(self, d, enc):
        self.d = d
        self.p = 0
        self.enc = enc

    def i32(self):
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def f32(self):
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def u8(self):
        v = self.d[self.p]
        self.p += 1
        return v

    def text(self):
        n = self.i32()
        s = self.d[self.p:self.p + n]
        self.p += n
        return s.decode("utf-16-le" if self.enc == 0 else "utf-8", "replace")

    def idx(self, size, signed=True):
        if size == 1:
            v = struct.unpack_from("<b" if signed else "<B", self.d, self.p)[0]
            self.p += 1
        elif size == 2:
            v = struct.unpack_from("<h" if signed else "<H", self.d, self.p)[0]
            self.p += 2
        else:
            v = self.i32()
            if not signed:
                v &= 0xFFFFFFFF
        return v


def read_pmx(path):
    d = open(path, "rb").read()
    assert d[:4] == b"PMX ", "not a PMX file"
    version = struct.unpack_from("<f", d, 4)[0]
    r = Reader(d, 0)
    r.p = 8
    gc = r.u8()
    g = [r.u8() for _ in range(gc)]
    enc, add_uv, vi_s, ti_s, mi_s, bi_s, moi_s, ri_s = g
    r.enc = enc

    out = {"version": version, "globals": g, "add_uv": add_uv,
           "vi_s": vi_s, "ti_s": ti_s, "mi_s": mi_s, "bi_s": bi_s}
    out["name"] = r.text()
    out["name_en"] = r.text()
    out["comment"] = r.text()
    out["comment_en"] = r.text()

    # vertices
    nv = r.i32()
    verts = []
    for _ in range(nv):
        px, py, pz = r.f32(), r.f32(), r.f32()
        nx, ny, nz = r.f32(), r.f32(), r.f32()
        u, v = r.f32(), r.f32()
        for _ in range(add_uv):
            for _ in range(4):
                r.f32()
        wt = r.u8()
        nb = {0: 1, 1: 2, 2: 4, 3: 2, 4: 4}.get(wt)
        if nb is None:
            raise ValueError("顶点 %d 的变形类型字节 %d 非法（文件解析失步）"
                             % (len(verts), wt))
        bones = [r.idx(bi_s) for _ in range(nb)]
        nw = {0: 0, 1: 1, 2: 4, 3: 1, 4: 4}[wt]
        ws = [r.f32() for _ in range(nw)]
        # SDEF(3) 在权重之后还有 C / R0 / R1 三个 vec3 —— 漏读会让后面
        # 所有顶点全部错位（表现为 wt 读到 'K'=75 之类的天书字节）。
        sdef = None
        if wt == 3:
            sdef = ((r.f32(), r.f32(), r.f32()),
                    (r.f32(), r.f32(), r.f32()),
                    (r.f32(), r.f32(), r.f32()))
        r.f32()  # edge scale
        verts.append(((px, py, pz), (nx, ny, nz), (u, v), wt, bones, ws, sdef))
    out["vertices"] = verts
    if os.environ.get("PMX_DEBUG"):
        print("[dbg] after verts   p=%d" % r.p)

    # faces
    nf = r.i32()
    faces = [r.idx(vi_s, signed=False) for _ in range(nf)]
    out["faces"] = faces
    if os.environ.get("PMX_DEBUG"):
        print("[dbg] after faces   p=%d (nf=%d)" % (r.p, nf))

    # textures
    ntex = r.i32()
    out["textures"] = [r.text() for _ in range(ntex)]

    # materials
    nm = r.i32()
    mats = []
    for _ in range(nm):
        name = r.text(); name_en = r.text()
        diff = [r.f32() for _ in range(4)]
        spec = [r.f32() for _ in range(3)]
        shin = r.f32()
        amb = [r.f32() for _ in range(3)]
        flag = r.u8()
        edge_col = [r.f32() for _ in range(4)]
        edge_size = r.f32()
        tex = r.idx(mi_s)
        sph = r.idx(mi_s)
        sph_mode = r.u8()
        toon_flag = r.u8()
        # toon 宽度随 flag 变（别搞反）：flag=1 → 1 字节内建 toon 号
        # （0..9 = MMD 的 Data/toon01.bmp..toon10.bmp，**0 号是 toon01.bmp**）；
        # flag=0 → 纹理索引宽度（本模型贴图），**-1 = 不使用 toon**。
        toon = r.idx(ti_s) if toon_flag == 0 else r.u8()
        memo = r.text()
        fcount = r.i32()
        mats.append({"name": name, "diffuse": diff, "flag": flag,
                     "tex": tex, "faces": fcount,
                     "sph": sph, "sph_mode": sph_mode,
                     "toon_flag": toon_flag, "toon": toon,
                     "memo": memo})
    out["materials"] = mats
    if os.environ.get("PMX_DEBUG"):
        print("[dbg] after materials p=%d (nm=%d)" % (r.p, nm))

    # bones
    nbn = r.i32()
    if os.environ.get("PMX_DEBUG"):
        print("[dbg] bones count = %d at p=%d" % (nbn, r.p))
    bones = []
    for _ in range(nbn):
        bname = r.text(); bname_en = r.text()
        pos = (r.f32(), r.f32(), r.f32())
        parent = r.idx(bi_s)
        layer = r.i32()
        bflag = struct.unpack_from("<H", d, r.p)[0]
        r.p += 2
        if bflag & 0x0001:
            tail = ("bone", r.idx(bi_s))
        else:
            tail = ("offset", (r.f32(), r.f32(), r.f32()))
        # PMX 2.0 bone flags:
        #   0x0002 rotatable  0x0004 movable  0x0008 visible  0x0010 operable
        #   0x0020 IK         0x0100 inherit-rot 0x0200 inherit-trans
        #   0x0400 fixed axis 0x0800 local axis  0x2000 external parent
        # 付与親/付与率是回転・移動共通的一组：任一 flag 置位只读一次，
        # 读两遍会多走 6 字节，从第一根双付与骨（如センター 0x031E）开始错位。
        if bflag & 0x0100 or bflag & 0x0200:
            r.idx(bi_s); r.f32()
        if bflag & 0x0400:
            r.f32(); r.f32(); r.f32()
        if bflag & 0x0800:
            for _ in range(6):
                r.f32()
        if bflag & 0x2000:
            r.i32()                     # 外部親Key 只有 4 字节
        ik = None
        if bflag & 0x0020:
            # IK: ターゲット(ボーンIndex) / ループ回数(int) /
            #     1回あたりの制限角度(float) / リンク数(int)
            tgt = r.idx(bi_s); r.i32(); r.f32()
            nl = r.i32()
            links = []
            for _ in range(nl):
                links.append(r.idx(bi_s))   # リンクボーンIndex
                if r.u8():                  # 角度制限フラグ（每个 link 交错）
                    for _ in range(6):      # 下限 float×3 + 上限 float×3
                        r.f32()
            ik = {"target": tgt, "links": links}
        bones.append({"name": bname, "pos": pos, "parent": parent,
                      "flag": bflag, "layer": layer, "tail": tail, "ik": ik})
    out["bones"] = bones

    # morphs
    nmo = r.i32()
    morphs = []
    for _ in range(nmo):
        mname = r.text(); mname_en = r.text()
        panel = r.u8(); kind = r.u8()
        noff = r.i32()
        refs = []                           # [(目标类, 索引)] 供越界检查用
        if kind == 0:                       # グループ
            for _ in range(noff):
                refs.append(("morph", r.idx(moi_s)))
                r.f32()
        elif kind == 1:                     # 頂点（顶点索引 + 3 个偏移）
            for _ in range(noff):
                refs.append(("vertex", r.idx(vi_s, signed=False)))
                r.f32(); r.f32(); r.f32()
        elif kind == 2:                     # ボーン
            for _ in range(noff):
                refs.append(("bone", r.idx(bi_s)))
                r.f32(); r.f32(); r.f32()
                r.f32(); r.f32(); r.f32(); r.f32()
        elif kind in (3, 4, 5, 6, 7):       # UV / 追加UV1-4（4 个偏移）
            for _ in range(noff):
                refs.append(("vertex", r.idx(vi_s, signed=False)))
                r.f32(); r.f32(); r.f32(); r.f32()
        elif kind == 8:                     # 材質
            for _ in range(noff):
                refs.append(("material", r.idx(mi_s)))
                r.u8()
                for _ in range(28):
                    r.f32()
        elif kind == 9:                     # フリップ
            for _ in range(noff):
                refs.append(("morph", r.idx(moi_s)))
                r.f32()
        elif kind == 10:                    # インパルス（刚体索引）
            for _ in range(noff):
                refs.append(("rigid", r.idx(ri_s)))
                r.u8()                  # ローカルフラグ
                r.f32(); r.f32(); r.f32()
                r.f32(); r.f32(); r.f32()
        morphs.append({"name": mname, "kind": kind, "count": noff, "refs": refs})
    out["morphs"] = morphs

    # display frames
    nd = r.i32()
    frames = []
    for _ in range(nd):
        fname = r.text(); fname_en = r.text()
        special = r.u8()
        ni = r.i32()
        items = []
        for _ in range(ni):
            t = r.u8()
            # 要素対象：0 = ボーン, 1 = モーフ
            if t == 0:
                items.append(("bone", r.idx(bi_s), None))
            else:
                items.append(("morph", r.idx(moi_s), None))
        frames.append({"name": fname, "special": special, "items": items})
    out["frames"] = frames

    # 刚体：名/英名/关联骨骼/グループ/非衝突グループ(2)/形状/サイズ/位置/回転/
    #      質量/移動減衰/回転減衰/反発力/摩擦力/物理演算
    nrb = r.i32()
    out["rigid_bodies"] = nrb
    rb_bones = []
    for _ in range(nrb):
        r.text(); r.text()
        rb_bones.append(r.idx(bi_s))
        r.u8()
        r.p += 2
        r.u8()
        for _ in range(14):
            r.f32()
        r.u8()
    # 关节：名/英名/種類/剛体A/剛体B/8 个 vec3
    nj = r.i32()
    out["joints"] = nj
    joints = []
    for _ in range(nj):
        r.text(); r.text()
        r.u8()
        joints.append((r.idx(ri_s), r.idx(ri_s)))
        for _ in range(24):
            r.f32()
    out["rigid_bones"] = rb_bones
    out["joint_rigid"] = joints
    out["consumed"] = r.p
    out["filesize"] = len(d)
    return out


# ---------------------------------------------------------------- render ----

def write_png(path, w, h, rows):
    raw = bytearray()
    for r in rows:
        raw.append(0)
        for px in r:
            raw += bytes(px)

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data +
                struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def render(model, size=560, bg=(250, 250, 250), show_bones=False):
    verts = model["vertices"]
    faces = model["faces"]
    if not verts or not faces:
        return None

    xs = [v[0][0] for v in verts]
    ys = [v[0][1] for v in verts]
    zs = [v[0][2] for v in verts]
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    minz, maxz = min(zs), max(zs)

    SCALE = 0.52

    span = max(maxx - minx, maxy - miny) or 1.0
    k = size * SCALE * 2 / span

    def project(p):
        """正面视图（相机在 **-Z**，朝 +Z 看）。

        MMD / PMX 的约定是 **正面 = -Z、左手 = +X、上 = +Y**（实测：用户本机
        Anastasya.pmx 的「Eye」材质顶点 Z 重心 = -1.27，「Face」= -1.31；本工具
        转出的 PSK 模型修正后同样是负值）。

        相机在 -Z 时，屏幕基向量（右手系下 x_view = up × z_view，
        z_view 指「从场景指向相机」= (0,0,-1)）：
            屏幕上 = +Y     → sy 取 +Y
            屏幕右 = -X     → sx 取 **-X**（所以这里要镜像 X）
        第三个分量返回**朝相机的深度** -z，越大越近，配合下面的 z 缓冲用。

        ⚠ 2026-09-24 修：以前这里的代码是
              sx = ... + size/2 ;  sy = ... - ... ;  return sx, sy, p[2]
        即「屏幕右 = +X、深度 = +z、大的赢」—— 相机实际落在 **+Z**，看到的是
        **背面**（注释却写着 front view）；而且下面还按屏幕绕序把朝向相机的面
        剔掉了，结果渲染出来是一堆内翻的碎片。现在两处一起修正。
        """
        sx = size / 2 - (p[0] - (minx + maxx) / 2) * k
        sy = size / 2 - (p[1] - (miny + maxy) / 2) * k
        return sx, sy, -p[2]

    img = [[list(bg) for _ in range(size)] for _ in range(size)]
    zbuf = [[-1e30] * size for _ in range(size)]

    L = (0.45, 0.72, 0.53)
    ln = math.sqrt(L[0] ** 2 + L[1] ** 2 + L[2] ** 2)
    L = (L[0] / ln, L[1] / ln, L[2] / ln)

    pts = [project(v[0]) for v in verts]
    nrm = [v[1] for v in verts]

    for i in range(0, len(faces) - 2, 3):
        a, b, c = faces[i], faces[i + 1], faces[i + 2]
        pa, pb, pc = pts[a], pts[b], pts[c]
        # 不做背面剔除，只靠 z 缓冲。
        # 屏幕空间绕序在这里不可靠：投影把 X 镜像了一次，绕序会跟着翻，
        # 加上 MMD 模型里大量单面/双面混用的裙摆头发，按绕序剔除很容易把
        # 朝向相机的面剔掉、只剩壳体内部（2026-09-24 之前就是这个症状）。
        # 只跳过退化三角形（三点共线，面积 0）。
        area = ((pb[0] - pa[0]) * (pc[1] - pa[1]) -
                (pc[0] - pa[0]) * (pb[1] - pa[1]))
        if area == 0.0:
            continue
        n = nrm[a]
        lam = abs(n[0] * L[0] + n[1] * L[1] + n[2] * L[2])
        shade = 0.35 + 0.65 * lam
        col = (int(214 * shade), int(198 * shade), int(190 * shade))
        x0 = max(0, int(min(pa[0], pb[0], pc[0])))
        x1 = min(size - 1, int(max(pa[0], pb[0], pc[0])) + 1)
        y0 = max(0, int(min(pa[1], pb[1], pc[1])))
        y1 = min(size - 1, int(max(pa[1], pb[1], pc[1])) + 1)
        if x1 < x0 or y1 < y0:
            continue
        d = (pb[1] - pc[1]) * (pa[0] - pc[0]) + (pc[0] - pb[0]) * (pa[1] - pc[1])
        if abs(d) < 1e-9:
            continue
        inv = 1.0 / d
        for py in range(y0, y1 + 1):
            cy = py + 0.5
            row = img[py]
            zrow = zbuf[py]
            for px in range(x0, x1 + 1):
                cx = px + 0.5
                w0 = ((pb[1] - pc[1]) * (cx - pc[0]) +
                      (pc[0] - pb[0]) * (cy - pc[1])) * inv
                if w0 < 0:
                    continue
                w1 = ((pc[1] - pa[1]) * (cx - pc[0]) +
                      (pa[0] - pc[0]) * (cy - pc[1])) * inv
                if w1 < 0:
                    continue
                w2 = 1.0 - w0 - w1
                if w2 < 0:
                    continue
                z = w0 * pa[2] + w1 * pb[2] + w2 * pc[2]
                if z > zrow[px]:
                    zrow[px] = z
                    row[px] = [col[0], col[1], col[2]]

    if show_bones:
        for b in model["bones"]:
            sx, sy, _ = project(b["pos"])
            ix, iy = int(sx), int(sy)
            for dx in range(-2, 3):
                for dy in range(-2, 3):
                    if abs(dx) + abs(dy) > 3:
                        continue
                    px, py = ix + dx, iy + dy
                    if 0 <= px < size and 0 <= py < size:
                        img[py][px] = [220, 40, 40]
    return img


def mmd_audit(m, path=None):
    """MMD 比 PMXEditor 严格的那些点。

    PMXEditor 很宽容：索引越界、版本号、编码它大多能糊过去。MMD（DirectX9 时代
    的老程序）不行 —— 越界的索引会直接把顶点/物理数据喂进渲染管线，
    表现就是「PMXEditor 能开、MMD 打不开」。这里逐项对照。

    返回 (ok_list, problems)，problems 里的每一条都是**可能导致 MMD 拒绝载入**
    或载入后出错的原因；ok_list 是已确认没问题的项。
    """
    nv = len(m["vertices"])
    nb = len(m["bones"])
    ntex = len(m["textures"])
    nmat = len(m["materials"])
    nmo = len(m["morphs"])
    nfo = len(m["faces"])
    nrb = m.get("rigid_bodies", 0)
    nj = m.get("joints", 0)
    ok, bad = [], []

    # --- 1. 文本编码：这是「PE 能开 / MMD 不能开」最常见的成因 -------
    enc = m["globals"][0] if m.get("globals") else -1
    if enc == 0:
        ok.append("文本编码 UTF-16LE（MMD 要求的编码）")
    elif enc == 1:
        bad.append("文本编码是 UTF-8 —— MMD 只认 UTF-16LE，会直接拒绝载入；"
                   "PMXEditor 两种都能开，所以症状正好是「PE 能开、MMD 不能开」。"
                   "修复：python formats/pmxio.py <文件>（或 PE 里另存为 UTF-16）")

    # --- 2. 版本号 ------------------------------------------------
    v = m["version"]
    if v > 2.05:
        bad.append("版本号 %.2f（PMX 2.1）：部分 MMD 版本不认 2.1，"
                   "会报 Invalid PMX format。建议降成 2.0 再试" % v)
    elif abs(v - 2.0) > 0.01:
        bad.append("版本号 %.2f 不是 2.0，MMD 只保证认识 2.0" % v)
    else:
        ok.append("版本号 2.0")

    # --- 3. 索引越界（MMD 最不能忍的一类）--------------------------
    n = sum(1 for f in m["faces"] if f >= nv)
    if n:
        bad.append("%d 个面引用了不存在的顶点（顶点只有 %d 个）" % (n, nv))
    n = 0
    for vt in m["vertices"]:
        for b in vt[4]:
            if b >= nb or b < 0:
                n += 1
    if n:
        bad.append("%d 个顶点权重的骨骼索引越界（骨骼只有 %d 根）" % (n, nb))

    # --- 3b. 孤儿顶点 & 材质顶点索引跨度 ---------------------------
    # 这是「PMXEditor 能开、MMD 拖进去闪退」的另一大来源，2026-10-02 实测踩到：
    # VRM/glTF 里一个 mesh 的多个 primitive 会共用同一份 POSITION 数组，靠各自的
    # indices 按材质切分；如果转换时逐个 primitive 都完整复制一遍顶点，就会产出
    # 大量没人引用的顶点，并把材质引用到的顶点索引整体推高（实测苏娅.vrm：
    # 391157 个顶点里 332702 个是孤儿，材质索引最大到 391156）。
    # MMD 是 D3D9 时代程序，渲染走 16 位索引缓冲，索引一旦 ≥ 65536 就会闪退。
    if nv:
        used = set(m["faces"])
        orphan = nv - len(used)
        pct = 100.0 * orphan / nv
        if orphan and pct > 20.0:
            bad.append("有 %d 个顶点（%.0f%%）没有被任何面引用 —— 白占内存，还会把"
                       "材质引用到的顶点索引整体推高。常见成因：VRM/glTF 里一个 mesh "
                       "的多个 primitive 共用同一份顶点数组，转换时逐 primitive "
                       "复制了一遍" % (orphan, pct))
        else:
            ok.append("孤儿顶点 %d 个（占比 %.1f%%，可接受）" % (orphan, pct))

    if m["materials"] and m["faces"]:
        start = 0
        spans = []
        for mm in m["materials"]:
            seg = m["faces"][start:start + mm["faces"]]
            start += mm["faces"]
            if seg and max(seg) >= 65535:
                spans.append((mm["name"], max(seg)))
        if spans:
            bad.append("%d 个材质引用到的顶点索引 ≥ 65535（最大 %d，如「%s」）—— "
                       "MMD 的 D3D9 渲染用 16 位索引缓冲，超限会直接闪退；"
                       "多半是上面「孤儿顶点」那条的连带结果"
                       % (len(spans), max(x[1] for x in spans), spans[0][0]))
        else:
            ok.append("所有材质引用的顶点索引都 < 65535")

    n = 0
    for mt in m["materials"]:
        for key in ("tex", "sph"):
            k = mt.get(key, -1)
            if k != -1 and not (0 <= k < ntex):
                bad.append("材质「%s」的%s索引 %d 越界（贴图只有 %d 张）"
                           % (mt.get("name"), key, k, ntex))
                n += 1
        if mt.get("toon_flag", 0) == 0:
            k = mt.get("toon", -1)
            if k != -1 and not (0 <= k < ntex):
                bad.append("材质「%s」的 Toon 贴图索引 %d 越界（贴图只有 %d 张）"
                           % (mt.get("name"), k, ntex))
                n += 1
        elif not (0 <= mt.get("toon", 9) <= 9):
            bad.append("材质「%s」的共享 Toon 编号 %d 越界（必须是 0〜9）"
                       % (mt.get("name"), mt.get("toon")))
            n += 1
    if not n:
        ok.append("材质引用的贴图 / 球面 / Toon 索引都在范围内")

    limit = {"vertex": nv, "bone": nb, "material": nmat, "morph": nmo, "rigid": nrb}
    n = 0
    for mo in m["morphs"]:
        for tgt, ix in (mo.get("refs") or []):
            hi = limit.get(tgt)
            if hi is None:
                continue
            if not (0 <= ix < hi):
                bad.append("表情「%s」引用了越界的%s索引 %d（上限 %d）"
                           % (mo.get("name"), tgt, ix, hi))
                n += 1
                break
    if not n:
        ok.append("表情的引用索引都在范围内")

    n = 0
    for fr in m["frames"]:
        for tgt, ix, _ in fr["items"]:
            if tgt == "bone" and not (0 <= ix < nb):
                bad.append("显示枠「%s」引用了不存在的骨骼 %d" % (fr["name"], ix))
                n += 1
            elif tgt == "morph" and not (0 <= ix < nmo):
                bad.append("显示枠「%s」引用了不存在的表情 %d" % (fr["name"], ix))
                n += 1
    if not n:
        ok.append("显示枠的骨骼 / 表情引用都在范围内")

    n = 0
    for bo in m["bones"]:
        p = bo.get("parent", -1)
        if p != -1 and not (0 <= p < nb):
            bad.append("骨骼「%s」的父骨骼索引 %d 越界" % (bo["name"], p))
            n += 1
        ik = bo.get("ik")
        if ik:
            if not (0 <= ik["target"] < nb):
                bad.append("骨骼「%s」的 IK 目标 %d 越界" % (bo["name"], ik["target"]))
                n += 1
            for lk in ik["links"]:
                if not (0 <= lk < nb):
                    bad.append("骨骼「%s」的 IK 链接 %d 越界" % (bo["name"], lk))
                    n += 1
    if not n:
        ok.append("骨骼父级 / IK 引用都在范围内")

    if nrb or nj:
        n = 0
        for bx in m.get("rigid_bones") or []:
            if bx != -1 and not (0 <= bx < nb):
                bad.append("刚体关联的骨骼索引 %d 越界（-1 表示不关联骨骼）" % bx)
                n += 1
        for a, b in m.get("joint_rigid") or []:
            for x in (a, b):
                if not (0 <= x < nrb):
                    bad.append("关节引用了不存在的刚体 %d（刚体只有 %d 个）" % (x, nrb))
                    n += 1
        if not n:
            ok.append("刚体 / 关节的引用都在范围内")
    else:
        ok.append("没有刚体 / 关节（不会踩物理数据的坑）")

    # --- 4. 名字里的坑 -------------------------------------------
    def bad_name(s):
        return (s or "").strip() == "" or any(ord(c) < 32 for c in (s or ""))

    hit = [nm for nm in ([x.get("name") for x in m["materials"]] +
                         [x.get("name") for x in m["bones"]] +
                         [x.get("name") for x in m["morphs"]] +
                         [x.get("name") for x in m["frames"]]) if bad_name(nm)]
    if hit:
        bad.append("%d 个名字为空或含控制字符（MMD 可能读不稳）：%s"
                   % (len(hit), hit[:5]))

    # --- 5. 规模：32 位 MMD 的内存上限 ----------------------------
    mb = (m["filesize"] / 1048576.0)
    if nv >= 300000 or nfo >= 900000:
        bad.append("规模偏大（%d 顶点 / %d 面索引 / %.1f MB）：32 位 MMD 有 2GB "
                   "地址空间上限，内存吃紧时会直接载入失败，PE 反而更能扛"
                   % (nv, nfo, mb))

    # --- 6. 路径字符集（日文系统 ANSI = CP932）--------------------
    if path:
        full = os.path.abspath(path)
        parts = [full] + full.replace("\\", "/").split("/")
        weird = []
        for s in parts:
            for c in s:
                try:
                    c.encode("cp932")
                except Exception:
                    weird.append(c)
        weird = sorted(set(weird))
        if weird:
            bad.append("文件路径含日文系统（CP932）表示不了的字符 %s —— MMD 用的是"
                       "老式路径处理，遇到非 ANSI 字符会静默失败（无报错、模型不出现），"
                       "而 PMXEditor 没事。建议放到纯英文 / 纯日文路径下再试"
                       % (weird[:8],))
        else:
            ok.append("路径字符在日文系统（CP932）下可表示")

    return ok, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pmx")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--size", type=int, default=560)
    ap.add_argument("--no-render", action="store_true")
    ap.add_argument("--mmd", action="store_true",
                    help="只做「MMD 能不能载入」体检（不渲染预览）")
    ap.add_argument("--bones", action="store_true",
                    help="overlay bone positions as red markers")
    a = ap.parse_args()
    if a.mmd:
        a.no_render = True

    m = read_pmx(a.pmx)
    print("file      : %s" % a.pmx)
    print("version   : %.1f   globals=%s" % (m["version"], m["globals"]))
    enc = m["globals"][0] if m.get("globals") else -1
    if enc == 0:
        print("encoding  : UTF-16LE  （MMD 可载入）")
    elif enc == 1:
        print("encoding  : UTF-8  ← MMD 无法载入这种 PMX，"
              "需要转成 UTF-16LE（pmxio.py 可以直接转）")
    else:
        print("encoding  : 未知取值 %d" % enc)
    print("name      : %s / %s" % (m["name"], m["name_en"]))
    print("vertices  : %d" % len(m["vertices"]))
    print("faces     : %d indices = %d triangles" % (len(m["faces"]), len(m["faces"]) // 3))
    print("textures  : %d" % len(m["textures"]))
    print("materials : %d" % len(m["materials"]))
    print("bones     : %d" % len(m["bones"]))
    print("morphs    : %d" % len(m["morphs"]))
    print("display   : %d frames" % len(m["frames"]))
    print("rigid/joint: %d / %d" % (m["rigid_bodies"], m["joints"]))
    print("bytes read: %d / %d  %s"
          % (m["consumed"], m["filesize"],
             "OK" if m["consumed"] == m["filesize"] else "MISMATCH"))

    nv = len(m["vertices"])
    nb = len(m["bones"])
    bad_v = sum(1 for f in m["faces"] if f >= nv)
    bad_b = 0
    for v in m["vertices"]:
        for b in v[4]:
            if b >= nb or b < 0:
                bad_b += 1
    print("index check: face->vertex out-of-range=%d  weight->bone out-of-range=%d"
          % (bad_v, bad_b))

    wsum_bad = 0
    for v in m["vertices"]:
        wt, ws = v[3], v[5]
        if wt == 0:
            s = 1.0
        elif wt in (1, 3):                 # BDEF2 / SDEF: w1 + (1 - w1)
            s = ws[0] + (1.0 - ws[0])
        else:                              # BDEF4 / QDEF
            s = sum(ws)
        if abs(s - 1.0) > 0.02:
            wsum_bad += 1
    print("weight sums off by >2%%: %d" % wsum_bad)

    if m["materials"]:
        tot = sum(mm["faces"] for mm in m["materials"])
        print("material face coverage: %d of %d indices" % (tot, len(m["faces"])))
        unused = [mm["name"] for mm in m["materials"] if mm["faces"] == 0]
        if unused:
            print("  materials with 0 faces: %s" % unused)

    # ---------------------------------------------- MMD 可载入性体检 --
    ok, bad = mmd_audit(m, a.pmx)
    print("")
    print("== MMD 可载入性体检 ==")
    for s in ok:
        print("  [OK]   %s" % s)
    if bad:
        for s in bad:
            print("  [问题] %s" % s)
        print("  → 结论：有 %d 项可能导致 MMD 无法载入（PMXEditor 宽容，"
              "这些它多半能开）" % len(bad))
    else:
        print("  → 结论：这个 PMX 在 MMD 侧应该能正常载入。"
              "若 MMD 仍打不开，请确认 MMD 版本、内存，以及模型是否放在"
              "纯英文/日文路径下")

    if a.no_render:
        return 0
    out = a.out or (os.path.splitext(a.pmx)[0] + "_preview.png")
    img = render(m, size=a.size, show_bones=a.bones)
    if img is None:
        print("nothing to render")
        return 1
    write_png(out, a.size, a.size, img)
    print("\npreview -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
