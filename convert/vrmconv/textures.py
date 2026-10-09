# -*- coding: utf-8 -*-
"""贴图瘦身：限制最大边长、不透明贴图改存 JPEG。

HoYo 系 PMX 的贴图常是 2K～4K 的 PNG，原样塞进 VRM 文件动辄上百 MB，游戏导入和加载都慢。
缩放和 JPEG 编码要用 Pillow（可选依赖）；没装 Pillow 时原样返回，转换照常进行。

只有完全不透明的贴图才转 JPEG：带透明通道的（头发高光、睫毛、镂空）JPEG 存不了 alpha。
"""
import io

try:
    from PIL import Image
except ImportError:          # 纯标准库环境：不瘦身
    Image = None

JPEG_QUALITY = 90


def available():
    return Image is not None


class Optimizer:
    """max_size：最大边长（0 = 不缩放）；jpeg：不透明贴图改存 JPEG。统计在 before / after / changed。"""

    def __init__(self, max_size=0, jpeg=False):
        self.max_size = int(max_size or 0)
        self.jpeg = bool(jpeg)
        self.before = self.after = self.changed = 0

    @property
    def active(self):
        return Image is not None and (self.max_size > 0 or self.jpeg)

    def __call__(self, data, mime):
        """→ (data, mime)。处理失败或没变小时返回原数据。"""
        return self.account(data, self.process(data, mime))

    def process(self, data, mime):
        """只做处理、不计数（几张贴图可以在不同线程里同时处理），计数交给 account。"""
        if self.active:
            try:
                return self._process(data, mime)
            except Exception:  # noqa: BLE001  坏图 / 不支持的格式：原样用
                pass
        return data, mime

    def account(self, data, out):
        """把一张贴图 data → out（process 的结果）记进统计，返回 out。"""
        self.before += len(data)
        self.after += len(out[0])
        if out[0] is not data:
            self.changed += 1
        return out

    def _process(self, data, mime):
        im = Image.open(io.BytesIO(data))
        im.load()
        w, h = im.size
        resized = False
        if self.max_size and max(w, h) > self.max_size:
            k = self.max_size / float(max(w, h))
            if im.mode not in ("RGB", "RGBA", "L", "LA"):
                im = im.convert("RGBA")
            im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
            resized = True
        opaque = True
        if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
            a = im.convert("RGBA").getchannel("A")
            opaque = a.getextrema()[0] >= 255
        if self.jpeg and opaque:
            buf = io.BytesIO()
            # 4:4:4 不做色度抽样：二次元贴图的细线和色块边缘抽样后会发糊、渗色
            im.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
            if resized or buf.tell() < len(data):
                return buf.getvalue(), "image/jpeg"
        if not resized:
            return data, mime
        buf = io.BytesIO()
        if mime == "image/jpeg" and opaque:
            im.convert("RGB").save(buf, "JPEG", quality=JPEG_QUALITY, subsampling=0, optimize=True)
            return buf.getvalue(), "image/jpeg"
        if opaque and im.mode == "RGBA":
            im = im.convert("RGB")
        im.save(buf, "PNG", optimize=True)
        return buf.getvalue(), "image/png"

    def summary(self):
        if not self.changed:
            return None
        return "贴图：%d 张瘦身（%s%s）%.1f MB → %.1f MB" % (
            self.changed, ("最大边长 %d" % self.max_size) if self.max_size else "",
            ("、" if self.max_size and self.jpeg else "") + ("不透明转 JPEG" if self.jpeg else ""),
            self.before / 1048576.0, self.after / 1048576.0)
