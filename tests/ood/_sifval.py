#!/usr/bin/env python3
"""Validate ABA runs end-to-end FROM the apptainer image (sandbox or .sif),
host-side. Starts apptainer with the host mock dirs bound to /groups + /cluster/aba,
then drives health / bundle-scope / chat+run_python against localhost.
Usage: _sifval.py <sandbox-or-sif-path>
"""
import json, os, sys, time, subprocess, signal, urllib.request

IMG = sys.argv[1] if len(sys.argv) > 1 else "/home/pkharchenko/aba/tools/aba_sandbox/"
PORT = 8765
APP = "/home/pkharchenko/aba/tools/apptainer-env/bin/apptainer"
GROUPS = "/home/pkharchenko/aba/ood-groups"
CLUSTER = "/home/pkharchenko/aba/ood-cluster"
KEY = os.environ.get("ABA_TEST_TOKEN") or json.load(open(f"{GROUPS}/kharchenko/aba/ood/credentials.json"))["anthropic_api_key"]
RT = f"{GROUPS}/kharchenko/aba/sif-test"


def log(*a): print(f"[{time.strftime('%H:%M:%S')}]", *a, flush=True)
def rq(u, data=None, ct=None):
    r = urllib.request.Request(u, data=data)
    if ct: r.add_header("Content-Type", ct)
    return r


subprocess.run(["rm", "-rf", RT]); os.makedirs(f"{RT}/.home", exist_ok=True)
env = dict(os.environ, APPTAINER_CACHEDIR="/home/pkharchenko/aba/tools/apptainer-cache",
          TMPDIR="/home/pkharchenko/aba/tools/apptainer-tmp",
          # apptainer-env/bin on PATH so it finds squashfuse -> direct SIF mount (no extraction)
          PATH="/home/pkharchenko/aba/tools/apptainer-env/bin:" + os.environ.get("PATH", ""))
cmd = [APP, "run", "--containall",
       "--bind", f"{GROUPS}:/groups", "--bind", f"{CLUSTER}:/cluster/aba",
       "--env", "ABA_SITE_CONFIG=/cluster/aba/site.yaml",
       "--env", "ABA_GROUP=kharchenko",
       "--env", "ABA_RUNTIME_DIR=/groups/kharchenko/aba/sif-test",
       "--env", "ABA_ENVS_DIR=/groups/kharchenko/aba/.envs",
       "--env", f"ANTHROPIC_API_KEY={KEY}", "--env", "ABA_LLM_CREDENTIAL=apikey",
       "--env", f"ABA_PORT={PORT}", IMG]
log("starting apptainer:", IMG)
proc = subprocess.Popen(cmd, env=env, stdout=open("/tmp/sif_uvicorn.log", "w"), stderr=subprocess.STDOUT)
base = f"http://127.0.0.1:{PORT}"
res = {"img": IMG}

ready = False
for _ in range(50):
    if proc.poll() is not None:
        log("apptainer EXITED early rc=", proc.returncode); break
    try:
        r = urllib.request.urlopen(rq(base + "/api/health"), timeout=5)
        if r.status == 200: ready = True; r.close(); break
        r.close()
    except Exception:
        pass
    time.sleep(3)
res["health"] = ready
log("health:", "READY" if ready else "NOT READY")

if ready:
    try:
        r = urllib.request.urlopen(base + "/api/bundle/state", timeout=8); d = json.loads(r.read()); r.close()
        res["scopes"] = [(s["name"], s["present"]) for s in d["scope_chain"]]
    except Exception as e:
        res["scopes"] = repr(e)[:80]
    try:
        r = urllib.request.urlopen(rq(base + "/api/projects", data=json.dumps({"name": "SIF smoke"}).encode(),
                                       ct="application/json"), timeout=30)
        pid = json.loads(r.read()).get("id"); r.close(); log("pid", pid)
        body = json.dumps({"text": "Use run_python to compute numpy.mean([2,4,6]) and print it.",
                           "project_id": pid}).encode()
        tools, txt, errs, tres = [], [], [], []
        r = urllib.request.urlopen(rq(base + "/api/chat", data=body, ct="application/json"), timeout=300)
        for raw in r:
            s = raw.decode(errors="replace").strip()
            if not s.startswith("data:"): continue
            try: ev = json.loads(s[5:].strip())
            except Exception: continue
            t = ev.get("type")
            if t == "delta": txt.append(ev.get("text", ""))
            elif t in ("tool_use", "tool_call"): tools.append(ev.get("name"))
            elif t == "tool_result": tres.append(ev.get("name"))
            elif t == "error": errs.append(ev); log("ERR", str(ev)[:150])
            elif t == "done": break
        r.close()
        res["tools"] = tools; res["tool_results"] = tres
        res["reply"] = "".join(txt)[:160]; res["errs"] = len(errs)
        log("tools:", tools, "| reply:", res["reply"])
    except Exception as e:
        res["chat_err"] = repr(e)[:200]; log("chat exception:", res["chat_err"])

proc.send_signal(signal.SIGTERM)
try: proc.wait(timeout=15)
except Exception: proc.kill()

log("RESULT:", json.dumps({k: res[k] for k in res if k != "reply"}))
ok = (res.get("health") and (res.get("tools") or res.get("tool_results"))
      and not res.get("errs") and not res.get("chat_err"))
log("VERDICT:", "PASS" if ok else "CHECK (see /tmp/sif_uvicorn.log)")
