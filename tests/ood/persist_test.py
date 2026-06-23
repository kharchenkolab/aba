import _pwenv as E, os, re, time, ssl, json, base64, urllib.request
from playwright.sync_api import sync_playwright
PHASE=os.environ.get("PHASE","1"); MARK="PERSIST-MARK-OOD"
def log(*a): print(f"[{time.strftime('%H:%M:%S')}] P{PHASE}", *a, flush=True)
with sync_playwright() as p:
    b=p.chromium.launch(args=E.LAUNCH_ARGS); ctx=b.new_context(ignore_https_errors=True, http_credentials=E.AUTH); pg=ctx.new_page()
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sys/aba/session_contexts/new", wait_until="networkidle", timeout=60000)
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
        if st==200: break
    except Exception: pass
    time.sleep(4)
if PHASE=="1":
    r=urllib.request.urlopen(rq(base+"/api/projects",data=json.dumps({"name":MARK}).encode(),ct="application/json"),context=cx,timeout=30)
    pid=json.loads(r.read()).get("id"); r.close(); log("created project", MARK, pid)
else:
    r=urllib.request.urlopen(rq(base+"/api/projects"),context=cx,timeout=30)
    data=json.loads(r.read()); r.close()
    items=data if isinstance(data,list) else data.get("projects",data.get("items",[]))
    names=[ (x.get("name") if isinstance(x,dict) else x) for x in items ]
    log("projects seen:", names)
    log("RESULT:", "PERSISTED" if MARK in names else "LOST")
