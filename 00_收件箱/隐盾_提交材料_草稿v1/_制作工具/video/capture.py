"""隐盾截图 S01～S12（PPT 与 Word 共用）。先起一个隐盾，再跑：

    python capture.py http://127.0.0.1:8520/ ../shots

视口 1600×1000、2 倍像素（出图 3200×2000），暗色主题；演示数据全部虚构（yindun/samples.py）。
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL, OUT = sys.argv[1], Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

SUSPECT_TEXT = "客户王建国先生说他另一个号 12345678 也被冻结了，资金账号：88123456，麻烦查下原因。"
UNKNOWN_ANSWER = ("建议回电〈客户_1〉先生（\\<手机\\_1\\>）：账户 <账号_1> 暂未发现异常。\n"
                  "另外 <客户_9> 的情况需要另查。")


def wait_idle(pg, timeout_s: float = 20.0) -> None:
    """等 Streamlit 这一轮脚本跑完：右上角运行状态标记连续 1 秒不出现。"""
    import time
    end, quiet = time.monotonic() + timeout_s, 0
    while time.monotonic() < end and quiet < 4:
        busy = pg.evaluate("() => !!document.querySelector('[data-testid=\"stStatusWidget\"]')")
        quiet = 0 if busy else quiet + 1
        pg.wait_for_timeout(250)


def click(pg, role: str, name: str) -> None:
    pg.get_by_role(role, name=name, exact=True).first.click()
    pg.wait_for_timeout(300)
    wait_idle(pg)


def press(pg, key: str) -> None:
    """按钮带图标时无障碍名里混着图标字，按 Streamlit 的 key（容器类名 st-key-…）点。"""
    pg.locator(f".st-key-{key} button").first.click()
    pg.wait_for_timeout(300)
    wait_idle(pg)


def fill(pg, key: str, text: str) -> None:
    ta = pg.locator(f".st-key-{key} textarea").first
    ta.click()
    ta.fill(text)
    ta.press("Tab")
    pg.wait_for_timeout(300)
    wait_idle(pg)


def shot(pg, name: str, locator=None) -> None:
    pg.wait_for_timeout(500)
    path = OUT / f"{name}.png"
    if locator is None:
        pg.screenshot(path=str(path))
    else:
        locator.screenshot(path=str(path))
    print("shot", path.name, flush=True)


def scroll_main(pg, y: int) -> None:
    pg.evaluate("(y) => { const m = document.querySelector('[data-testid=\"stMain\"]') || document.scrollingElement; m.scrollTo(0, y); }", y)
    pg.wait_for_timeout(400)


def main() -> None:
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--hide-scrollbars"])
        ctx = b.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        pg = ctx.new_page()
        pg.goto(URL, wait_until="networkidle")
        pg.wait_for_selector("text=隐盾", timeout=60000)
        wait_idle(pg)
        shot(pg, "S01_home")

        press(pg, "ex_0")
        shot(pg, "S02_redact")
        click(pg, "radio", "原文对照")
        shot(pg, "S03_compare")
        click(pg, "radio", "本次映射")
        shot(pg, "S04_mapping")
        click(pg, "radio", "脱敏结果")
        scroll_main(pg, 520)
        shot(pg, "S05_copy")
        scroll_main(pg, 0)

        click(pg, "tab", "② 还原 · 贴大模型的回答")
        press(pg, "btn_fill")
        press(pg, "btn_restore")
        shot(pg, "S06_restore")

        click(pg, "tab", "① 脱敏 · 贴原文")
        press(pg, "ex_2")
        shot(pg, "S07_round2")

        click(pg, "tab", "② 还原 · 贴大模型的回答")
        fill(pg, "ans", UNKNOWN_ANSWER)
        press(pg, "btn_restore")
        shot(pg, "S08_restore_warn")

        press(pg, "btn_new")
        click(pg, "tab", "① 脱敏 · 贴原文")
        press(pg, "ex_1")
        shot(pg, "S09_ops")

        press(pg, "btn_new")
        fill(pg, "src", SUSPECT_TEXT)
        press(pg, "btn_redact")
        shot(pg, "S10_suspect")

        fill(pg, "names", "陈晓东\n欧阳明")
        fill(pg, "keep", "95521")
        pg.mouse.move(1500, 960)   # 鼠标移开，免得帮助提示框盖住侧边栏
        shot(pg, "S11_sidebar", pg.locator('[data-testid="stSidebar"]'))

        click(pg, "tab", "规则与边界")
        shot(pg, "S12_rules")
        b.close()


if __name__ == "__main__":
    main()
