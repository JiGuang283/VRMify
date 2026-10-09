# -*- coding: utf-8 -*-
"""pmxio.py - 完整的 PMX 2.0 / 2.1 读写（纯 Python 标准库）。

读进模型的全部内容（含表情 offset、骨骼 IK/付与/轴限制、刚体、关节），并且能原样写回。
PMX → VRM 和各格式 → PMX 的转换都基于它。

数据结构（dict）：
    model = {
      "version", "enc", "add_uv",
      "name", "name_en", "comment", "comment_en",
      "vertices": [ {"pos":(3), "normal":(3), "uv":(2), "add_uv":[(4)...],
                      "wtype":0..4, "wbones":[int..], "wweights":[float..],
                      "sdef":(C,R0,R1) 或 None,
                      "edge":float} ],
      "faces":   [v0, v1, v2, ...],          # 展平
      "textures":["相对路径", ...],
      "materials":[ {"name","name_en","diffuse":(4),"specular":(3),"shininess",
                     "ambient":(3),"flag","edge_color":(4),"edge_size",
                     "tex","sph","sph_mode","toon_flag","toon","memo","faces"} ],
      "bones":   [ {"name","name_en","pos":(3),"parent","layer","flag",
                     "tail_kind":0/1,"tail":(3)或int,
                     "inherit_rot":(bone,ratio)或None,
                     "inherit_mov":(bone,ratio)或None,
                     "fixed_axis":(3)或None,
                     "local_axis":(X,Z)或None,
                     "external":int 或 None,
                     "ik":{...}或None} ],
      "morphs":  [ {"name","name_en","panel","kind","offsets":[...]} ],
      "frames":  [ {"name","name_en","special","items":[(type,index)]} ],
      "rigid_bodies":[...],
      "joints":[...],
    }
"""
import os
import struct

_DBG = bool(os.environ.get("PMXIO_DEBUG"))

FLOAT = "f"
WEIGHT_BONES = {0: 1, 1: 2, 2: 4, 3: 2, 4: 4}
WEIGHT_FLOATS = {0: 0, 1: 1, 2: 4, 3: 1, 4: 4}

# PMX 头部的文本编码取值：0 = UTF-16LE，1 = UTF-8。
# MMD 本体只认 0（原版提示「エンコード方式がUTF16のPMXファイルしか読み込めません」，
# 汉化版误译成「不能载入编码为UTF16的PMX文件」，其实意思正好相反）。
# PMX Editor / mmd_tools 也都默认写 0，所以输出一律用 UTF-16LE。
ENC_UTF16 = 0
ENC_UTF8 = 1
MMD_ENC = ENC_UTF16


# ------------------------------------------------------------------ reader --
class _R:
    def __init__(self, d):
        self.d = d
        self.p = 0
        self.enc = 1
        # 当前解析到的段落/条目，仅用于出错时给出能定位的信息
        self.sec = "头部"

    def _fail(self, what):
        return "PMX 解析错位（段落：%s）：" % self.sec

    def _need(self, n, what):
        if self.p + n > len(self.d):
            raise ValueError(
                self._fail(what)
                + "读取「%s」时越过文件末尾（偏移 %d / 文件 %d 字节）。"
                  "可能是某一段的结构与写入程序不一致，请反馈此模型文件。"
                % (what, self.p, len(self.d)))

    def i32(self, what="int"):
        self._need(4, what)
        v = struct.unpack_from("<i", self.d, self.p)[0]
        self.p += 4
        return v

    def u8(self, what="byte"):
        self._need(1, what)
        v = self.d[self.p]
        self.p += 1
        return v

    def f32(self, what="float"):
        self._need(4, what)
        v = struct.unpack_from("<f", self.d, self.p)[0]
        self.p += 4
        return v

    def text(self, what="text"):
        self._need(4, what)
        n = self.i32(what)
        if n < 0 or self.p + n > len(self.d):
            raise ValueError(
                self._fail(what)
                + "「%s」长度 %d 超出文件剩余字节（偏移 %d / %d）。"
                % (what, n, self.p, len(self.d)))
        s = self.d[self.p:self.p + n]
        self.p += n
        return s.decode("utf-16-le" if self.enc == 0 else "utf-8", "replace")

    def idx(self, size, signed=True, what="index"):
        self._need(size, what)
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


_IDX = {1: "b", 2: "h", 4: "i"}                 # 索引宽度 → struct 格式（有符号；面的顶点索引用大写 = 无符号）


def read_pmx(path):
    with open(path, "rb") as f:
        d = f.read()
    if d[:4] != b"PMX ":
        raise ValueError("不是 PMX 文件：%s" % path)
    version = struct.unpack_from("<f", d, 4)[0]
    r = _R(d)
    r.p = 8
    gc = r.u8()
    g = [r.u8() for _ in range(gc)]
    while len(g) < 8:
        g.append(1)
    enc, add_uv, vi, ti, mi, bi, moi, ri = g[:8]
    r.enc = enc
    m = {"version": version, "enc": enc, "add_uv": add_uv,
         "sizes": (vi, ti, mi, bi, moi, ri)}
    m["name"] = r.text()
    m["name_en"] = r.text()
    m["comment"] = r.text()
    m["comment_en"] = r.text()

    # ---- vertices：每个顶点按固定布局批量解（逐个字段 struct.unpack 是读大模型时最慢的一段）
    r.sec = "顶点"
    verts = []
    head = struct.Struct("<%df" % (8 + 4 * add_uv))           # 位置、法线、UV、追加 UV
    bone_st = {n: struct.Struct("<%d%s" % (n, _IDX[bi])) for n in (1, 2, 4)}
    float_st = {n: struct.Struct("<%df" % n) for n in (1, 4, 9)}
    for vi_ in range(r.i32("vertex count")):
        r.sec = "顶点[%d]" % vi_
        r._need(head.size + 1, "vertex")
        v = head.unpack_from(d, r.p)
        r.p += head.size
        pos, nrm, uv = v[0:3], v[3:6], v[6:8]
        au = [v[8 + 4 * k:12 + 4 * k] for k in range(add_uv)]
        wt = d[r.p]
        r.p += 1
        nb = WEIGHT_BONES.get(wt, 1)
        nf = WEIGHT_FLOATS.get(wt, 0)
        st = bone_st[nb]
        r._need(st.size, "weight bones")
        bs = list(st.unpack_from(d, r.p))
        r.p += st.size
        ws = []
        if nf:
            st = float_st[nf]
            r._need(st.size, "weights")
            ws = list(st.unpack_from(d, r.p))
            r.p += st.size
        sdef = None
        if wt == 3:
            st = float_st[9]
            r._need(st.size, "SDEF")
            c = st.unpack_from(d, r.p)
            r.p += st.size
            sdef = (c[0:3], c[3:6], c[6:9])
        edge = r.f32()
        verts.append({"pos": pos, "normal": nrm, "uv": uv, "add_uv": au,
                      "wtype": wt, "wbones": bs, "wweights": ws,
                      "sdef": sdef, "edge": edge})
    m["vertices"] = verts

    # ---- faces（一次解完）
    r.sec = "面"
    nface = r.i32("face count")
    r._need(nface * vi, "faces")
    m["faces"] = list(struct.unpack_from("<%d%s" % (nface, _IDX[vi].upper()), d, r.p))
    r.p += nface * vi

    # ---- textures
    r.sec = "贴图"
    m["textures"] = [r.text() for _ in range(r.i32())]

    # ---- materials
    r.sec = "材质"
    mats = []
    for mi_ in range(r.i32("material count")):
        r.sec = "材质[%d]" % mi_
        mm = {}
        mm["name"] = r.text()
        mm["name_en"] = r.text()
        mm["diffuse"] = (r.f32(), r.f32(), r.f32(), r.f32())
        mm["specular"] = (r.f32(), r.f32(), r.f32())
        mm["shininess"] = r.f32()
        mm["ambient"] = (r.f32(), r.f32(), r.f32())
        mm["flag"] = r.u8()
        mm["edge_color"] = (r.f32(), r.f32(), r.f32(), r.f32())
        mm["edge_size"] = r.f32()
        mm["tex"] = r.idx(ti)
        mm["sph"] = r.idx(ti)
        mm["sph_mode"] = r.u8()
        mm["toon_flag"] = r.u8()
        # toon 的宽度**取决于 flag**，两个方向别搞反（PMX 规范 + mmd_tools 的
        # `is_shared_toon_texture` 都这么写）：
        #   flag = 1 → 共享/内建 toon（MMD 的 Data/toon01.bmp..toon10.bmp），
        #              字段是**1 字节编号**，**编号 0 对应 toon01.bmp**
        #              （mmd_tools: `"toon%02d.bmp" % (shared_toon_texture + 1)`；
        #                MMD 的 Data 目录里并没有 toon00.bmp）
        #   flag = 0 → 本模型纹理表里的贴图，字段是**纹理索引宽度（ti）**，
        #              **-1 = なし（不使用 toon）** ← 「不使用」只能这么写
        # mmd_tools: `if is_shared: self.toon_texture = fs.readSignedByte()
        #             else:      self.toon_texture = fs.readTextureIndex()`
        mm["toon"] = r.idx(ti) if mm["toon_flag"] == 0 else r.u8()
        mm["memo"] = r.text()
        mm["faces"] = r.i32()
        mats.append(mm)
    m["materials"] = mats

    # ---- bones
    r.sec = "骨骼"
    bones = []
    for bi_ in range(r.i32("bone count")):
        r.sec = "骨骼[%d]" % bi_
        b = {}
        b["name"] = r.text()
        b["name_en"] = r.text()
        b["pos"] = (r.f32(), r.f32(), r.f32())
        b["parent"] = r.idx(bi)
        b["layer"] = r.i32()
        r._need(2, "flag")
        flag = struct.unpack_from("<H", d, r.p)[0]
        r.p += 2
        b["flag"] = flag
        b["tail_kind"] = 1 if flag & 0x0001 else 0
        b["tail"] = r.idx(bi) if (flag & 0x0001) else (r.f32(), r.f32(), r.f32())
        b["inherit_rot"] = None
        b["inherit_mov"] = None
        b["fixed_axis"] = None
        b["local_axis"] = None
        b["external"] = None
        b["ik"] = None
        if flag & 0x0100 or flag & 0x0200:
            # PMX 仕様：付与親/付与率は「共通の 1 組」しか書かれない——
            # 付与回転(0x0100)と付与移動(0x0200)が同時でも 1 組だけ。
            # 按两个 flag 各读一遍会多读 6 字节，从第一根双付与骨开始整体错位
            # （实测小食Taberu：センター flag=0x031E 只有一组 (3, 1.0)）。
            gparent = r.idx(bi)
            grate = r.f32()
            if flag & 0x0100:
                b["inherit_rot"] = (gparent, grate)
            if flag & 0x0200:
                b["inherit_mov"] = (gparent, grate)
        if flag & 0x0400:
            b["fixed_axis"] = (r.f32(), r.f32(), r.f32())
        if flag & 0x0800:
            b["local_axis"] = ((r.f32(), r.f32(), r.f32()),
                               (r.f32(), r.f32(), r.f32()))
        if flag & 0x2000:
            # 外部親変形：只有 4 字节的 Key，后面没有索引字节
            b["external"] = r.i32()
        if flag & 0x0020:
            ik = {}
            ik["target"] = r.idx(bi)
            ik["iterations"] = r.i32()
            ik["limit_angle"] = r.f32()
            links = []
            for _ in range(r.i32()):
                lb = r.idx(bi)
                has = r.u8()
                if has:
                    links.append({"bone": lb,
                                  "min": (r.f32(), r.f32(), r.f32()),
                                  "max": (r.f32(), r.f32(), r.f32())})
                else:
                    links.append({"bone": lb, "min": None, "max": None})
            ik["links"] = links
            b["ik"] = ik
        bones.append(b)
    m["bones"] = bones

    # ---- morphs
    r.sec = "表情"
    if _DBG:
        print("[dbg] morphs start p=%d" % r.p)
    morphs = []
    # 顶点 / UV 表情的偏移是定长记录（无符号顶点索引 + 3 / 4 个 float），整段一次解
    vert_st = struct.Struct("<%s3f" % _IDX[vi].upper())
    uv_st = struct.Struct("<%s4f" % _IDX[vi].upper())
    for mo_ in range(r.i32("morph count")):
        r.sec = "表情[%d]" % mo_
        mo = {}
        mo["name"] = r.text()
        mo["name_en"] = r.text()
        mo["panel"] = r.u8()
        kind = r.u8()
        mo["kind"] = kind
        n = r.i32()
        offs = []
        if kind == 1 or kind in (3, 4, 5, 6, 7):     # 顶点：3 个偏移；UV / 追加UV：4 个
            st = vert_st if kind == 1 else uv_st
            size = max(n, 0) * st.size
            r._need(size, "morph offsets")
            offs =[(o[0], o[1:]) for o in st.iter_unpack(d[r.p:r.p + size])]
            r.p += size
            n = 0
        for _ in range(n):
            if kind == 2:                            # 骨骼
                offs.append((r.idx(bi),
                             (r.f32(), r.f32(), r.f32()),
                             (r.f32(), r.f32(), r.f32(), r.f32())))
            elif kind == 8:                          # 材质
                mat_i = r.idx(mi)
                op = r.u8()
                vals = [r.f32() for _ in range(28)]
                offs.append((mat_i, op, vals))
            elif kind == 0 or kind == 9:             # グループ / フリップ
                offs.append((r.idx(moi), r.f32()))
            elif kind == 10:                         # インパルス
                if _DBG:
                    print("[dbg] impulse morph %r n=%d @p=%d" % (mo["name"], n, r.p))
                # 剛体Index, ローカルフラグ(1byte), 移動速度(3), 回転トルク(3)
                offs.append((r.idx(ri), r.u8(),
                             (r.f32(), r.f32(), r.f32()),
                             (r.f32(), r.f32(), r.f32())))
            else:
                raise ValueError("未知的 PMX 表情类型 %d" % kind)
        mo["offsets"] = offs
        morphs.append(mo)
    m["morphs"] = morphs

    # ---- display frames
    r.sec = "表示枠"
    frames = []
    for fr_ in range(r.i32("frame count")):
        r.sec = "表示枠[%d]" % fr_
        if _DBG:
            print("[dbg] frame at p=%d" % r.p)
        fr = {}
        fr["name"] = r.text()
        fr["name_en"] = r.text()
        fr["special"] = r.u8()
        items = []
        for _ in range(r.i32()):
            t = r.u8()
            # 要素対象：0 = ボーン（ボーンIndex幅）, 1 = モーフ（モーフIndex幅）
            items.append((t, r.idx(bi) if t == 0 else r.idx(moi)))
        fr["items"] = items
        frames.append(fr)
    m["frames"] = frames

    # ---- rigid bodies
    r.sec = "刚体"
    if _DBG:
        print("[dbg] after frames p=%d (nframes=%d)" % (r.p, len(frames)))
    rbs = []
    for rb_ in range(r.i32("rigid body count")):
        r.sec = "刚体[%d]" % rb_
        rb = {}
        rb["name"] = r.text()
        rb["name_en"] = r.text()
        rb["bone"] = r.idx(bi)
        rb["group"] = r.u8()
        r._need(2, "mask")
        rb["mask"] = struct.unpack_from("<H", d, r.p)[0]
        r.p += 2
        rb["shape"] = r.u8()
        rb["size"] = (r.f32(), r.f32(), r.f32())
        rb["pos"] = (r.f32(), r.f32(), r.f32())
        rb["rot"] = (r.f32(), r.f32(), r.f32())
        rb["mass"] = r.f32()
        rb["move_damp"] = r.f32()
        rb["rot_damp"] = r.f32()
        rb["repulsion"] = r.f32()
        rb["friction"] = r.f32()
        rb["mode"] = r.u8()
        rbs.append(rb)
    m["rigid_bodies"] = rbs

    # ---- joints
    r.sec = "关节"
    jts = []
    for jt_ in range(r.i32("joint count")):
        r.sec = "关节[%d]" % jt_
        j = {}
        j["name"] = r.text()
        j["name_en"] = r.text()
        j["type"] = r.u8()
        j["rb_a"] = r.idx(ri)
        j["rb_b"] = r.idx(ri)
        j["pos"] = (r.f32(), r.f32(), r.f32())
        j["rot"] = (r.f32(), r.f32(), r.f32())
        j["move_min"] = (r.f32(), r.f32(), r.f32())
        j["move_max"] = (r.f32(), r.f32(), r.f32())
        # PMX 2.0 关节是 8 个 vec3：位置/旋转/移动下限/移动上限/
        #                        旋转下限/旋转上限/移动弹簧/旋转弹簧
        j["rot_min"] = (r.f32(), r.f32(), r.f32())
        j["rot_max"] = (r.f32(), r.f32(), r.f32())
        j["spring_move"] = (r.f32(), r.f32(), r.f32())
        j["spring_rot"] = (r.f32(), r.f32(), r.f32())
        jts.append(j)
    m["joints"] = jts

    m["consumed"] = r.p
    m["filesize"] = len(d)

    # PMX 2.1 在关节之后还有一段 SoftBody
    # 注意：float32 的 2.1 读回来是 2.0999999...，直接写 >= 2.1 永远不成立，
    # 必须用容差比较（否则 PMX 2.1 文件末尾会剩 4 字节 SoftBody 计数读不到）。
    if version > 2.05:
        if r.p == len(d):
            # 少数工具（如 MikuMikuDayo 的粒子 PMX）标着 2.1 却没写
            # SoftBody 计数字段，容错当作 0 处理，别一棒子打死。
            m["soft_bodies"] = 0
        else:
            r._need(4, "softbody count")
            nsoft = r.i32("softbody count")
            m["soft_bodies"] = nsoft
            m["consumed"] = r.p
            if nsoft:
                raise ValueError(
                    "该模型是 PMX 2.1 且含 %d 个 SoftBody，本转换器暂不支持 SoftBody。"
                    % nsoft)

    if m["consumed"] != m["filesize"]:
        raise ValueError(
            "PMX 解析后仍有 %d 字节未读完（已读 %d / 共 %d），"
            "说明某一段的结构与写入程序不一致，请反馈此模型文件。"
            % (m["filesize"] - m["consumed"], m["consumed"], m["filesize"]))
    return m


# ------------------------------------------------------------------ writer --
def _isize(count, signed=True):
    """按数量挑索引字节宽度。"""
    if signed:
        if count <= 127:
            return 1
        if count <= 32767:
            return 2
        return 4
    if count <= 255:
        return 1
    if count <= 65535:
        return 2
    return 4


class _W:
    def __init__(self):
        self.buf = bytearray()

    def i32(self, v):
        self.buf += struct.pack("<i", v)

    def u8(self, v):
        self.buf += struct.pack("<B", v)

    def u16(self, v):
        self.buf += struct.pack("<H", v)

    def f32(self, v):
        self.buf += struct.pack("<f", v)

    def text(self, s, enc):
        b = (s or "").encode("utf-16-le" if enc == 0 else "utf-8")
        self.i32(len(b))
        self.buf += b

    def idx(self, v, size, signed=True):
        if size == 1:
            self.buf += struct.pack("<b" if signed else "<B", v)
        elif size == 2:
            self.buf += struct.pack("<h" if signed else "<H", v)
        else:
            self.i32(v)


def write_pmx(model, path):
    m = model
    verts = m["vertices"]
    faces = m["faces"]
    mats = m["materials"]
    bones = m["bones"]
    morphs = m["morphs"]

    nv = len(verts)
    vi = _isize(nv + 1, signed=False)
    ti = _isize(len(m["textures"]) + 1)
    mi = _isize(len(mats) + 1)
    bi = _isize(len(bones) + 1)
    moi = _isize(len(morphs) + 1)
    ri = _isize(max(len(m.get("rigid_bodies") or []), 1) + 1)

    w = _W()
    enc = m.get("enc", MMD_ENC)
    add_uv = m.get("add_uv", 0)
    w.buf += b"PMX "
    w.f32(m.get("version", 2.0))
    w.u8(8)
    for b in (enc, add_uv, vi, ti, mi, bi, moi, ri):
        w.u8(b)
    w.text(m.get("name", "model"), enc)
    w.text(m.get("name_en", m.get("name", "model")), enc)
    w.text(m.get("comment", ""), enc)
    w.text(m.get("comment_en", ""), enc)

    w.i32(nv)
    for v in verts:
        w.f32(v["pos"][0]); w.f32(v["pos"][1]); w.f32(v["pos"][2])
        w.f32(v["normal"][0]); w.f32(v["normal"][1]); w.f32(v["normal"][2])
        w.f32(v["uv"][0]); w.f32(v["uv"][1])
        for _ in range(add_uv):
            for k in range(4):
                au = v.get("add_uv") or []
                w.f32(au[_][k] if _ < len(au) else 0.0)
        wt = v["wtype"]
        w.u8(wt)
        nb = WEIGHT_BONES.get(wt, 1)
        bs = list(v["wbones"]) + [-1] * nb
        for k in range(nb):
            w.idx(bs[k] if bs[k] >= 0 else 0, bi)
        if wt in (1, 3):
            ws = list(v["wweights"]) + [0.0]
            w.f32(ws[0])
        elif wt in (2, 4):
            ws = list(v["wweights"]) + [0.0] * 4
            for k in range(4):
                w.f32(ws[k])
        if wt == 3 and v.get("sdef"):
            for t in v["sdef"]:
                for k in range(3):
                    w.f32(t[k])
        elif wt == 3:
            for _ in range(9):
                w.f32(0.0)
        w.f32(v.get("edge", 1.0))

    w.i32(len(faces))
    for f in faces:
        w.idx(f, vi, signed=False)

    w.i32(len(m["textures"]))
    for t in m["textures"]:
        w.text(t, enc)

    w.i32(len(mats))
    for mm in mats:
        w.text(mm["name"], enc)
        w.text(mm.get("name_en", mm["name"]), enc)
        for k in range(4):
            w.f32(mm["diffuse"][k])
        for k in range(3):
            w.f32(mm["specular"][k])
        w.f32(mm["shininess"])
        for k in range(3):
            w.f32(mm["ambient"][k])
        w.u8(mm["flag"])
        for k in range(4):
            w.f32(mm["edge_color"][k])
        w.f32(mm["edge_size"])
        w.idx(mm["tex"], ti)
        w.idx(mm["sph"], ti)
        w.u8(mm["sph_mode"])
        w.u8(mm["toon_flag"])
        # 宽度随 flag 变（见读取侧注释）：flag=1 → 1 字节内建 toon 号；
        # flag=0 → 纹理索引宽度。
        if mm["toon_flag"] == 0:
            w.idx(mm["toon"], ti)
        else:
            w.u8(mm["toon"])
        w.text(mm.get("memo", ""), enc)
        w.i32(mm["faces"])

    w.i32(len(bones))
    for b in bones:
        w.text(b["name"], enc)
        w.text(b.get("name_en", b["name"]), enc)
        for k in range(3):
            w.f32(b["pos"][k])
        w.idx(b["parent"], bi)
        w.i32(b.get("layer", 0))
        flag = b["flag"]
        w.u16(flag)
        if flag & 0x0001:
            w.idx(b["tail"], bi)
        else:
            for k in range(3):
                w.f32(b["tail"][k])
        if flag & 0x0100 or flag & 0x0200:
            # 与读取侧同理：付与親/付与率只写共通的一组。
            # 两个 flag 同开时两组值理应一致；万一不一致（旧版本写出的文件），
            # 以回転付与为准，避免丢信息。
            if flag & 0x0100:
                gp, gr = b["inherit_rot"]
            else:
                gp, gr = b["inherit_mov"]
            w.idx(gp, bi)
            w.f32(gr)
        if flag & 0x0400:
            for k in range(3):
                w.f32(b["fixed_axis"][k])
        if flag & 0x0800:
            for t in b["local_axis"]:
                for k in range(3):
                    w.f32(t[k])
        if flag & 0x2000:
            ext = b["external"]
            w.i32(ext if isinstance(ext, int) else (ext or (0,))[0])
        if flag & 0x0020:
            ik = b["ik"]
            w.idx(ik["target"], bi)
            w.i32(ik["iterations"])
            w.f32(ik["limit_angle"])
            w.i32(len(ik["links"]))
            for lk in ik["links"]:
                w.idx(lk["bone"], bi)
                if lk.get("min"):
                    w.u8(1)
                    for k in range(3):
                        w.f32(lk["min"][k])
                    for k in range(3):
                        w.f32(lk["max"][k])
                else:
                    w.u8(0)

    w.i32(len(morphs))
    for mo in morphs:
        w.text(mo["name"], enc)
        w.text(mo.get("name_en", mo["name"]), enc)
        w.u8(mo["panel"])
        kind = mo["kind"]
        w.u8(kind)
        w.i32(len(mo["offsets"]))
        for off in mo["offsets"]:
            if kind == 1:
                w.idx(off[0], vi, signed=False)
                for k in range(3):
                    w.f32(off[1][k])
            elif kind in (3, 4, 5, 6, 7):
                w.idx(off[0], vi, signed=False)
                for k in range(4):
                    w.f32(off[1][k])
            elif kind == 2:
                w.idx(off[0], bi)
                for k in range(3):
                    w.f32(off[1][k])
                for k in range(4):
                    w.f32(off[2][k])
            elif kind == 8:
                w.idx(off[0], mi)
                w.u8(off[1])
                for x in off[2]:
                    w.f32(x)
            elif kind == 0 or kind == 9:
                w.idx(off[0], moi)
                w.f32(off[1])
            elif kind == 10:
                w.idx(off[0], ri)
                for k in range(3):
                    w.f32(off[1][k])
                for k in range(3):
                    w.f32(off[2][k])

    frames = m.get("frames") or []
    w.i32(len(frames))
    for fr in frames:
        w.text(fr["name"], enc)
        w.text(fr.get("name_en", fr["name"]), enc)
        w.u8(fr.get("special", 0))
        w.i32(len(fr["items"]))
        for t, i in fr["items"]:
            w.u8(t)
            w.idx(i, bi if t == 0 else moi)

    rbs = m.get("rigid_bodies") or []
    w.i32(len(rbs))
    for rb in rbs:
        w.text(rb["name"], enc)
        w.text(rb.get("name_en", rb["name"]), enc)
        w.idx(rb["bone"], bi)
        w.u8(rb["group"])
        w.u16(rb["mask"])
        w.u8(rb["shape"])
        for k in range(3):
            w.f32(rb["size"][k])
        for k in range(3):
            w.f32(rb["pos"][k])
        for k in range(3):
            w.f32(rb["rot"][k])
        w.f32(rb["mass"])
        w.f32(rb["move_damp"])
        w.f32(rb["rot_damp"])
        w.f32(rb["repulsion"])
        w.f32(rb["friction"])
        w.u8(rb["mode"])

    jts = m.get("joints") or []
    w.i32(len(jts))
    for j in jts:
        w.text(j["name"], enc)
        w.text(j.get("name_en", j["name"]), enc)
        w.u8(j["type"])
        w.idx(j["rb_a"], ri)
        w.idx(j["rb_b"], ri)
        for k in range(3):
            w.f32(j["pos"][k])
        for k in range(3):
            w.f32(j["rot"][k])
        for k in range(3):
            w.f32(j["move_min"][k])
        for k in range(3):
            w.f32(j["move_max"][k])
        rm = j.get("rot_min") or (0.0, 0.0, 0.0)
        rx = j.get("rot_max") or (0.0, 0.0, 0.0)
        for k in range(3):
            w.f32(rm[k])
        for k in range(3):
            w.f32(rx[k])
        for k in range(3):
            w.f32(j["spring_move"][k])
        for k in range(3):
            w.f32(j["spring_rot"][k])

    # PMX 2.1 需要写 SoftBody 计数（本转换器不支持 SoftBody，恒写 0）
    if m.get("version", 2.0) > 2.05:
        w.i32(0)

    with open(path, "wb") as f:
        f.write(w.buf)
    return len(w.buf)


# ------------------------------------------------------- 缠绕顺序自动判定 --
def detect_winding(positions, normals, faces, sample=4000):
    """判断三角形的缠绕方向是否与法线一致。

    positions: [(x,y,z), ...]
    normals  : [(x,y,z), ...]  与 positions 一一对应
    faces    : [i0,i1,i2, ...] 展平的索引

    返回 (need_flip, agree, disagree)：
      need_flip=True 表示按现在的顺序算出来的几何法线与顶点法线相反，
      应该交换后两个索引。

    实测：PMX 与 glTF 的原始数据都是「从外面看逆时针」，几何法线与顶点法线同向。
    注意（2026-09-18）：这只对「未经手性修正的原始数据」成立。
    pmx2vrm 做镜面反射（改手性）之后，绕序会被颠倒，
    所以转换器里的 auto 判定必须用「反射后的数据」重新投票，
    那时会判「需要反转」——这是正确的，别因此改回不反转。
    以前硬编码「必须反转」是错的，会让模型在 MMD / VRM 查看器里被背面剔除，
    表现就是大面积镂空 + 轮廓线糊成一片黑。
    """
    n_tri = len(faces) // 3
    if n_tri == 0 or not normals or len(normals) < len(positions):
        return False, 0, 0
    step = 1 if n_tri <= sample else (n_tri + sample - 1) // sample
    agree = disagree = 0
    for t in range(0, n_tri, step):
        a = faces[t * 3]
        b = faces[t * 3 + 1]
        c = faces[t * 3 + 2]
        if a >= len(positions) or b >= len(positions) or c >= len(positions):
            continue
        pa, pb, pc = positions[a], positions[b], positions[c]
        e1 = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        e2 = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        gn = (e1[1] * e2[2] - e1[2] * e2[1],
              e1[2] * e2[0] - e1[0] * e2[2],
              e1[0] * e2[1] - e1[1] * e2[0])
        if abs(gn[0]) + abs(gn[1]) + abs(gn[2]) < 1e-12:
            continue
        na = normals[a]
        nb = normals[b]
        nc = normals[c]
        vn = (na[0] + nb[0] + nc[0], na[1] + nb[1] + nc[1],
              na[2] + nb[2] + nc[2])
        d = gn[0] * vn[0] + gn[1] * vn[1] + gn[2] * vn[2]
        if d > 0:
            agree += 1
        elif d < 0:
            disagree += 1
    if agree + disagree == 0:
        return False, 0, 0
    return (disagree > agree), agree, disagree


# ------------------------------------------------------------- 空模型工具 --
def new_model(name="Model"):
    return {
        "version": 2.0, "enc": MMD_ENC, "add_uv": 0,
        "name": name, "name_en": name, "comment": "", "comment_en": "",
        "vertices": [], "faces": [], "textures": [], "materials": [],
        "bones": [], "morphs": [], "frames": [],
        "rigid_bodies": [], "joints": [],
    }


def strip_edges(model, force_double_sided=True):
    """去除 PMX 的黑色轮廓线（MMD 轮廓线 / edge line）。

    对输出到 MMD 后常见的“模型边缘出现黑边”问题，把材质的轮廓线标记、
    粗细、颜色 alpha 全部关闭，并把每个顶点的 edge 系数置 0。
    可选同时强制开启双面描绘，减少因背面剔除产生的黑缝/镂空。
    """
    for v in model.get("vertices", []):
        v["edge"] = 0.0
    for mm in model.get("materials", []):
        mm["flag"] = (mm.get("flag", 0x0F) & ~0x10)
        if force_double_sided:
            mm["flag"] |= 0x01
        mm["edge_size"] = 0.0
        ec = list(mm.get("edge_color") or (0.0, 0.0, 0.0, 1.0))
        while len(ec) < 4:
            ec.append(1.0)
        ec[3] = 0.0
        mm["edge_color"] = tuple(ec)
    return model


# ------------------------------------------------------- 文本编码转换工具 --
def convert_encoding(src, dst=None, enc=MMD_ENC, verbose=True):
    """把已有 PMX 的文本编码改成 enc（默认 UTF-16LE —— MMD 唯一认的编码）。

    用于修复早先版本导出的 UTF-8 PMX：MMD 会报
    「MMDではエンコード方式がUTF16のPMXファイルしか読み込めません」而拒绝载入。
    不传 dst 时写到同目录的 "<名字>_utf16.pmx"（不覆盖原文件）。
    返回 (输出路径, 字节数)。
    """
    model = read_pmx(src)
    old = model.get("enc", ENC_UTF8)
    model["enc"] = enc
    if dst is None:
        stem, _ext = os.path.splitext(src)
        dst = stem + ("_utf16.pmx" if enc == ENC_UTF16 else "_utf8.pmx")
    tmp = dst + ".tmp"
    n = write_pmx(model, tmp)
    # 回读校验：能完整解析、名称一致才认账，避免把文件改坏
    back = read_pmx(tmp)
    if back["name"] != model["name"] or back["consumed"] != back["filesize"]:
        os.remove(tmp)
        raise ValueError("改写后回读校验失败，未写入：%s" % src)
    os.replace(tmp, dst)
    if verbose:
        print("%-40s 编码 %d(%s) -> %d(%s)  %.2f MB" % (
            os.path.basename(src), old, "UTF-8" if old else "UTF-16LE",
            enc, "UTF-8" if enc else "UTF-16LE", n / 1048576.0))
    return dst, n


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(
        description="把 PMX 的文本编码改成 MMD 能读的那种（默认 UTF-16LE）")
    ap.add_argument("files", nargs="+", help="要处理的 .pmx")
    ap.add_argument("--enc", choices=("utf16", "utf8"), default="utf16",
                    help="目标编码，默认 utf16（MMD 只认这个）")
    ap.add_argument("-o", "--out", default=None, help="输出路径（仅限单个输入）")
    ap.add_argument("--in-place", action="store_true",
                    help="直接覆盖原文件（危险，建议先备份）")
    a = ap.parse_args()
    if a.out and len(a.files) > 1:
        ap.error("-o 只能配合单个输入文件使用")

    want = ENC_UTF16 if a.enc == "utf16" else ENC_UTF8
    for p in a.files:
        try:
            convert_encoding(p, a.out or (p if a.in_place else None), want)
        except Exception as e:
            print("失败 %s：%s" % (p, e))
