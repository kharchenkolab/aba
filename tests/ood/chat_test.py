import _pwenv as E, os, re, time, ssl, json, base64, urllib.request
from playwright.sync_api import sync_playwright
TOKEN = os.environ["ABA_TEST_TOKEN"]
def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH); pg = ctx.new_page()
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sys/aba/session_contexts/new", wait_until="networkidle", timeout=60000)
    pg.fill("#batch_connect_session_context_aba_token", TOKEN)
    pg.click("input[type=submit][value=Launch]"); pg.wait_for_load_state("networkidle", timeout=60000)
    href=None; t0=time.time()
    while time.time()-t0<180:
        pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sessions", wait_until="networkidle", timeout=60000)
        l=pg.get_by_role("link", name=re.compile("Connect to ABA", re.I))
        if l.count(): href=l.first.get_attribute("href"); break
        time.sleep(4)
    b.close()
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
        if st==200: log("uvicorn ready"); break
    except Exception as e: log("waiting:",repr(e)[:50])
    time.sleep(4)
# create a project
r=urllib.request.urlopen(rq(base+"/api/projects",data=json.dumps({"name":"OOD smoke"}).encode(),ct="application/json"),context=cx,timeout=30)
proj=json.loads(r.read()); r.close(); log("project resp:", str(proj)[:160])
pid=proj.get("id") or proj.get("pid") or proj.get("project_id")
log("pid:", pid)
log("POST /api/chat with project")
body=json.dumps({"text":"Reply with exactly five words.","project_id":pid}).encode()
deltas=[]; err=None
try:
    r=urllib.request.urlopen(rq(base+"/api/chat",data=body,ct="application/json"),context=cx,timeout=120)
    for raw in r:
        s=raw.decode(errors="replace").strip()
        if s.startswith("data:"):
            try: ev=json.loads(s[5:].strip())
            except: continue
            tp=ev.get("type")
            if tp=="delta": deltas.append(ev.get("text",""))
            elif tp=="error": err=ev; log("ERROR:", str(ev)[:200])
            elif tp=="done": log("done"); break
    r.close()
except Exception as e: log("request failed:", repr(e)[:200])
log("reply:", repr("".join(deltas)[:200]))
log("RESULT:", "CHAT OK" if (deltas and not err) else "CHAT FAILED")
