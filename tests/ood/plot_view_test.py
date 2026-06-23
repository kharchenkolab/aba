import _pwenv as E, re, time, ssl, json, base64, urllib.request
from playwright.sync_api import sync_playwright
def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH); pg = ctx.new_page()
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sys/aba/session_contexts/new", wait_until="networkidle", timeout=60000)
    pg.click("input[type=submit][value=Launch]"); pg.wait_for_load_state("networkidle", timeout=60000)
    href=None; t0=time.time()
    while time.time()-t0<180:
        pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sessions", wait_until="networkidle", timeout=60000)
        l=pg.get_by_role("link", name=re.compile("Connect to ABA", re.I))
        if l.count(): href=l.first.get_attribute("href"); break
        time.sleep(4)
    m=re.search(r"/rnode/([^/]+)/(\d+)/", href or ""); host,port=m.group(1),m.group(2); log("session", host, port)
    cx=ssl.create_default_context(); cx.check_hostname=False; cx.verify_mode=ssl.CERT_NONE
    A="Basic "+base64.b64encode(b"ood:ood").decode(); base=f"{E.BASE}/rnode/{host}/{port}"
    def rq(u,data=None,ct=None):
        r=urllib.request.Request(u,data=data); r.add_header("Authorization",A)
        if ct: r.add_header("Content-Type",ct)
        return r
    for _ in range(25):
        try:
            r=urllib.request.urlopen(rq(base+"/api/health"),context=cx,timeout=5); st=r.status; r.close()
            if st==200: break
        except Exception: pass
        time.sleep(4)
    r=urllib.request.urlopen(rq(base+"/api/projects",data=json.dumps({"name":"plot view"}).encode(),ct="application/json"),context=cx,timeout=30)
    pid=json.loads(r.read()).get("id"); r.close(); log("pid", pid)
    body=json.dumps({"text":"Use run_python to make a matplotlib histogram of [1,2,2,3,3,3] and display the figure.","project_id":pid}).encode()
    r=urllib.request.urlopen(rq(base+"/api/chat",data=body,ct="application/json"),context=cx,timeout=180)
    for raw in r:
        s=raw.decode(errors="replace").strip()
        if s.startswith("data:") and '"type": "done"' in s.replace('"type":"done"','"type": "done"'): break
    r.close(); log("run_python done; loading SPA")
    pg.goto(base+"/", wait_until="domcontentloaded", timeout=60000); time.sleep(6)
    # open the project
    for sel in [lambda: pg.get_by_role("link", name=re.compile("Open project", re.I)).first,
                lambda: pg.get_by_text(re.compile("Open project", re.I)).first]:
        try: sel().click(timeout=6000); break
        except Exception: pass
    pg.wait_for_load_state("domcontentloaded", timeout=30000); time.sleep(9)
    log("url now:", pg.url)
    imgs = pg.eval_on_selector_all("img", "els => els.map(e=>({src:e.src.slice(0,80), nat:e.naturalWidth, complete:e.complete}))")
    log("ALL imgs on page:", imgs)
    arts=[o for o in imgs if 'artifact' in o['src']]
    pg.screenshot(path="_shots/04_plot.png", full_page=True)
    loaded = any(o["nat"]>0 and o["complete"] for o in arts)
    log("RESULT:", "PLOT RENDERS" if loaded else ("NO ARTIFACT IMG" if not arts else "PLOT BROKEN(404)"))
    b.close()
