"""续拍：沿用 final/ledger.sqlite3（主流程与模型调用已完成），只拍 S14 版本对比、S20 审计指标、S15～S19。不调模型。

用法：python capture2.py <app_url> <out_dir>
"""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture import bordered, wait_idle  # noqa: E402

URL, OUT = sys.argv[1], Path(sys.argv[2])
VW, VH = 1366, 900


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
        ctx = b.new_context(viewport={"width": VW, "height": VH}, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_selector("text=需知，开工", timeout=60000)
        pg.wait_for_timeout(1500)

        def tall(h: int):
            pg.set_viewport_size({"width": VW, "height": h})
            pg.wait_for_timeout(700)

        def shot(name, top, bottom=None, pad_top=12, pad_bottom=16, max_h=2400):
            top.scroll_into_view_if_needed()
            pg.wait_for_timeout(500)
            tb, bb = top.bounding_box(), (bottom or top).bounding_box()
            if not tb or not bb:
                print("miss", name); return
            y = max(0, tb["y"] - pad_top)
            h = min(max_h, bb["y"] + bb["height"] - y + pad_bottom, pg.viewport_size["height"] - y)
            pg.screenshot(path=str(OUT / f"{name}.png"), clip={"x": 40, "y": y, "width": VW - 80, "height": h})
            print("ok", name, int(h))

        def to_top():
            pg.evaluate("() => { document.querySelectorAll('[data-testid=\"stMain\"],[data-testid=\"stAppViewContainer\"]').forEach(e=>e.scrollTop=0); }")
            pg.wait_for_timeout(500)

        def choose(prefix: str) -> bool:
            pg.locator('[data-testid="stSelectbox"]').first.click()
            pg.wait_for_timeout(500)
            opt = pg.locator('[role="option"]').filter(has_text=prefix).first
            if not opt.count():
                pg.keyboard.press("Escape"); return False
            opt.click()
            pg.wait_for_function("() => { const t = document.querySelector('textarea'); return t && t.value.length > 20; }", timeout=30000)
            pg.wait_for_timeout(600)
            return True

        def start_and_wait(expect: str):
            pg.get_by_role("button", name="需知，开工").click()
            pg.wait_for_function("(t) => document.body.textContent.includes(t)", arg=expect, timeout=90000)
            pg.wait_for_timeout(1500)
            wait_idle(pg)

        # ---- 首页「查看一本账」：S14 版本对比、S20 审计指标 ----
        tall(2600)
        pg.get_by_text("查看一本账").first.click()
        pg.wait_for_timeout(2000)
        wait_idle(pg)
        pick = pg.locator('[data-testid="stSelectbox"]').filter(has_text="#").first
        if pick.count():
            pick.click(); pg.wait_for_timeout(500)
            o = pg.locator('[role="option"]').filter(has_text="净值日报改版").first
            if o.count():
                o.click(); pg.wait_for_timeout(1500); wait_idle(pg)
        ver = pg.get_by_text("版本记录").filter(visible=True).first
        if ver.count():
            ver.click(); pg.wait_for_timeout(1800); wait_idle(pg)
            exp = ver.locator("xpath=ancestor::*[@data-testid='stExpander'][1]")
            shot("S14_ledger_versions", exp, max_h=1700)
        audit_h = pg.locator("h4").filter(has_text="模型调用审计").filter(visible=True).first
        if audit_h.count():
            mets = audit_h.locator("xpath=following::*[@data-testid='stMetric']")
            if mets.count():
                shot("S20_audit", audit_h, mets.nth(min(4, mets.count() - 1)), max_h=700)
        pg.get_by_text("查看一本账").first.click()
        pg.wait_for_timeout(1500)

        # ---- S15 追问记忆（样例⑤）----
        tall(VH); to_top()
        if choose("05_"):
            start_and_wait("沿用上次口径")
            tall(2600)
            pg.get_by_role("tab", name="问清").click(); pg.wait_for_timeout(1400)
            head = pg.get_by_text("待确认清单").filter(visible=True).first
            mem = pg.locator(".xz-q").filter(has=pg.locator(".xz-chip.mem"))
            shot("S15_memory", head, mem.nth(min(1, max(0, mem.count() - 1))) if mem.count() else pg.locator(".xz-q").nth(2), max_h=1300)

        # ---- S16 纪要直通车（样例⑥）----
        tall(VH); to_top()
        if choose("06_"):
            start_and_wait("纪要直通车")
            tall(2600)
            split = pg.get_by_role("button").filter(has_text="分别受理").first
            if split.count():
                split.click(); pg.wait_for_timeout(2500); wait_idle(pg)
            card = bordered(pg, "纪要直通车")
            if card is not None:
                shot("S16_minutes_split", card, max_h=1300)

        # ---- S17 / S18 Jira ----
        tall(VH); to_top()
        pg.get_by_text("Jira 需求评估").first.click(); pg.wait_for_timeout(1500)
        up = pg.get_by_text("上传导出（批量）").first
        if up.count():
            up.click(); pg.wait_for_timeout(1200)
        demo = pg.get_by_role("button").filter(has_text="用示例导出").first
        if demo.count():
            demo.click(); pg.wait_for_timeout(2500); wait_idle(pg)
        tall(2600)
        kpi = pg.locator(".xz-kpi").filter(visible=True).first
        table = pg.locator('[data-testid="stDataFrame"]').filter(visible=True).first
        if kpi.count() and table.count():
            shot("S17_jira_batch", kpi, table, max_h=1700)
            bb = table.bounding_box()
            pg.mouse.click(bb["x"] + 16, bb["y"] + 38 + 3 * 35 + 17)   # 第 4 行 = XZ-1040
            pg.wait_for_timeout(2200); wait_idle(pg)
            det = bordered(pg, "XZ-1040")
            if det is not None:
                shot("S18_jira_detail", det, max_h=1700)

        # ---- S19 隐盾（样例③）----
        tall(VH); to_top()
        pg.get_by_text("零散需求").first.click(); pg.wait_for_timeout(1500); wait_idle(pg)
        if choose("03_"):
            start_and_wait("<手机_1>")
            tall(2600)
            pg.get_by_role("tab", name="隐盾").click(); pg.wait_for_timeout(1400)
            red = pg.get_by_text(re.compile(r"进模型前脱敏 \d+ 处")).filter(visible=True).first   # 页头技能条里也有「进模型前脱敏」，按「N 处」认正文
            sb = pg.locator('[data-testid="stDataFrame"]').filter(visible=True).last
            shot("S19_shield", red, sb, max_h=1800)
        b.close()


if __name__ == "__main__":
    main()
