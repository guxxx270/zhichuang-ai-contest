"""录动画卡片：片头、痛点、三种用法、评测、片尾（时长按旁白 timeline，元素按句出现）。

用法：python record_cards.py   （先跑 tts.py 生成 tts/timeline.json）
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from recorder import Recorder

here = Path(__file__).resolve().parent
tl = json.loads((here / "tts/timeline.json").read_text(encoding="utf-8"))
clips = here / "clips"
clips.mkdir(exist_ok=True)
CARDS = {"s00_title": "title", "s01_pain": "pain", "s10_ways": "ways", "s11_eval": "eval", "s12_close": "close"}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    for sid, cid in CARDS.items():
        if sid not in tl:
            continue
        pg.goto("about:blank")
        r = Recorder(pg)
        pg.goto(f"file://{here}/cards.html#{cid}")
        pg.wait_for_load_state("load")
        r.start()
        pg.evaluate("(lines) => window.__cue(lines)", tl[sid]["lines"])
        pg.evaluate("document.getElementById('xzrec-cursor') && (document.getElementById('xzrec-cursor').style.display='none')")
        r.hold(tl[sid]["duration"] + 0.4)
        r.stop()
        d = r.encode(str(clips / f"{sid}.mp4"))
        print(sid, round(d, 1), "s")
    b.close()
