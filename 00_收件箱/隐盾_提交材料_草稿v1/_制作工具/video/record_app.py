"""录 Demo 主体（s02～s09）：按旁白时间轴逐句驱动隐盾网页。

用法：python record_app.py <app_url>      （先跑 tts.py；隐盾要是刚启动的新会话）
视口 1600×900、1.2 倍像素 → 帧 1920×1080，界面字号比 1080p 原生大两成，成片里看得清。
输出：clips/<sid>.mp4
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
CLIPS = HERE / "clips"
CLIPS.mkdir(exist_ok=True)
SUSPECT_TEXT = "客户王建国先生说他另一个号 12345678 也被冻结了，资金账号：88123456，麻烦查下原因。"

UNION_JS = """([sel, label]) => {
  const els = [...document.querySelectorAll(sel)].filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; });
  if (!els.length) return false;
  const rs = els.map(e => e.getBoundingClientRect());
  const x = Math.min(...rs.map(r => r.left)), y = Math.min(...rs.map(r => r.top));
  const w = Math.max(...rs.map(r => r.right)) - x, h = Math.max(...rs.map(r => r.bottom)) - y;
  window.__xzrec && window.__xzrec.hl({x, y, width: w, height: h}, label);
  return true;
}"""


def wait_idle(pg, timeout_s: float = 15.0) -> None:
    """等 Streamlit 这一轮脚本跑完：运行状态标记连续 1 秒不出现。"""
    end, quiet = time.monotonic() + timeout_s, 0
    while time.monotonic() < end and quiet < 2:
        busy = pg.evaluate("() => !!document.querySelector('[data-testid=\"stStatusWidget\"]')")
        quiet = 0 if busy else quiet + 1
        pg.wait_for_timeout(250)


class Scene:
    def __init__(self, rec: Recorder, sid: str):
        self.rec, self.sid = rec, sid
        self.lines = TL[sid]["lines"]
        self.duration = TL[sid]["duration"]
        self.t0 = rec.now()
        print(f"[scene] {sid} @ {self.t0:.1f}s", flush=True)

    def cue(self, i: int, frac: float = 0.0) -> None:
        """等到第 i 句旁白的 frac 处（0 = 句首）。"""
        ln = self.lines[i]
        self.rec.wait_until(self.t0 + ln["start"] + (ln["end"] - ln["start"]) * frac)

    def end(self, extra: float = 0.3) -> tuple[float, float]:
        self.rec.wait_until(self.t0 + self.duration + extra)
        self.rec.snap()
        return self.t0, self.rec.now()


def main() -> None:
    marks: dict[str, tuple[float, float]] = {}
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
        ctx = b.new_context(viewport={"width": 1600, "height": 900}, device_scale_factor=1.2)
        pg = ctx.new_page()
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_selector("text=隐盾", timeout=60000)
        wait_idle(pg)
        pg.wait_for_timeout(800)
        (HERE / "frames").mkdir(exist_ok=True)
        rec = Recorder(pg, width=1920, height=1080, spool=HERE / "frames")   # 帧写盘，不占内存
        rec.cursor = (1180.0, 640.0)
        rec.start()

        def key(k: str):
            return pg.locator(f".st-key-{k} button").first

        def press(k: str, ms: int = 700) -> None:
            rec.click(key(k), ms=ms)
            wait_idle(pg)

        def hl_union(sel: str, label: str, seconds: float, clear: bool = True) -> None:
            pg.evaluate(UNION_JS, [sel, label])
            rec.sleep(seconds)
            if clear:
                rec.clear_highlights()
                rec.sleep(0.35)

        def scroll_top() -> None:
            pg.evaluate("() => (document.querySelector('[data-testid=\"stMain\"]') || document.scrollingElement).scrollTo({top: 0, behavior: 'smooth'})")
            rec.sleep(0.6)

        def reinstall() -> None:
            """Streamlit 重画页面后，假光标和高亮层可能被冲掉：补装一次。"""
            if not pg.evaluate("() => !!document.getElementById('xzrec-cursor')"):
                pg.evaluate("() => { window.__xzrec = undefined; }")
                rec.install_overlay()

        # ---------- s02 打开 ----------
        sc = Scene(rec, "s02_open")
        sc.cue(0, 0.15)
        rec.highlight(pg.locator(".yd-chips").first, "本机运行 · 不联网", 3.2)
        sc.cue(1, 0.3)
        rec.move_to(*_center(key("ex_0")), ms=900)
        sc.cue(1, 0.62)
        press("ex_0")
        marks["s02_open"] = sc.end()

        # ---------- s03 脱敏结果 ----------
        sc = Scene(rec, "s03_redact")
        reinstall()
        sc.cue(0, 0.0)
        hl_union('[data-testid="stMain"] [data-testid="stMetric"]', "9 处 · 7 类 · 不到 1 毫秒", 3.0)
        sc.cue(0, 0.35)
        rec.highlight(pg.locator(".yd-box").first, "带类型的语义标签", 6.0)
        sc.cue(1, 0.05)
        rec.move_to(*_center(pg.locator(".yd-tag").first), ms=900)
        rec.sleep(1.2)
        rec.move_to(*_center(pg.locator(".yd-tag").nth(1)), ms=700)
        marks["s03_redact"] = sc.end()

        # ---------- s04 原文对照 + 复制 ----------
        sc = Scene(rec, "s04_compare")
        sc.cue(0, 0.05)
        rec.click(pg.get_by_role("radio", name="原文对照"), ms=800)
        wait_idle(pg)
        reinstall()
        rec.highlight(pg.locator(".yd-box").first, "标红 = 发出去之前被换掉的内容", 3.6)
        sc.cue(1, 0.0)
        code = pg.locator('[data-testid="stMain"] [data-testid="stCode"]').first
        code.scroll_into_view_if_needed()
        rec.sleep(0.4)
        rec.highlight(code, "复制去问大模型（开头自带「保留标签」提示）", 2.6, clear=False)
        box = code.bounding_box()
        rec.move_to(box["x"] + box["width"] - 26, box["y"] + 22, ms=700)
        rec.sleep(0.6)
        rec.clear_highlights()
        marks["s04_compare"] = sc.end()

        # ---------- s05 还原 ----------
        sc = Scene(rec, "s05_restore")
        scroll_top()
        sc.cue(0, 0.0)
        rec.click(pg.get_by_role("tab", name="② 还原 · 贴大模型的回答"), ms=800)
        wait_idle(pg)
        reinstall()
        sc.cue(0, 0.2)
        press("btn_fill")
        reinstall()
        rec.highlight(pg.locator(".st-key-ans textarea").first, "模拟回答：〈〉【】、Markdown 转义、少了尖括号", 5.0)
        sc.cue(1, 0.0)
        press("btn_restore")
        reinstall()
        hl_union('[data-testid="stMain"] [data-testid="stMetric"]', "还原 9 处 · 纠正写法 4 处", 3.2)
        rec.highlight(pg.locator(".yd-box").first, "青色 = 换回的原文", 3.8)
        marks["s05_restore"] = sc.end()

        # ---------- s06 第二轮 ----------
        sc = Scene(rec, "s06_round2")
        sc.cue(0, 0.15)
        rec.click(pg.get_by_role("tab", name="① 脱敏 · 贴原文"), ms=800)
        wait_idle(pg)
        reinstall()
        sc.cue(0, 0.5)
        press("ex_2")
        reinstall()
        sc.cue(1, 0.0)
        rec.highlight(pg.locator(".yd-box").first, "客户_1、手机_1 沿用；新账户编成账号_2", 5.5)
        sc.cue(2, 0.0)
        hl_union('[data-testid="stMain"] [data-testid="stMetric"]', "会话映射 9 → 10 条", 2.6)
        rec.highlight(pg.locator('[data-testid="stSidebar"] [data-testid="stMetric"]').first, "多轮共用一张映射表", 2.2)
        marks["s06_round2"] = sc.end()

        # ---------- s07 运维日志 ----------
        sc = Scene(rec, "s07_ops")
        sc.cue(0, 0.3)
        press("btn_new")
        reinstall()
        sc.cue(0, 0.62)
        press("ex_1")
        reinstall()
        sc.cue(1, 0.0)
        rec.highlight(pg.locator(".st-key-src textarea").first, "原文：连接串口令、api_key、GitHub 令牌", 3.4)
        rec.highlight(pg.locator(".yd-box").first, "键名留着，值换成 <密钥_n>", 4.2)
        marks["s07_ops"] = sc.end()

        # ---------- s08 疑似遗漏 ----------
        sc = Scene(rec, "s08_suspect")
        press("btn_new", ms=500)
        reinstall()
        ta = pg.locator(".st-key-src textarea").first
        rec.click(ta, ms=500)
        ta.fill(SUSPECT_TEXT)
        ta.press("Tab")
        wait_idle(pg)
        press("btn_redact", ms=500)
        reinstall()
        rec.highlight(pg.locator('[data-testid="stMain"] [data-testid="stAlertContainer"]').first, "没有上下文的 8 位数字：只提示，不自作主张", 3.4)
        sc.cue(1, 0.66)
        press("btn_sus")
        reinstall()
        rec.highlight(pg.locator(".yd-box").first, "确认后一起脱敏", 2.4)
        marks["s08_suspect"] = sc.end()

        # ---------- s09 侧边栏 ----------
        sc = Scene(rec, "s09_sidebar")
        sc.cue(0, 0.0)
        names = pg.locator(".st-key-names textarea").first
        rec.click(names, ms=700)
        names.press_sequentially("陈晓东", delay=90)
        rec.highlight(pg.locator(".st-key-names").first, "自定义名单：裸姓名也能换", 1.8)
        pg.locator(".st-key-kinds").first.scroll_into_view_if_needed()
        rec.sleep(0.3)
        rec.highlight(pg.locator(".st-key-kinds").first, "按类型开关", 1.6)
        exp = pg.locator('[data-testid="stSidebar"] [data-testid="stDownloadButton"]').first
        exp.scroll_into_view_if_needed()
        rec.highlight(exp, "映射表导出，隔天还原", 1.8)
        marks["s09_sidebar"] = sc.end()

        rec.stop()
        for sid, (a, z) in marks.items():
            d = rec.encode(str(CLIPS / f"{sid}.mp4"), t_from=a, t_to=z)
            print(sid, f"{d:.1f}s", flush=True)
        (HERE / "marks.json").write_text(json.dumps(marks, indent=1), encoding="utf-8")
        b.close()


def _center(loc) -> tuple[float, float]:
    loc.scroll_into_view_if_needed()
    box = loc.bounding_box()
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


if __name__ == "__main__":
    main()
