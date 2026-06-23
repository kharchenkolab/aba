"""Measure OOD proxy SSE buffering: launch the sse_probe app, then read its
/sse stream THROUGH the OOD reverse proxy and time each event's arrival.
Verdict STREAMED = events ~400ms apart (proxy flushes); BUFFERED = all arrive
together near the end.
"""
import _pwenv as E
import re, time, ssl, base64, urllib.request, statistics as st
from playwright.sync_api import sync_playwright


def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)


with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH)
    pg = ctx.new_page()
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sys/sse_probe/session_contexts/new",
            wait_until="networkidle", timeout=60000)
    pg.click("input[type=submit][value=Launch]")
    pg.wait_for_load_state("networkidle", timeout=60000)

    href = None
    t0 = time.time()
    while time.time() - t0 < 180:
        pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sessions",
                wait_until="networkidle", timeout=60000)
        link = pg.get_by_role("link", name=re.compile("Connect to SSE Probe", re.I))
        if link.count():
            href = link.first.get_attribute("href"); break
        time.sleep(4)
    log("connect href:", href)
    m = re.search(r"/rnode/([^/]+)/(\d+)/", href or "")
    if not m:
        log("no session; abort"); b.close(); raise SystemExit(2)
    host, port = m.group(1), m.group(2)
    sse_url = f"{E.BASE}/rnode/{host}/{port}/sse"
    log("streaming", sse_url)

    cx = ssl.create_default_context(); cx.check_hostname = False; cx.verify_mode = ssl.CERT_NONE
    AUTHH = "Basic " + base64.b64encode(b"ood:ood").decode()

    def _req(u):
        r = urllib.request.Request(u); r.add_header("Authorization", AUTHH); return r

    # Wait for the probe server to be reachable through the proxy (avoid 503 race).
    base = f"{E.BASE}/rnode/{host}/{port}/"
    for _ in range(20):
        try:
            r = urllib.request.urlopen(_req(base), context=cx, timeout=5)
            code = r.status; r.read(50); r.close()
            if code == 200:
                log("probe reachable"); break
            log("probe status", code)
        except Exception as e:
            log("probe not ready:", repr(e)[:70])
        time.sleep(2)

    arrivals = []
    start = time.time()
    try:
        resp = urllib.request.urlopen(_req(sse_url), context=cx, timeout=30)
        for raw in resp:
            if raw.decode(errors="replace").startswith("data:"):
                arrivals.append(time.time() - start)
                if len(arrivals) >= 12:
                    break
        resp.close()
    except Exception as e:
        log("stream error:", repr(e))

    gaps = [round(arrivals[i] - arrivals[i - 1], 3) for i in range(1, len(arrivals))]
    log("events:", len(arrivals))
    log("arrival times (s):", [round(a, 2) for a in arrivals])
    log("inter-event gaps (s):", gaps)
    if arrivals:
        med = st.median(gaps) if gaps else 0
        log(f"first={arrivals[0]:.2f}s  median_gap={med:.2f}s  total={arrivals[-1]:.2f}s")
        verdict = ("STREAMED (proxy flushes SSE)" if med >= 0.25
                   else "BUFFERED (events batched)" if arrivals[0] > 2.0
                   else "INCONCLUSIVE")
        log("VERDICT:", verdict)

    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sessions", wait_until="networkidle", timeout=60000)
    pg.on("dialog", lambda d: d.accept())
    dl = pg.get_by_role("link", name=re.compile("Delete", re.I))
    if dl.count():
        try:
            dl.first.click(); pg.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
    b.close()
