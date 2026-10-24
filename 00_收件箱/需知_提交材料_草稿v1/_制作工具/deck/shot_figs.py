import sys
from playwright.sync_api import sync_playwright
base, out = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=2)
    for name, cid in (("F01_architecture", "arch"), ("F02_six_steps", "flow")):
        pg.goto(f"file://{base}/figs.html#{cid}"); pg.reload(); pg.wait_for_timeout(600)
        pg.locator(f"#{cid}").screenshot(path=f"{out}/{name}.png")
        print("ok", name)
    b.close()
