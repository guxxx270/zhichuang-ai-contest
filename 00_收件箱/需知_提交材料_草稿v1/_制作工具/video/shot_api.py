import sys
from playwright.sync_api import sync_playwright
url, out = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1366, "height": 1500}, device_scale_factor=2)
    pg.goto(url, wait_until="networkidle")
    pg.wait_for_selector(".opblock", timeout=30000)
    pg.wait_for_timeout(800)
    n = pg.locator(".opblock").count()
    last = pg.locator(".opblock").nth(n - 1).bounding_box()
    top = pg.locator(".information-container").bounding_box() or {"y": 0}
    h = min(last["y"] + last["height"] + 20 - top["y"], 1460)
    pg.screenshot(path=out, clip={"x": 0, "y": top["y"], "width": 1366, "height": h})
    print("ok S21_api_docs", n, "operations")
    b.close()
