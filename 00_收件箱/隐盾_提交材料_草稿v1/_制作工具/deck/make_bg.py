"""隐盾 deck 背景：近黑深蓝底、淡网格、绿 / 青光晕（与隐盾界面一致）。用法：python make_bg.py assets"""
import sys
from PIL import Image, ImageDraw, ImageFilter
out = sys.argv[1]
W, H = 2667, 1500   # 13.333 x 7.5 in @ 200 dpi

def base():
    img = Image.new("RGB", (W, H), (7, 11, 22))
    d = ImageDraw.Draw(img, "RGBA")
    step = 46
    for x in range(0, W, step):
        d.line([(x, 0), (x, H)], fill=(96, 165, 250, 12), width=1)
    for y in range(0, H, step):
        d.line([(0, y), (W, y)], fill=(96, 165, 250, 12), width=1)
    return img

def glow(img, cx, cy, r, rgb, alpha):
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=rgb + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(r * 0.55))
    img.paste(layer, (0, 0), layer)

# Title / closing: strong glows
t = base()
glow(t, int(W * .10), int(H * -.05), 760, (52, 211, 153), 70)
glow(t, int(W * .95), int(H * .02), 700, (34, 211, 238), 70)
glow(t, int(W * .60), int(H * 1.10), 820, (67, 97, 238), 60)
t.save(f"{out}/bg_title.png", optimize=True)

# Section divider: one cyan glow left
s = base()
glow(s, int(W * .05), int(H * .50), 820, (52, 211, 153), 55)
glow(s, int(W * 1.0), int(H * 1.0), 640, (34, 211, 238), 45)
s.save(f"{out}/bg_section.png", optimize=True)

# Content: subtle glows only at the top corners
c = base()
glow(c, int(W * .04), int(H * -.12), 620, (52, 211, 153), 30)
glow(c, int(W * .98), int(H * -.10), 620, (34, 211, 238), 30)
c.save(f"{out}/bg_content.png", optimize=True)
print("ok")
