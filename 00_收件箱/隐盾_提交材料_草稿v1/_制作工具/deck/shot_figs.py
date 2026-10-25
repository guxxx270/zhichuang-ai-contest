"""figs.html → figs/F01_flow.png、figs/F02_engine.png（3200×1800）。用法：python shot_figs.py"""
from pathlib import Path

from playwright.sync_api import sync_playwright

here = Path(__file__).resolve().parent
(here / "figs").mkdir(exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
    for fid in ("F01_flow", "F02_engine"):
        pg.goto("about:blank")   # 只改锚点不会重新加载页面
        pg.goto(f"file://{here}/figs.html#{fid}")
        pg.wait_for_timeout(500)
        pg.screenshot(path=str(here / "figs" / f"{fid}.png"))
        print("wrote", fid)
    b.close()
