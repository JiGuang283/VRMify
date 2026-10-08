# -*- coding: utf-8 -*-
"""生成程序图标：圆角方块（界面的强调蓝）+ 白色菱形，和界面左上角的「◆」一致。

    python tools/make_icon.py        → ui/icon.ico（exe 图标，多尺寸）+ ui/web/icon.png（页面 favicon）

需要 Pillow。按 4 倍分辨率画再缩小，边缘是平滑的。
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOP, BOTTOM = (0x4F, 0x86, 0xF5), (0x2F, 0x5C, 0xD6)       # 界面的 --accent #3a6ee8 上下各偏一点
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def draw(size):
    k = 4
    n = size * k
    img = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    # 竖直渐变的底
    grad = Image.new("RGBA", (1, n))
    for y in range(n):
        t = y / max(1, n - 1)
        grad.putpixel((0, y), tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,))
    grad = grad.resize((n, n))
    mask = Image.new("L", (n, n), 0)
    pad = round(n * 0.04)
    ImageDraw.Draw(mask).rounded_rectangle((pad, pad, n - 1 - pad, n - 1 - pad), radius=round(n * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)
    # 白色菱形（小尺寸时画大一点，免得糊成一个点）
    d = ImageDraw.Draw(img)
    c = n / 2
    r = n * (0.30 if size >= 32 else 0.34)
    d.polygon([(c, c - r), (c + r, c), (c, c + r), (c - r, c)], fill=(255, 255, 255, 255))
    if size >= 48:                     # 大图标：菱形中间挖一个小菱形，多一点层次
        r2 = r * 0.38
        d.polygon([(c, c - r2), (c + r2, c), (c, c + r2), (c - r2, c)],
                  fill=tuple(round((TOP[i] + BOTTOM[i]) / 2) for i in range(3)) + (255,))
    return img.resize((size, size), Image.LANCZOS)


def main():
    imgs = [draw(s) for s in SIZES]
    ico = os.path.join(ROOT, "ui", "icon.ico")
    imgs[-1].save(ico, sizes=[(s, s) for s in SIZES], append_images=imgs[:-1])
    png = os.path.join(ROOT, "ui", "web", "icon.png")
    draw(64).save(png, optimize=True)
    print(ico)
    print(png)


if __name__ == "__main__":
    main()
