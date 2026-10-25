"""PPT 用的局部截图：从 shots/S*.png（3200×2000）裁出右栏结果区，存成 shots/P*.png；
示意图去掉自带的标题行，存成 deck/figs/F*_body.png（幻灯片标题已经说了）。
整屏截图放进幻灯片字太小，右栏放大后投屏看得清。用法：python scripts/crop_shots.py shots deck/figs"""
import sys
from pathlib import Path

from PIL import Image

SHOTS = Path(sys.argv[1])
RIGHT = (1940, 440, 3070)          # 右栏：x 起、y 起、x 止；下边按内容自动找（tabs 下方开始）
CROPS = {   # 输出名: (源图, 区域)
    "P02_redact": ("S02_redact", (1940, 440, 3070, 1310)), "P02_src": ("S02_redact", (750, 450, 1850, 790)), "P03_compare": ("S03_compare", RIGHT), "P05_copy": ("S05_copy", (1940, 0, 3070)),
    "P06_restore": ("S06_restore", RIGHT), "P07_round2": ("S07_round2", RIGHT), "P08_restore_warn": ("S08_restore_warn", RIGHT),
    "P09_ops": ("S09_ops", RIGHT), "P10_suspect": ("S10_suspect", RIGHT), "P09_ops_src": ("S09_ops", (740, 440, 1860)),
}


def content_bottom(im: Image.Image, x0: int, x1: int, y0: int) -> int:
    """从下往上找最后一行有内容（亮像素够多）的位置，再留 30px 边。"""
    g = im.convert("L")
    w = x1 - x0
    for y in range(im.height - 1, y0, -4):
        row = list(g.crop((x0, y, x1, y + 1)).tobytes())
        if sum(1 for v in row if v > 70) > w * 0.01:
            return min(im.height, y + 30)
    return im.height


for name, (src, box) in CROPS.items():
    im = Image.open(SHOTS / f"{src}.png")
    x0, y0, x1 = box[:3]
    y1 = box[3] if len(box) > 3 else content_bottom(im, x0, x1, y0)   # 给了第 4 个数就按固定下边裁
    im.crop((x0, y0, x1, y1)).save(SHOTS / f"{name}.png", optimize=True)
    print(name, (x0, y0, x1, y1))

if len(sys.argv) > 2:
    figs = Path(sys.argv[2])
    for fid in ("F01_flow", "F02_engine"):
        im = Image.open(figs / f"{fid}.png")
        im.crop((0, 196, im.width, im.height)).save(figs / f"{fid}_body.png", optimize=True)
        print(f"{fid}_body", im.width, im.height - 196)
