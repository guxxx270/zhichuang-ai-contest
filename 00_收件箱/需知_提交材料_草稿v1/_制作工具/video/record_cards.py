"""录三张动画卡片：s00_title / s01_pain / s13_close（时长按旁白 timeline）。"""
import json, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright
from recorder import Recorder
here = Path(__file__).resolve().parent
tl = json.loads((here / "tts/timeline.json").read_text(encoding="utf-8"))
clips = here / "clips"; clips.mkdir(exist_ok=True)
CARDS = {"s00_title": "title", "s01_pain": "pain", "s13_close": "close"}
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1920, "height": 1080})
    for sid, cid in CARDS.items():
        if sid not in tl: continue
        pg.goto("about:blank")
        r = Recorder(pg)
        pg.goto(f"file://{here}/cards.html#{cid}")
        r.start()
        if cid == "pain" and len(tl[sid]["lines"]) > 1:   # 三张痛点卡跟着第二句旁白出现
            t2 = tl[sid]["lines"][1]["start"]
            pg.evaluate(f"""[...document.querySelectorAll('#pain .pain')].forEach((e,i)=>e.style.animationDelay=({t2:.2f}+i*1.4)+'s')""")
        pg.evaluate("document.getElementById('xzrec-cursor') && (document.getElementById('xzrec-cursor').style.display='none')")
        r.hold(tl[sid]["duration"] + 0.4)
        r.stop()
        d = r.encode(str(clips / f"{sid}.mp4"))
        print(sid, round(d, 1), "s")
    b.close()
