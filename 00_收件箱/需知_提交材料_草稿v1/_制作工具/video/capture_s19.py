"""补拍 S19：隐盾页从「进模型前脱敏 N 处」到沙箱策略表（不含页头）。

用法：python capture_s19.py <app_url> <out_dir>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import wait_idle  # noqa: E402

URL, OUT = sys.argv[1], Path(sys.argv[2])
VW = 1366


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
        ctx = b.new_context(viewport={"width": VW, "height": 900}, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_selector("text=需知，开工", timeout=60000)
        pg.wait_for_timeout(1500)
        pg.locator('[data-testid="stSelectbox"]').first.click()
        pg.wait_for_timeout(500)
        pg.locator('[role="option"]').filter(has_text="03_").first.click()
        pg.wait_for_function("() => { const t = document.querySelector('textarea'); return t && t.value.length > 20; }", timeout=30000)
        pg.wait_for_timeout(600)
        pg.get_by_role("button", name="需知，开工").click()
        pg.wait_for_function("() => document.body.textContent.includes('<手机_1>')", timeout=90000)
        pg.wait_for_timeout(1500)
        wait_idle(pg)
        pg.set_viewport_size({"width": VW, "height": 3000})
        pg.wait_for_timeout(800)
        pg.get_by_role("tab", name="隐盾").click()
        pg.wait_for_timeout(1500)
        top = pg.get_by_text(re.compile(r"进模型前脱敏 \d+ 处")).filter(visible=True).first
        table = pg.locator('[data-testid="stDataFrame"]').filter(visible=True).last
        top.scroll_into_view_if_needed()
        pg.wait_for_timeout(600)
        tb, bb = top.bounding_box(), table.bounding_box()
        y = max(0, tb["y"] - 14)
        h = min(1500, bb["y"] + bb["height"] - y + 16)
        pg.screenshot(path=str(OUT / "S19_shield.png"), clip={"x": 40, "y": y, "width": VW - 80, "height": h})
        print("ok S19_shield", int(h), "top text:", top.inner_text()[:40])
        b.close()


if __name__ == "__main__":
    main()
