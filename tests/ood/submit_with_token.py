import _pwenv as E, os, re, time
from playwright.sync_api import sync_playwright
TOKEN = os.environ["ABA_TEST_TOKEN"]
with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH)
    pg = ctx.new_page()
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sys/aba/session_contexts/new", wait_until="networkidle", timeout=60000)
    pg.fill("#batch_connect_session_context_aba_token", TOKEN)
    pg.click("input[type=submit][value=Launch]")
    pg.wait_for_load_state("networkidle", timeout=60000)
    href = None; t0 = time.time()
    while time.time() - t0 < 180:
        pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sessions", wait_until="networkidle", timeout=60000)
        link = pg.get_by_role("link", name=re.compile("Connect to ABA", re.I))
        if link.count(): href = link.first.get_attribute("href"); break
        time.sleep(4)
    print("HREF", href)
    b.close()
