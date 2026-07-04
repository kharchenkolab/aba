"""Resource-placement robustness study (restest branch).

Drives LIVE agent turns (real /api/chat → guide → Anthropic) in-process (TestClient)
while INJECTING a synthetic compute environment, so we can see how the agent routes
the SAME pipeline request under a laptop / workstation-GPU / small-cluster / big-busy-
cluster / infeasible box. Heavy execution is stubbed (we study the DECISION, not the
compute); the agent's tool-call args (background / est_gpu / est_cores / …) and its
reasoning text are captured from the SSE stream, and the router's resulting placement
is computed for comparison.

Run (from repo root):
  ABA_STUDY_MODEL=claude-opus-4-8 env/bin/python regtest/placement/study.py [--only name,name]
"""
from __future__ import annotations
import json
import os
import sys
import time
from pathlib import Path

INSTALL = Path("/users/peter.kharchenko/data/aba/install")
REPO = Path("/groups/tanaka/People/current/PeterK/aba/aba")


# ── 1. config.env (creds + ABA_HOME + bundles), like the launcher sources it ──
def _load_config_env() -> None:
    cfg = INSTALL / "config.env"
    if not cfg.exists():
        return
    for line in cfg.read_text().splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:]
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_config_env()
# ABA_HOME (set by the launcher, NOT config.env) — needed to load the installation
# bundle = the recipe pack ($ABA_HOME/installation/skills/recipes). Without it the
# agent is UNPRIMED (no scVI/integration recipe), which would confound the study.
os.environ["ABA_HOME"] = str(INSTALL)

# ── 2. isolate runtime state to a throwaway study dir ─────────────────────────
RUN = Path("/tmp/aba_placement_study") / time.strftime("run-%Y%m%d-%H%M%S")
RUN.mkdir(parents=True, exist_ok=True)
os.environ["ABA_RUNTIME_DIR"] = str(RUN)
os.environ["ABA_DB_PATH"] = str(RUN / "live.db")
os.environ["ABA_RAW_REQUEST_DIR"] = str(RUN / "rawreq")
os.environ.setdefault("ABA_ENVS_DIR", "/tmp/aba_discovery/envs")
os.environ["ABA_MODEL"] = os.environ.get("ABA_STUDY_MODEL", "claude-opus-4-8")

# ── 3. sys.path → WORKING-TREE backend (test restest code directly) ───────────
sys.path.insert(0, str(REPO / "backend"))

import content.bio  # noqa: E402,F401
from core.graph._schema import init_db  # noqa: E402
init_db()
from fastapi.testclient import TestClient  # noqa: E402
from main import app  # noqa: E402
import core.exec.compute_env as CE  # noqa: E402
import content.bio.tools as BT  # noqa: E402
import guide as GUIDE  # noqa: E402
import core.jobs.runner as RUNNER  # noqa: E402
from core.exec.router import LocalRouter  # noqa: E402


# ── compute_env injection ─────────────────────────────────────────────────────
_ORIG_BUILD = CE._build_compute_env


def set_compute_env(env: dict | None) -> None:
    """Patch compute_env so context_line() + the router see `env` (None = real)."""
    if env is None:
        CE._build_compute_env = _ORIG_BUILD
    else:
        CE._build_compute_env = lambda: dict(env)
    CE._CACHE.update(ts=0.0, env=None)   # bust the 20s cache


# ── execution block (we study the DECISION, not the compute) ──────────────────
_STUB = {"n": 0, "facts": "", "gave_facts": False}


def _stub_run(input_, ctx=None):
    # Return the scenario's data facts ONCE (the agent's first inspection verifies the
    # object), then silent success for later prep/compute calls. Returning the same
    # facts string on EVERY call makes a careful agent detect a fake execution layer
    # and halt — so give facts once, then empty (a step that "ran, no output").
    _STUB["n"] += 1
    code = (input_.get("code") or "") if isinstance(input_, dict) else ""
    inspecting = len(code) < 700 and any(
        k in code for k in ("print", "shape", "value_counts", "repr(", ".obs", "exists",
                            ".head", "unique", "dtype", "n_obs", "info("))
    if inspecting and not _STUB["gave_facts"]:
        _STUB["gave_facts"] = True
        out = _STUB["facts"]
    else:
        out = ""
    return {"status": "ok", "returncode": 0, "stdout": out, "stderr": "",
            "plots": [], "tables": [], "files": [], "execution_mode": "session"}


def _stub_submit_py(code, title, focus_entity_id=None, **kw):
    _STUB["n"] += 1
    return {"id": f"job_stub_{_STUB['n']}", "status": "queued", "kind": "run_python"}


def _stub_submit_r(code, title, focus_entity_id=None, **kw):
    _STUB["n"] += 1
    return {"id": f"job_stub_{_STUB['n']}", "status": "queued", "kind": "run_r"}


def _stub_ensure(input_, ctx=None):
    """Provisioning is not what we're studying — canned 'available' so the agent
    proceeds to the placement decision without a slow real install."""
    name = input_.get("name") if isinstance(input_, dict) else input_
    return {"ok": True, "status": "ok", "capability": name,
            "message": f"{name} is available (placement-study stub)"}


BT.run_python = _stub_run
BT.run_r = _stub_run
BT.ensure_capability = _stub_ensure
GUIDE.submit_python_job = _stub_submit_py
RUNNER.submit_python_job = _stub_submit_py
RUNNER.submit_r_job = _stub_submit_r


# ── drive one turn, capture decision + reasoning ──────────────────────────────
def drive_turn(client, pid, tid, text):
    cap = {"tools": [], "text": [], "kinds": {}, "run_id": None, "plan": None}
    with client.stream("POST", "/api/chat",
                       json={"text": text, "project_id": pid, "thread_id": tid}) as r:
        for line in r.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            cap["kinds"][t] = cap["kinds"].get(t, 0) + 1
            if ev.get("run_id"):
                cap["run_id"] = ev["run_id"]
            if t == "delta":
                cap["text"].append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                nm = ev.get("name") or ev.get("tool")
                inp = ev.get("input") or {}
                cap["tools"].append({"name": nm, "input": inp})
                if nm == "present_plan":
                    cap["plan"] = inp
    cap["text"] = "".join(cap["text"]).strip()
    return cap


def _exec_decisions(cap):
    """The run_python/run_r tool calls = the placement decisions."""
    return [t for t in cap["tools"] if t["name"] in ("run_python", "run_r")]


def router_for(env, tool_input):
    est = {"runtime_min": float(tool_input.get("estimated_runtime_min") or 0),
           "cores": tool_input.get("est_cores"), "mem_gb": tool_input.get("est_mem_gb"),
           "gpu": tool_input.get("est_gpu")}
    override = "background" if tool_input.get("background") else None
    ch = LocalRouter().route(env=env, estimate=est, override=override)
    return {"location": ch.location, "rationale": ch.rationale}


# ── scenarios ─────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scenarios_data import SCENARIOS  # noqa: E402


def run():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    client = TestClient(app).__enter__()
    results = []
    for sc in SCENARIOS:
        if only and sc["name"] not in only:
            continue
        set_compute_env(sc["compute_env"])
        _STUB["facts"] = sc.get("data_facts", "")
        _STUB["gave_facts"] = False
        ctx_line = CE.context_line()
        pid = client.post("/api/projects", json={"name": f"plc-{sc['name']}"}).json()["id"]
        client.post(f"/api/projects/{pid}/open")
        tid = client.post("/api/threads", json={"project_id": pid, "title": sc["name"]}).json()["id"]
        t0 = time.time()
        # Turn 1: the request — the agent plans (heavy work is plan-first).
        cap1 = drive_turn(client, pid, tid, sc["prompt"])
        # Turn 2: approve + answer the usual blockers — the agent executes here.
        cap2 = drive_turn(client, pid, tid, sc.get("approve", "Approved — go ahead and run it now."))
        dt = time.time() - t0
        decisions = _exec_decisions(cap1) + _exec_decisions(cap2)
        routed = [{"turn": ti + 1, "code_head": (d["input"].get("code") or "")[:80],
                   "input": {k: d["input"].get(k) for k in
                             ("background", "est_gpu", "est_cores", "est_mem_gb",
                              "estimated_runtime_min", "execution")},
                   "router": router_for(sc["compute_env"], d["input"])}
                  for ti, cap in ((0, cap1), (1, cap2)) for d in _exec_decisions(cap)]
        rec = {"name": sc["name"], "expected": sc["expected"], "context_line": ctx_line,
               "n_exec_calls": len(decisions), "decisions": routed,
               "turn1_tools": [t["name"] for t in cap1["tools"]],
               "turn2_tools": [t["name"] for t in cap2["tools"]],
               "plan": cap1.get("plan") or cap2.get("plan"),
               "turn1_reply": cap1["text"], "turn2_reply": cap2["text"],
               "secs": round(dt, 1)}
        results.append(rec)
        print(f"\n{'='*90}\n[{sc['name']}]  ({dt:.0f}s)  expected: {sc['expected']}")
        print(f"  compute cue: {ctx_line[:300]}")
        print(f"  turn1 tools: {rec['turn1_tools']}   turn2 tools: {rec['turn2_tools']}")
        for r in routed:
            print(f"  DECISION (t{r['turn']}): {r['input']}  ->  router={r['router']['location']} "
                  f"({r['router']['rationale']})  code[{r['code_head']}]")
        if not decisions:
            print("  NO run_python/run_r in either turn — agent stayed in text/plan")
        print(f"  turn1 reply: {cap1['text'][:400]}")
        print(f"  turn2 reply: {cap2['text'][:400]}")
        try:
            client.request("DELETE", f"/api/projects/{pid}")
        except Exception:
            pass
    out = RUN / "results.json"
    out.write_text(json.dumps(results, indent=2))
    print(f"\n\nwrote {out}")
    return results


if __name__ == "__main__":
    run()
