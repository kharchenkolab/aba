import _pwenv as E
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH)
    pg = ctx.new_page()
    pg.goto(E.BASE + "/pun/sys/dashboard", wait_until="networkidle", timeout=60000)
    links = pg.eval_on_selector_all(
        "a[href*='batch_connect']",
        "els => [...new Set(els.map(e => e.getAttribute('href')))]")
    print("=== batch_connect links ===")
    for l in links: print(" ", l)
    # also dump the Interactive Apps menu labels
    labels = pg.eval_on_selector_all(
        "a[href*='batch_connect']", "els => els.map(e => e.textContent.trim()).filter(Boolean)")
    print("=== labels ===", [l for l in dict.fromkeys(labels)][:20])
    pg.screenshot(path="/home/pkharchenko/aba/aba/tests/ood/_dashboard.png", full_page=True)
    print("screenshot -> tests/ood/_dashboard.png")
    b.close()
