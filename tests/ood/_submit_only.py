import _pwenv as E, sys
from playwright.sync_api import sync_playwright
APP = sys.argv[1] if len(sys.argv) > 1 else "aba"
with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH)
    pg = ctx.new_page()
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sys/{APP}/session_contexts/new",
            wait_until="networkidle", timeout=60000)
    pg.click("input[type=submit][value=Launch]")
    pg.wait_for_load_state("networkidle", timeout=60000)
    print("submitted", APP, "-> leaving session in place")
    b.close()
