"""
LIVE agent discovery spot-check (mode B of tool_discovery_testing.md).

Drives the REAL agent (Haiku) on a discovery-flavored task and prints the
conversation as it streams — agent text, tool calls, live tool_progress (#1),
plans, results — so we can watch whether the agent DISCOVERS the right tool,
installs it, and starts executing. Reuses the persistent /tmp/aba_discovery env
(so A's installs are cached → fast). Spends tokens; needs ANTHROPIC_API_KEY.

    ABA_LIVE_SCENARIO=pagoda2 .venv/bin/python -u tests/e2e/s3_live_discovery.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# Reuse A's cached materialized env; fresh DB + a scenario-local DATA_DIR.
PERSIST = Path(os.environ.get("ABA_DISC_HOME", str(Path(tempfile.gettempdir()) / "aba_discovery")))
os.environ.setdefault("ABA_ENVS_DIR", str(PERSIST / "envs"))
_run = tempfile.mkdtemp(prefix="aba_live_")
os.environ["ABA_DB_PATH"] = str(Path(_run) / "live.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_run) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_run) / "work")
os.environ["DATA_DIR"] = str(Path(_run) / "data")
Path(os.environ["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "backend"))

SCENARIO = os.environ.get("ABA_LIVE_SCENARIO", "pagoda2")

TASKS = {
    "pagoda2": (
        "I have a small single-cell RNA-seq count matrix at DATA_DIR/counts.csv "
        "(genes x cells, raw integer counts). I specifically want to analyze it with "
        "pagoda2 (the kharchenkolab R package). Set up pagoda2, build the Pagoda2 object, "
        "adjust variance, and run the PCA reduction. Keep it minimal."
    ),
    "atac": (
        "I have ATAC-seq read intervals at DATA_DIR/reads.bed (BED). Call peaks for me. "
        "Find and set up the right peak-calling tool and run it; small/quick is fine."
    ),
    "nfcore": (
        "I'd like to run a standard bulk RNA-seq quantification pipeline from nf-core on "
        "test data. Find the right nf-core pipeline, set up nextflow, and launch it on its "
        "built-in test profile."
    ),
    # Replays the real session that exposed the gaps: GEO fetch + "register as a
    # dataset". Watch for: discovers fetch-geo-processed-matrices (not flailing on
    # ENA), ensures GEOparse, and calls register_dataset (the entity op that did
    # not exist before) rather than just dumping files.
    "geo": (
        "I'd like to analyze some PBMC scRNA-seq data. Please fetch the processed "
        "count matrices for GEO sample GSM5746259 and register them as a new "
        "dataset in this project."
    ),
}


def _stage():
    d = Path(os.environ["DATA_DIR"])
    if SCENARIO == "pagoda2":
        import numpy as np, pandas as pd
        np.random.seed(0)
        m = np.random.poisson(1.0, size=(800, 120))
        df = pd.DataFrame(m, index=[f"g{i}" for i in range(800)],
                          columns=[f"c{j}" for j in range(120)])
        df.to_csv(d / "counts.csv")
    elif SCENARIO == "atac":
        import random
        random.seed(0); L = []
        for i in range(3000):
            s = random.randint(500, 1500); L.append(f"chr1\t{s}\t{s+50}\tr{i}\t0\t+")
        for i in range(300):
            s = random.randint(1, 1000000); L.append(f"chr1\t{s}\t{s+50}\tb{i}\t0\t+")
        (d / "reads.bed").write_text("\n".join(L) + "\n")


def _summ(obj, n=140):
    s = obj if isinstance(obj, str) else json.dumps(obj)
    s = " ".join(s.split())
    return s[:n] + ("…" if len(s) > n else "")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY") and not (ROOT / ".env").exists():
        print("No ANTHROPIC_API_KEY — skipping live pass."); return 2
    import content.bio  # noqa: F401
    import content.bio.lifecycle.registry  # noqa: F401
    from core.graph._schema import init_db
    init_db()
    _stage()
    from fastapi.testclient import TestClient
    from main import app

    task = TASKS[SCENARIO]
    print(f"=== LIVE discovery spot-check: {SCENARIO} (Haiku) ===", flush=True)
    print(f"USER: {task}\n", flush=True)

    state = {"run_id": None, "buf": []}
    seen = {"tools": [], "kinds": {}}

    def flush_text():
        t = "".join(state["buf"]).strip()
        state["buf"].clear()
        if t:
            print(f"🗣  {t}", flush=True)

    def consume(stream):
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type")
            seen["kinds"][t] = seen["kinds"].get(t, 0) + 1
            if ev.get("run_id"):
                state["run_id"] = ev["run_id"]
            if t == "delta":
                state["buf"].append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                flush_text()
                nm = ev.get("name") or ev.get("tool") or "?"
                seen["tools"].append(nm)
                print(f"🔧 {nm}  {_summ(ev.get('input') or {}, 110)}", flush=True)
            elif t == "tool_progress":
                print(f"    ⏳ {ev.get('message')}", flush=True)
            elif t == "tool_result":
                print(f"    ✓ {_summ(ev.get('result') or {}, 150)}", flush=True)
            elif t == "plan":
                flush_text(); print(f"📋 PLAN: {_summ(ev.get('plan') or ev, 200)}", flush=True)
            elif t == "entity_registered":
                e = ev.get("entity") or ev
                print(f"📦 {e.get('type')}: {e.get('title')}", flush=True)
            elif t in ("approval_pending", "clarification_pending", "notice", "error", "cancelled"):
                flush_text(); print(f"[{t}] {_summ(ev, 160)}", flush=True)
        flush_text()

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"text": task}) as resp:
            consume(resp)
        for hop in range(6):
            rid = state["run_id"]
            if not rid:
                break
            try:
                turn = client.get(f"/api/turns/{rid}").json()
            except Exception:
                break
            if turn.get("state") != "awaiting_user":
                break
            print(f"\n[resume {hop+1}] agent halted — replying 'go ahead'\n", flush=True)
            with client.stream("POST", f"/api/turns/{rid}/resume",
                               json={"user_text": "Yes, go ahead."}) as r2:
                consume(r2)

    print("\n=== summary ===", flush=True)
    print("tools called:", seen["tools"], flush=True)
    print("event kinds:", seen["kinds"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
