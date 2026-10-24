"""录 Demo 主体（s02～s12）：按旁白时间轴逐句驱动页面操作；等模型时暂停录制（成片里剪掉等待）。

用法：python record_app.py <app_url> [--model]
  --model：页面已配置模型（开工后后台增强）；不带则纯规则引擎。
输出：clips/<sid>.mp4，marks.json（各场景在录制时间轴上的起止）。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from recorder import Recorder

HERE = Path(__file__).resolve().parent
TL = json.loads((HERE / "tts/timeline.json").read_text(encoding="utf-8"))
URL = sys.argv[1]
MODEL = "--model" in sys.argv
CLIPS = HERE / "clips"
CLIPS.mkdir(exist_ok=True)

PII_TEXT = ("来源：邮件 · 营业部 客户经理 → 技术部\n主题：大户追保提醒\n\n"
            "客户张伟（手机 13812345678，资金账号 8800123456）这类大户，风险度超过 100% 时能不能自动提醒我？"
            "盘中每 30 分钟查一次，夜盘也要。数据可以从 10.20.30.40 的风控库取，企业微信提醒就行。")


BORDERED_JS = """(t) => { const els=[...document.querySelectorAll('[data-testid="stVerticalBlock"]')].filter(e => getComputedStyle(e).borderTopWidth !== '0px' && e.innerText.includes(t) && e.getBoundingClientRect().height > 0); if (!els.length) return null; els.sort((a,b)=>a.getBoundingClientRect().height-b.getBoundingClientRect().height); const k = 'cap' + Math.random().toString(36).slice(2,8); els[0].setAttribute('data-xzcap', k); return k; }"""


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


class Scene:
    def __init__(self, rec: Recorder, sid: str):
        self.rec, self.sid = rec, sid
        self.lines = TL[sid]["lines"]
        self.duration = TL[sid]["duration"]
        self.t0 = rec.now()
        print(f"[scene] {sid} @ {self.t0:.1f}s", flush=True)

    def cue(self, i: int, lead: float = 0.0) -> None:
        """等到第 i 句旁白开始（可提前 lead 秒）。"""
        if i < len(self.lines):
            self.rec.wait_until(self.t0 + max(0.0, self.lines[i]["start"] - lead))

    def end(self, extra: float = 0.3) -> tuple[float, float]:
        self.rec.wait_until(self.t0 + self.duration + extra)
        self.rec.snap()
        return self.t0, self.rec.now()


def main() -> None:
    marks = {}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
        ctx = b.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        pg = ctx.new_page()
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_selector("text=需知，开工", timeout=60000)
        pg.wait_for_timeout(1500)
        spool = HERE / "frames"
        spool.mkdir(exist_ok=True)
        for f in spool.glob("*.jpg"):
            f.unlink()
        rec = Recorder(pg, spool=spool)
        rec.start()

        def tab(name: str):
            return pg.get_by_role("tab", name=name)

        def wait_results(timeout=60000, expect: str | None = None):
            pg.wait_for_selector('[data-testid="stTabs"]', timeout=timeout)
            if expect:   # 再次开工时页签早就在，等到新结果里的字样出现
                pg.wait_for_function("(t) => document.body.textContent.includes(t)", arg=expect, timeout=timeout)
            rec.sleep(1.2)
            rec.install_overlay()

        def wait_model_done(timeout_s=420):
            """暂停录制，等后台模型完成（页面出现「模型分析已完成」或失败提示）。"""
            if not MODEL:
                return
            rec.pause()
            t_end = time.monotonic() + timeout_s
            while time.monotonic() < t_end:
                txt = pg.locator("body").inner_text()
                if "模型分析已完成" in txt or "模型分析未完全成功" in txt:
                    break
                pg.wait_for_timeout(1000)
            pg.wait_for_timeout(1500)
            rec.install_overlay()
            rec.resume()

        def choose_sample(prefix: str) -> bool:
            rec.click(pg.locator('[data-testid="stSelectbox"]').first)
            rec.sleep(0.5)
            opt = pg.locator('[role="option"]').filter(has_text=prefix).first
            if not opt.count():
                pg.keyboard.press("Escape")
                rec.sleep(0.3)
                return False
            rec.click(opt)
            pg.wait_for_function("() => { const t = document.querySelector('textarea'); return t && t.value.length > 20; }", timeout=30000)
            rec.sleep(0.6)
            return True

        def scroll_top():
            rec.scroll(-20000, 900)

        # ---------- s02 开工 ----------
        sc = Scene(rec, "s02_start")
        rec.hold(0.6)
        choose_sample("01_")
        rec.highlight(pg.locator("textarea").first, "微信里的三句话", 1.6)
        sc.cue(1, lead=0.6)
        rec.click(pg.get_by_role("button", name="需知，开工"))
        wait_results()
        rec.hold(1.0)
        if MODEL:
            box = pg.locator('[data-testid="stExpander"]').filter(has_text="模型正在分析").first
            if box.count():
                rec.highlight(box, "后台模型增强中", 1.8)
        marks["s02_start"] = sc.end()
        wait_model_done()

        # ---------- s03 问清 ----------
        sc = Scene(rec, "s03_clarify")
        rec.click(tab("问清"))
        rec.sleep(0.8)
        rec.scroll_to('[data-testid="stTabs"]', offset=10, ms=700)
        elems = pg.get_by_text("抽到的要素").filter(visible=True).first
        if elems.count():
            rec.highlight(elems.locator("xpath=ancestor::div[contains(@class,'stMarkdown')][1]"), "抽出的需求要素", 2.2)
        sc.cue(1, lead=0.3)
        rec.scroll_to(".xz-q", offset=160, ms=900)
        qs = pg.locator(".xz-q")
        rec.highlight(qs.nth(0), "期货口径 · 影响高", 2.4, clear=False)
        rec.highlight(qs.nth(1), "", 2.0)
        sc.cue(2, lead=0.2)
        rec.scroll_to('[data-testid="stTabs"]', offset=10, ms=800)
        msg = pg.locator('[data-testid="stCode"]').first
        if msg.count():
            rec.highlight(msg, "一键复制给业务", 2.6)
        marks["s03_clarify"] = sc.end()

        # ---------- s04 答复重算 ----------
        sc = Scene(rec, "s04_answer")
        rec.scroll_to(".xz-q", offset=160, ms=700)
        boxes = pg.get_by_placeholder("业务答复（留空 = 按默认假设）")
        for i, ans in enumerate(("结算后为准，夜盘算下一交易日", "上一交易日结算价，不复权", "客户版去掉集中度")):
            rec.type_text(boxes.nth(i), ans, delay_ms=38)   # 前三题：结算时点、比较基准、客户版脱敏
            pg.keyboard.press("Tab")
            rec.sleep(0.5)
        rec.sleep(0.4)
        sc.cue(1, lead=0.5)
        btn = pg.get_by_role("button", name="按业务答复重算").first
        rec.click(btn)
        wait_results()
        rec.scroll_to('[data-testid="stTabs"]', offset=260, ms=700)
        change = bordered(pg, "本次变更")
        if change is not None:
            change.scroll_into_view_if_needed()
            rec.sleep(0.3)
            rec.highlight(change, "这一版改了什么 · 需重新确认", 2.6)
        marks["s04_answer"] = sc.end()
        wait_model_done()

        # ---------- s05 写单 ----------
        sc = Scene(rec, "s05_spec")
        rec.click(tab("写单"))
        rec.sleep(1.0)
        rec.scroll(260, 900)
        sc.cue(1, lead=0.3)
        rec.click(pg.get_by_text("技术版（用户故事 / 验收 / 字段 / 接口）").first)
        rec.sleep(1.0)
        rec.scroll(420, 1400)
        rec.hold(1.2)
        rec.scroll(380, 1400)
        marks["s05_spec"] = sc.end()

        # ---------- s06 估量 ----------
        sc = Scene(rec, "s06_estimate")
        rec.scroll_to('[data-testid="stTabs"]', offset=10, ms=700)
        rec.click(tab("估量"))
        rec.sleep(1.2)
        cards = bordered(pg, "独立模型估时")
        sc.cue(1, lead=0.2)
        if cards is not None:
            rec.highlight(cards, "独立模型估时", 2.0)
        tasks_h = pg.get_by_text("工作项与估算依据").filter(visible=True).first
        if tasks_h.count():
            rec.scroll_to("h4", offset=120, ms=900, nth=0)
            rec.scroll(380, 1200)
        sc.cue(2, lead=0.2)
        rule = pg.get_by_text("规则模板参考 · 六维复杂度、公式与任务").filter(visible=True).first
        if rule.count():
            rule.scroll_into_view_if_needed()
            rec.sleep(0.4)
            rec.highlight(rule, "规则模板：单列、不混算", 1.8)
        rec.scroll(520, 1300)
        marks["s06_estimate"] = sc.end()

        # ---------- s07 定架 ----------
        sc = Scene(rec, "s07_arch")
        rec.scroll_to('[data-testid="stTabs"]', offset=10, ms=700)
        rec.click(tab("定架"))
        rec.sleep(1.2)
        dm = pg.locator('[data-testid="stDataFrame"]').filter(visible=True).first
        if dm.count():
            rec.highlight(dm, "数据地图", 2.4)
        sc.cue(2, lead=0.3)
        dec = pg.get_by_text("需要 IT 拍板的决策").filter(visible=True).first
        if dec.count():
            dec.scroll_into_view_if_needed()
            rec.sleep(0.3)
            rec.scroll(-120, 400)
            rec.highlight(pg.locator('[data-testid="stExpander"]').filter(has_text="建议：").first, "选项 · 建议 · 影响", 2.0)
        adr = pg.get_by_role("button", name="下载 ADR（架构决策记录）.md")
        if adr.count():
            adr.scroll_into_view_if_needed()
            rec.highlight(adr, "一键生成 ADR", 1.4)
        marks["s07_arch"] = sc.end()

        # ---------- s08 出样 ----------
        sc = Scene(rec, "s08_proto")
        rec.scroll_to('[data-testid="stTabs"]', offset=10, ms=700)
        rec.click(tab("出样"))
        rec.sleep(2.0)
        sc.cue(1, lead=0.3)
        rec.click(pg.get_by_text("客户版", exact=True).first)
        rec.sleep(2.2)
        rec.click(pg.get_by_text("手机版", exact=True).first)
        rec.sleep(1.5)
        marks["s08_proto"] = sc.end()

        # ---------- s09 一本账 ----------
        sc = Scene(rec, "s09_ledger")
        rec.click(pg.get_by_text("手机版", exact=True).first)   # 复原
        rec.click(tab("一本账"))
        rec.sleep(1.5)
        verx = pg.locator('[data-testid="stExpander"]').filter(has_text="版本记录").filter(visible=True).first
        if verx.count():
            verx.scroll_into_view_if_needed()
            rec.sleep(0.3)
            rec.click(verx.locator("summary").first)
            rec.sleep(1.4)
            rec.highlight(verx, "任选两版对比", 2.0)
        sc.cue(1, lead=0.2)
        board = pg.get_by_text("需求经营看板").filter(visible=True).first
        if board.count():
            board.scroll_into_view_if_needed()
            rec.sleep(0.3)
            rec.scroll(-80, 500)
            rec.scroll(420, 1600)
        marks["s09_ledger"] = sc.end()

        if MODEL:   # 后面的场景只用规则引擎：暂停录制，把模型来源切到没有 Key 的预设
            rec.pause()
            scroll_top()
            disable_model(pg)
            rec.install_overlay()
            rec.resume()

        # ---------- s10 追问记忆 ----------
        sc = Scene(rec, "s10_memory")
        scroll_top()
        choose_sample("05_")
        rec.click(pg.get_by_role("button", name="需知，开工"))
        wait_results(expect="沿用上次口径")
        rec.click(tab("问清"))
        sc.cue(1, lead=0.3)
        mem = pg.locator(".xz-chip.mem").filter(visible=True).first
        if mem.count():
            mem.scroll_into_view_if_needed()
            rec.sleep(0.3)
            rec.highlight(mem.locator("xpath=ancestor::div[contains(@class,'xz-q')][1]"), "🧠 沿用上次口径", 2.2)
        marks["s10_memory"] = sc.end()
        # ---------- s10b 纪要直通车 ----------
        if "s10b_minutes" in TL:
            sc = Scene(rec, "s10b_minutes")
            scroll_top()
            if not choose_sample("06_"):
                print("skip s10b: no minutes sample")
            else:
                rec.click(pg.get_by_role("button", name="需知，开工"))
                wait_results(expect="纪要直通车")
                banner = pg.get_by_text("纪要直通车").filter(visible=True).first
                if banner.count():
                    banner.scroll_into_view_if_needed()
                    rec.sleep(0.3)
                    rec.highlight(banner.locator("xpath=ancestor::div[@data-testid='stVerticalBlock'][1]"), "识别到多条需求", 2.0)
                sc.cue(1, lead=0.2)
                split = pg.get_by_role("button").filter(has_text="分别受理").first
                if split.count():
                    rec.click(split)
                    rec.sleep(2.0)
                    rec.install_overlay()
                    res = pg.get_by_text("纪要直通车").first
                    if res.count():
                        res.scroll_into_view_if_needed()
                    rec.scroll(200, 900)
                marks["s10b_minutes"] = sc.end()

        # ---------- s11 Jira ----------
        sc = Scene(rec, "s11_jira")
        scroll_top()
        rec.click(pg.get_by_text("Jira 需求评估").first)
        rec.sleep(1.5)
        rec.install_overlay()
        up = pg.get_by_text("上传导出（批量）").first
        if up.count():
            rec.click(up)
            rec.sleep(1.2)
        demo = pg.get_by_role("button").filter(has_text="用示例导出").first
        if demo.count():
            rec.click(demo)
            rec.sleep(2.0)
            rec.install_overlay()
        rec.scroll(380, 1200)
        sc.cue(1, lead=0.2)
        rec.scroll(420, 1400)
        sc.cue(2, lead=0.6)
        table = pg.locator('[data-testid="stDataFrame"]').filter(visible=True).first
        for row, key, label in ((3, "XZ-1040", "与原估差得多"), (2, "XZ-1036", "交易链路风险")):
            if not table.count():
                break
            table.scroll_into_view_if_needed()
            rec.sleep(0.4)
            bb = table.bounding_box()
            rec.click_at(bb["x"] + 16, bb["y"] + 38 + row * 35 + 17)   # 行首勾选框；待评估的排在前面
            pg.wait_for_function("(t) => document.body.textContent.includes(t)", arg=key, timeout=20000)
            rec.sleep(1.2)
            rec.install_overlay()
            det = bordered(pg, key)
            if det is not None:
                det.scroll_into_view_if_needed()
                rec.sleep(0.3)
                rec.highlight(det, f"{key} · {label}", 2.2)
        marks["s11_jira"] = sc.end()

        # ---------- s12 隐盾 ----------
        sc = Scene(rec, "s12_shield")
        scroll_top()
        rec.click(pg.get_by_text("零散需求").first)
        rec.sleep(1.5)
        rec.install_overlay()
        choose_sample("03_")
        rec.click(pg.get_by_role("button", name="需知，开工"))
        wait_results(expect="<手机_1>")
        rec.scroll_to('[data-testid="stTabs"]', offset=10, ms=700)
        rec.click(tab("隐盾"))
        rec.sleep(1.2)
        red = pg.get_by_text("进模型前脱敏").filter(visible=True).first
        if red.count():
            rec.highlight(red.locator("xpath=ancestor::div[@data-testid='stVerticalBlock'][1]"), "客户姓名 · 资金账号 · 手机号 → 标签", 2.6)
        sc.cue(1, lead=0.3)
        pol = pg.get_by_text("沙箱策略").filter(visible=True).first
        if pol.count():
            pol.scroll_into_view_if_needed()
            rec.sleep(0.3)
            rec.scroll(-60, 400)
            rec.scroll(320, 1400)
        marks["s12_shield"] = sc.end()

        rec.stop()
        (HERE / "marks.json").write_text(json.dumps(marks, ensure_ascii=False, indent=1), encoding="utf-8")
        for sid, (a, z) in marks.items():
            d = rec.encode(str(CLIPS / f"{sid}.mp4"), t_from=a, t_to=z)
            print(f"{sid}: {d:.1f}s")
        b.close()


if __name__ == "__main__":
    main()
