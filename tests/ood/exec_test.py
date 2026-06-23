import _pwenv as E, os, re, time, ssl, json, base64, urllib.request
from playwright.sync_api import sync_playwright
PROMPT = os.environ.get("ABA_PROMPT", "Use run_python to compute numpy.mean([2,4,6]) and print it.")
def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)
with sync_playwright() as p:
    b = p.chromium.launch(args=E.LAUNCH_ARGS)
    ctx = b.new_context(ignore_https_errors=True, http_credentials=E.AUTH); pg = ctx.new_page()
    pg.goto(f"{E.BASE}/pun/sys/dashboard/batch_connect/sys/aba/session_contexts/new", wait_until="networkidle", timeout=60000)
    pg.click("input[type=submit][value=Launch]"); pg.wait_for_load_state("networkidle", timeout=60000)  # cached cred, no token
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
r=urllib.request.urlopen(rq(base+"/api/projects",data=json.dumps({"name":"exec test"}).encode(),ct="application/json"),context=cx,timeout=30)
pid=json.loads(r.read()).get("id"); r.close(); log("pid", pid)
log("PROMPT:", PROMPT)
body=json.dumps({"text":PROMPT,"project_id":pid}).encode()
evcount={}; tools=[]; arts=[]; errs=[]; txt=[]
try:
    r=urllib.request.urlopen(rq(base+"/api/chat",data=body,ct="application/json"),context=cx,timeout=300)
    for raw in r:
        s=raw.decode(errors="replace").strip()
        if not s.startswith("data:"): continue
        try: ev=json.loads(s[5:].strip())
        except: continue
        t=ev.get("type"); evcount[t]=evcount.get(t,0)+1
        if t=="delta": txt.append(ev.get("text",""))
        elif t in ("tool_use","tool_call"): tools.append(ev.get("name"))
        elif t=="tool_result": log("tool_result:", ev.get("name"), "| keys:", list(ev.get("result",{}).keys()) if isinstance(ev.get("result"),dict) else type(ev.get("result")).__name__, "| ok:", not ev.get("is_error") and 'error' not in str(ev)[:300].lower())
        elif t in ("artifact","image","figure"): arts.append(ev)
        elif t=="error": errs.append(ev); log("ERROR:", str(ev)[:300])
        elif t=="done": log("done"); break
    r.close()
except Exception as e: log("request failed:", repr(e)[:200])
log("event counts:", evcount)
log("tools called:", tools)
log("reply text[:200]:", repr("".join(txt)[:200]))
log("RESULT:", "OK" if (not errs and ("run_python" in tools or "run_r" in tools or evcount.get('tool_result'))) else "CHECK")
