"""PPT / 文档用的高清截图（2× 像素密度，按区域裁切）：S01～S20。

用法：python capture.py <app_url> <out_dir> [--model]
步骤与 Demo 录制一致：样例① → 问清 → 答复重算 → 写单 → 估量 → 定架 → 出样 → 一本账 → 样例⑤ 追问记忆
→ 样例⑥ 纪要直通车 → Jira 批量与单条 → 含敏感信息的邮件看隐盾。
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

URL, OUT = sys.argv[1], Path(sys.argv[2])
MODEL = "--model" in sys.argv
OUT.mkdir(parents=True, exist_ok=True)
VW, VH = 1366, 900
PII_TEXT = ("来源：邮件 · 营业部 客户经理 → 技术部\n主题：大户追保提醒\n\n"
            "客户张伟（手机 13812345678，资金账号 8800123456）这类大户，风险度超过 100% 时能不能自动提醒我？"
            "盘中每 30 分钟查一次，夜盘也要。数据可以从 10.20.30.40 的风控库取，企业微信提醒就行。")


BORDERED_JS = """(t) => { const els=[...document.querySelectorAll('[data-testid="stVerticalBlock"]')].filter(e => getComputedStyle(e).borderTopWidth !== '0px' && e.innerText.includes(t) && e.getBoundingClientRect().height > 0); if (!els.length) return null; els.sort((a,b)=>a.getBoundingClientRect().height-b.getBoundingClientRect().height); const k = 'cap' + Math.random().toString(36).slice(2,8); els[0].setAttribute('data-xzcap', k); return k; }"""


# CAPTURE_ONLY="S10_proto,S11_proto_client"：只重拍这几张（流程照走，其余不落盘）
ONLY = {n for n in os.environ.get("CAPTURE_ONLY", "").split(",") if n}


def bordered(pg, text):
    """Streamlit 1.63 的带边框容器没有专门的 testid：按文字找最内层带边框的 stVerticalBlock。"""
    key = pg.evaluate(BORDERED_JS, text)
    return pg.locator(f'[data-xzcap="{key}"]') if key else None


def wait_idle(pg, timeout_s: float = 25.0) -> None:
    """等 Streamlit 这一轮脚本跑完：右上角运行状态标记连续 1 秒不出现。"""
    end, quiet = __import__("time").monotonic() + timeout_s, 0
    while __import__("time").monotonic() < end and quiet < 4:
        busy = pg.evaluate("() => !!document.querySelector('[data-testid=\"stStatusWidget\"]')")
        quiet = 0 if busy else quiet + 1
        pg.wait_for_timeout(250)


def disable_model(pg) -> None:
    """主流程录完后关掉模型：把「模型来源」切到没有 Key 的预设（.env 的 Key 只认它自己的网关），后面只跑规则。"""
    exp = pg.locator('[data-testid="stExpander"] summary').filter(has_text="模型与参考材料").first
    exp.scroll_into_view_if_needed()
    exp.click()
    pg.wait_for_timeout(900)
    sel = pg.locator('[data-testid="stSelectbox"]').filter(has_text="模型来源").first   # 选中的值在输入框里，按标签找
    if sel.count():
        pg.evaluate("(el) => el.scrollIntoView({block: 'center'})", sel.element_handle())
        pg.wait_for_timeout(500)
        for _ in range(3):   # 下拉框贴着视口底边时第一次点击可能落空：滚到中间、点输入框，没弹出就重试
            sel.locator("input").first.click()
            pg.wait_for_timeout(700)
            opt = pg.locator('[role="option"]').filter(has_text="OpenAI").first
            if opt.count():
                opt.click()
                break
            pg.keyboard.press("Escape")
        pg.wait_for_timeout(600)
        wait_idle(pg)
    exp.click()
    pg.wait_for_timeout(800)
    status = pg.locator(".xz-status").first.inner_text()
    print("model after disable:", status, flush=True)
    if "模型已配置" in status:
        raise RuntimeError("没能关掉模型：" + status)


def paste_custom(pg, text: str, before_fill=None) -> None:
    """选「（粘贴自己的）」后原话框会换一个新组件：等这一轮跑完再填，失焦提交后确认内容还在，没留住就重填。"""
    pg.wait_for_timeout(400)
    wait_idle(pg)
    for _ in range(3):
        ta = pg.locator("textarea").first
        if before_fill:
            before_fill(ta)
        ta.click()
        ta.fill(text)
        ta.press("Tab")
        pg.wait_for_timeout(400)
        wait_idle(pg)
        if pg.evaluate("() => (document.querySelector('textarea') || {value: ''}).value.length") > 50:
            return
    raise RuntimeError("原话没有填进去")


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

        def shot_region(name: str, top_loc, bottom_loc=None, *, pad_top=12, pad_bottom=16, max_h=2400, min_h=300, x0=40, x1=None):
            """从 top_loc 顶部截到 bottom_loc 底部（同一屏内；视口已调高）。"""
            if ONLY and name not in ONLY:   # 只补拍几张：流程照走，其余不落盘
                return
            top_loc.scroll_into_view_if_needed()
            pg.wait_for_timeout(500)
            tb = top_loc.bounding_box()
            bb = (bottom_loc or top_loc).bounding_box()
            if not tb or not bb:
                print("miss", name)
                return
            y = max(0, tb["y"] - pad_top)
            h = min(max_h, max(min_h, bb["y"] + bb["height"] - y + pad_bottom))
            vh = pg.viewport_size["height"]
            if y + h > vh:   # 先把区域顶端滚到视口顶部再截
                pg.evaluate("(dy) => { const el=[...document.querySelectorAll('[data-testid=\"stMain\"],[data-testid=\"stAppViewContainer\"]')].find(e=>e.scrollHeight>e.clientHeight+4); if(el) el.scrollTop += dy; }", y - 10)
                pg.wait_for_timeout(600)
                tb = top_loc.bounding_box(); bb = (bottom_loc or top_loc).bounding_box()
                y = max(0, tb["y"] - pad_top)
                h = min(max_h, max(min_h, bb["y"] + bb["height"] - y + pad_bottom), vh - y)
            pg.screenshot(path=str(OUT / f"{name}.png"), clip={"x": x0, "y": y, "width": (x1 or VW - 40) - x0, "height": h})
            print("ok", name, int(h))

        def wait_results():
            pg.wait_for_selector('[data-testid="stTabs"]', timeout=90000)
            pg.wait_for_timeout(1500)

        def wait_model(timeout_s=420):
            if not MODEL:
                return
            t_end = time.monotonic() + timeout_s
            while time.monotonic() < t_end:
                txt = pg.locator("body").inner_text()
                if "模型分析已完成" in txt or "模型分析未完全成功" in txt:
                    break
                pg.wait_for_timeout(1000)
            pg.wait_for_timeout(1200)

        def choose(prefix: str) -> bool:
            pg.locator('[data-testid="stSelectbox"]').first.click()
            pg.wait_for_timeout(500)
            opt = pg.locator('[role="option"]').filter(has_text=prefix).first
            if not opt.count():
                pg.keyboard.press("Escape")
                return False
            opt.click()
            pg.wait_for_function("() => { const t = document.querySelector('textarea'); return t && t.value.length > 20; }", timeout=30000)
            pg.wait_for_timeout(600)
            return True

        def tab(name):
            pg.get_by_role("tab", name=name).click()
            pg.wait_for_timeout(1400)

        def to_top():
            pg.evaluate("() => { document.querySelectorAll('[data-testid=\"stMain\"],[data-testid=\"stAppViewContainer\"]').forEach(e=>e.scrollTop=0); }")
            pg.wait_for_timeout(500)

        # S01 首页
        choose("01_")
        if not ONLY or "S01_home" in ONLY:
            pg.screenshot(path=str(OUT / "S01_home.png"))
            print("ok S01_home")
        pg.get_by_role("button", name="需知，开工").click()
        wait_results()
        wait_model()
        tall(2600)

        # S02 问清
        tab("问清")
        tabs = pg.locator('[data-testid="stTabs"]').first
        qs = pg.locator(".xz-q")
        shot_region("S02_clarify", tabs, qs.nth(min(4, max(0, qs.count() - 1))))

        # S03 答复重算
        boxes = pg.get_by_placeholder("业务答复（留空 = 按默认假设）")
        for i, ans in enumerate(("结算后为准，夜盘算下一交易日", "上一交易日结算价，不复权", "客户版去掉集中度")):
            boxes.nth(i).fill(ans)
            boxes.nth(i).press("Tab")
            pg.wait_for_timeout(500)
        wait_idle(pg)
        pg.get_by_role("button", name="按业务答复重算").first.click()
        wait_results()
        wait_model()
        det = pg.get_by_text("变更明细").filter(visible=True).first
        if det.count():
            det.click()
            pg.wait_for_timeout(1200)
        card = bordered(pg, "本次变更")
        if card is not None:
            shot_region("S03_recompute", card, pad_top=14, max_h=1500)
        else:
            shot_region("S03_recompute", pg.locator(".xz-q").first, pg.locator(".xz-q").nth(2))
        if det.count():   # 收起，后面的截图不受影响
            det.click()
            pg.wait_for_timeout(800)

        # S04 / S05 写单
        tab("写单")
        cont = bordered(pg, "提出方")
        shot_region("S04_spec_biz", pg.get_by_text("业务版（一页纸，业务签字）").first, cont, max_h=1500)
        pg.get_by_text("技术版（用户故事 / 验收 / 字段 / 接口）").first.click()
        pg.wait_for_timeout(1400)
        cont = bordered(pg, "用户故事") or bordered(pg, "提出方")
        shot_region("S05_spec_tech", pg.get_by_text("技术版（用户故事 / 验收 / 字段 / 接口）").first, cont, max_h=1500)

        # S06 / S07 估量
        tab("估量")
        top = pg.get_by_text("模型分析、规则模板、历史记录分开提供依据").first
        end = pg.get_by_text("工作项与估算依据").first
        tbl = end.locator("xpath=following::*[@data-testid='stDataFrame'][1]") if end.count() else None
        shot_region("S06_estimate_model", top, tbl if (tbl is not None and tbl.count()) else None, max_h=1700)
        rule = pg.get_by_text("规则模板参考 · 六维复杂度、公式与任务").first
        bars = pg.get_by_text("各维加进参考工时（小时）").first
        if rule.count():
            target_end = bars.locator("xpath=following::*[contains(@data-testid,'stVegaLiteChart') or contains(@data-testid,'stArrowVegaLiteChart')][1]") if bars.count() else None
            shot_region("S07_estimate_refs", rule, target_end if (target_end is not None and target_end.count()) else None, max_h=1800)

        # S08 / S09 定架
        tab("定架")
        dm = pg.locator('[data-testid="stDataFrame"]').filter(visible=True).first
        shot_region("S08_arch_datamap", pg.get_by_text("复用发现").filter(visible=True).first, dm, max_h=1400)
        dec = pg.get_by_text("需要 IT 拍板的决策").filter(visible=True).first
        exps = pg.locator('[data-testid="stExpander"]').filter(has_text="建议：").filter(visible=True)
        shot_region("S09_arch_decisions", dec, exps.nth(min(1, max(0, exps.count() - 1))), max_h=1600)

        # S10～S12 出样
        tab("出样")
        pg.wait_for_timeout(1500)
        frame_box = pg.locator('iframe[title="st.iframe"], iframe').filter(has=pg.locator("xpath=.")).last
        iframes = pg.locator("iframe")
        big = None
        for i in range(iframes.count()):
            bb = iframes.nth(i).bounding_box()
            if bb and bb["height"] > 400:
                big = iframes.nth(i)
        if big:
            shot_region("S10_proto", pg.get_by_text("手机版", exact=True).first, big, max_h=1100)
            pg.get_by_text("客户版", exact=True).first.click()
            pg.wait_for_timeout(2000)
            shot_region("S11_proto_client", pg.get_by_text("手机版", exact=True).first, big, max_h=1100)
            pg.get_by_text("手机版", exact=True).first.click()
            pg.wait_for_timeout(2000)
            for i in range(iframes.count()):
                bb = iframes.nth(i).bounding_box()
                if bb and bb["height"] > 400:
                    big = iframes.nth(i)
            shot_region("S12_proto_mobile", pg.get_by_text("手机版", exact=True).first, big, max_h=1300)
            pg.get_by_text("手机版", exact=True).first.click()
            pg.get_by_text("客户版", exact=True).first.click()
            pg.wait_for_timeout(1200)

        if ONLY and ONLY <= {"S10_proto", "S11_proto_client", "S12_proto_mobile"}:
            b.close()
            return

        # S13 / S14 一本账
        tab("一本账")
        board = pg.get_by_text("需求经营看板").filter(visible=True).first
        charts = pg.locator('[data-testid="stVegaLiteChart"], [data-testid="stArrowVegaLiteChart"]')
        if board.count():
            last_chart = charts.nth(min(3, max(0, charts.count() - 1))) if charts.count() else None
            shot_region("S13_ledger_dashboard", board, last_chart, max_h=1500)
        verx = pg.locator('[data-testid="stExpander"]').filter(has_text="版本记录").filter(visible=True).first   # 「版本记录」字样别处也有，按可见的折叠块找
        if verx.count():
            verx.locator("summary").first.click()
            pg.wait_for_timeout(1500)
            shot_region("S14_ledger_versions", verx, max_h=1500)
        audit_h = pg.locator("h4").filter(has_text="模型调用审计").filter(visible=True).first
        if audit_h.count():
            # 只截标题与五张指标卡：「出站明文敏感项 0 处」是要点；明细表里有网关域名，不进材料
            mets = audit_h.locator("xpath=following::*[@data-testid='stMetric']")
            if mets.count():
                shot_region("S20_audit", audit_h, mets.nth(min(4, mets.count() - 1)), max_h=700, min_h=60)   # min_h 放小：别把下面带网关域名的明细表撑进来
            else:
                print("miss S20_audit (no model calls)")
        if MODEL:   # 主流程截完：关掉模型，后面几张只跑规则，免得后台模型任务中途刷新页面
            tall(VH)
            to_top()
            disable_model(pg)

        # S15 追问记忆
        tall(VH)
        to_top()
        if choose("05_"):
            pg.get_by_role("button", name="需知，开工").click()
            wait_results()
            tall(2600)
            tab("问清")
            head = pg.get_by_text("待确认清单").first
            mem = pg.locator(".xz-q").filter(has=pg.locator(".xz-chip.mem"))
            shot_region("S15_memory", head, mem.nth(min(2, max(0, mem.count() - 1))) if mem.count() else pg.locator(".xz-q").nth(2), max_h=1300)

        # S16 纪要直通车
        tall(VH)
        to_top()
        if choose("06_"):
            pg.get_by_role("button", name="需知，开工").click()
            wait_results()
            tall(2600)
            banner = pg.get_by_text("纪要直通车").first
            if banner.count():
                split = pg.get_by_role("button").filter(has_text="分别受理").first
                if split.count():
                    split.click()
                    pg.wait_for_timeout(2500)
                banner = pg.get_by_text("纪要直通车").first
                blk = banner.locator("xpath=ancestor::div[@data-testid='stVerticalBlock'][1]")
                shot_region("S16_minutes_split", blk, max_h=1300)

        # S17 / S18 Jira
        tall(VH)
        to_top()
        pg.get_by_text("Jira 需求评估").first.click()
        pg.wait_for_timeout(1500)
        up = pg.get_by_text("上传导出（批量）").first
        if up.count():
            up.click()
            pg.wait_for_timeout(1200)
        demo = pg.get_by_role("button").filter(has_text="用示例导出").first
        if demo.count():
            demo.click()
            pg.wait_for_timeout(2500)
        tall(2600)
        kpi = pg.locator(".xz-kpi").filter(visible=True).first
        table = pg.locator('[data-testid="stDataFrame"]').filter(visible=True).first
        if kpi.count() and table.count():
            shot_region("S17_jira_batch", kpi, table, max_h=1700)
            bb = table.bounding_box()
            pg.mouse.click(bb["x"] + 16, bb["y"] + 38 + 3 * 35 + 17)   # 第 4 行 = XZ-1040
            pg.wait_for_timeout(2200)
            det = bordered(pg, "XZ-1040")
            if det is not None:
                shot_region("S18_jira_detail", det, max_h=1700)
            else:
                print("miss S18_jira_detail")
        else:
            print("miss S17_jira_batch")

        # S19 隐盾
        tall(VH)
        to_top()
        pg.get_by_text("零散需求").first.click()
        pg.wait_for_timeout(1500)
        choose("03_")
        pg.get_by_role("button", name="需知，开工").click()
        pg.wait_for_function("(t) => document.body.textContent.includes(t)", arg="<手机_1>", timeout=60000)
        pg.wait_for_timeout(1200)
        tall(2600)
        tab("隐盾")
        red = pg.get_by_text(re.compile(r"进模型前脱敏 \d+ 处")).filter(visible=True).first   # 页头技能条里也有「进模型前脱敏」，按「N 处」认正文
        sb = pg.locator('[data-testid="stDataFrame"]').last
        shot_region("S19_shield", red, sb, max_h=1800)
        b.close()


if __name__ == "__main__":
    main()
