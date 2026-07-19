"""T0/T1 round-trip: Launch an OOD app -> wait Running -> Connect -> verify -> Stop.

Usage:  python round_trip.py [app_url_name] [connect_text]
  e.g.  python round_trip.py aba "Connect to ABA" "ABA"

Drives https://localhost:33000 (basic auth ood/ood) headless. Proves the full
chain: control-panel submit -> Slurm job -> session Running -> reverse-proxy
Connect -> app page loads -> clean delete. Screenshots -> tests/ood/_shots/.
Exit 0 = PASS (connected app page verified), 2 = INCOMPLETE.
"""
import _pwenv as E
import re, sys, time, pathlib
from playwright.sync_api import sync_playwright

APP = sys.argv[1] if len(sys.argv) > 1 else "aba"
CONNECT = sys.argv[2] if len(sys.argv) > 2 else "Connect to ABA"
VERIFY = sys.argv[3] if len(sys.argv) > 3 else "ABA"
SHOTS = pathlib.Path(__file__).parent / "_shots"; SHOTS.mkdir(exist_ok=True)
DEADLINE = 240
STATUS_RE = re.compile(r"\b(Queued|Hold|Starting|Running|Completed|Failed|Undetermined|Suspended)\b")


def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH)
    pg = ctx.new_page()

    log("opening", APP, "control panel")
    _app_path = APP if "/" in APP else f"sys/{APP}"   # "dev/aba" targets a sandbox app
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/{_app_path}/session_contexts/new",
            wait_until="networkidle", timeout=60000)
    pg.click("input[type=submit][value=Launch]")
    pg.wait_for_load_state("networkidle", timeout=60000)
    log("submitted ->", pg.url)
    pg.screenshot(path=str(SHOTS / "01_after_launch.png"), full_page=True)

    # The SESSION card is the one in main content that has a Delete button
    # (the left sidebar is also a .card — that was the earlier mis-select).
    def session_card():
        c = pg.locator("div.card").filter(
            has=pg.get_by_role("link", name=re.compile("Delete", re.I)))
        return c.first if c.count() else None

    connect = None
    t0 = time.time(); last = ""
    while time.time() - t0 < DEADLINE:
        pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sessions",
                wait_until="networkidle", timeout=60000)
        link = pg.get_by_role("link", name=re.compile(re.escape(CONNECT), re.I))
        card = session_card()
        txt = card.inner_text() if card else pg.inner_text("body")
        m = STATUS_RE.search(txt)
        status = m.group(1) if m else "?"
        if status != last:
            log("session status:", status); last = status
        if link.count() > 0:
            connect = link.first; log("Connect link present"); break
        if status in ("Failed", "Completed"):
            log("session reached terminal state before connect:", status); break
        time.sleep(5)

    pg.screenshot(path=str(SHOTS / "02_sessions.png"), full_page=True)

    ok = False
    if connect:
        href = connect.get_attribute("href")
        log("connecting ->", href)
        try:
            with ctx.expect_page(timeout=15000) as pi:
                connect.click()
            app_pg = pi.value
        except Exception:
            connect.click(); app_pg = pg
        # uvicorn may still be importing (heavy deps) -> OOD marks Running before
        # it's listening, so the proxy 502s briefly. Retry the page load.
        for attempt in range(12):
            try:
                app_pg.wait_for_load_state("domcontentloaded", timeout=20000)
            except Exception:
                pass
            time.sleep(3)
            title = app_pg.title() or ""
            try:
                body = app_pg.inner_text("body")
            except Exception:
                body = ""
            hay = (title + " " + body).lower()
            if VERIFY.lower() in hay:
                ok = True; log(f"connected OK (attempt {attempt}); title={title!r}"); break
            log(f"attempt {attempt}: title={title!r} body[:80]={body[:80]!r}; retry")
            try:
                app_pg.reload()
            except Exception:
                pass
        log("final app title:", repr(app_pg.title()))
        app_pg.screenshot(path=str(SHOTS / "03_app.png"), full_page=True)

    # Cleanup: delete the session
    log("cleanup: deleting session")
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sessions",
            wait_until="networkidle", timeout=60000)
    pg.on("dialog", lambda d: d.accept())
    dl = pg.get_by_role("link", name=re.compile("Delete", re.I))
    if dl.count():
        try:
            dl.first.click(); pg.wait_for_load_state("networkidle", timeout=30000); log("deleted")
        except Exception as e:
            log("delete issue:", str(e)[:120])

    b.close()
    print("RESULT:", "PASS" if ok else "INCOMPLETE")
    sys.exit(0 if ok else 2)
