"""
Run a scenario from the regtest/scenarios library against the live agent (Haiku),
stream the transcript, and auto-check expected.must_mention / must_not against
the agent's text. Detailed per-turn context dumps land in ABA_TURN_LOG_DIR for
deeper analysis.

    ABA_SCENARIO=enrichment .venv/bin/python -u regtest/harness/library_runner.py
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
LIB = ROOT / "regtest" / "scenarios"
PERSIST = Path(os.environ.get("ABA_DISC_HOME", str(Path(tempfile.gettempdir()) / "aba_discovery")))
os.environ.setdefault("ABA_ENVS_DIR", str(PERSIST / "envs"))
os.environ.setdefault("ABA_TURN_LOG_DIR", "/tmp/aba_lib_turnlog")
_run = tempfile.mkdtemp(prefix="aba_lib_")
os.environ["ABA_DB_PATH"] = str(Path(_run) / "live.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_run) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_run) / "work")
os.environ["DATA_DIR"] = str(Path(_run) / "data")
Path(os.environ["DATA_DIR"]).mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "backend"))

SCENARIO = os.environ.get("ABA_SCENARIO", "enrichment")


def _summ(obj, n=170):
    s = obj if isinstance(obj, str) else json.dumps(obj)
    return " ".join(s.split())[:n] + ("…" if len(" ".join(s.split())) > n else "")


def main() -> int:
    spec = yaml.safe_load((LIB / SCENARIO / "scenario.yaml").read_text())
    task = spec["prompt"]
    # stage data
    src = LIB / SCENARIO / "data"
    if src.is_dir():
        for f in src.iterdir():
            shutil.copy(f, Path(os.environ["DATA_DIR"]) / f.name)
    if not os.environ.get("ANTHROPIC_API_KEY") and not (ROOT / ".env").exists():
        print("No ANTHROPIC_API_KEY — skipping."); return 2
    import content.bio  # noqa: F401
    import content.bio.lifecycle.registry  # noqa: F401
    from core.graph._schema import init_db
    init_db()
    from fastapi.testclient import TestClient
    from main import app

    print(f"=== SCENARIO: {SCENARIO} ({spec.get('domain')}) (Haiku) ===", flush=True)
    print(f"USER: {task}\n", flush=True)
    state = {"run_id": None, "buf": [], "all_text": []}
    seen = {"tools": [], "kinds": {}}

    def flush_text():
        t = "".join(state["buf"]).strip(); state["buf"].clear()
        if t:
            state["all_text"].append(t)
            print(f"🗣  {t}", flush=True)

    def consume(stream):
        for line in stream.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                ev = json.loads(line[6:])
            except Exception:
                continue
            t = ev.get("type"); seen["kinds"][t] = seen["kinds"].get(t, 0) + 1
            if ev.get("run_id"):
                state["run_id"] = ev["run_id"]
            if t == "delta":
                state["buf"].append(ev.get("text") or ev.get("delta") or "")
            elif t == "tool_start":
                flush_text(); nm = ev.get("name") or ev.get("tool") or "?"
                seen["tools"].append(nm); print(f"🔧 {nm}  {_summ(ev.get('input') or {}, 120)}", flush=True)
            elif t == "tool_progress":
                print(f"    ⏳ {_summ(ev.get('message'), 110)}", flush=True)
            elif t == "tool_result":
                print(f"    ✓ {_summ(ev.get('result') or {}, 180)}", flush=True)
            elif t == "plan":
                flush_text(); print(f"📋 PLAN: {_summ(ev.get('plan') or ev, 200)}", flush=True)
            elif t == "entity_registered":
                e = ev.get("entity") or ev; print(f"📦 {e.get('type')}: {e.get('title')}", flush=True)
            elif t in ("approval_pending", "clarification_pending", "notice", "error", "cancelled"):
                flush_text(); print(f"[{t}] {_summ(ev, 180)}", flush=True)
        flush_text()

    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"text": task}) as resp:
            consume(resp)
        for hop in range(5):
            rid = state["run_id"]
            if not rid:
                break
            try:
                turn = client.get(f"/api/turns/{rid}").json()
            except Exception:
                break
            if turn.get("state") != "awaiting_user":
                break
            print("\n[resume] → Yes, go ahead.\n", flush=True)
            with client.stream("POST", f"/api/turns/{rid}/resume", json={"user_text": "Yes, go ahead."}) as r2:
                consume(r2)

    # auto-check expected
    blob = " ".join(state["all_text"]).lower()
    exp = spec.get("expected", {}) or {}
    miss = [m for m in (exp.get("must_mention") or []) if m.lower() not in blob]
    bad = [m for m in (exp.get("must_not") or []) if m.lower() in blob]
    verdict = "PASS" if not miss and not bad else "CHECK"
    print("\n=== summary ===", flush=True)
    print("tools called:", seen["tools"], flush=True)
    print(f"AUTOCHECK [{verdict}]  missing_mention={miss}  present_forbidden={bad}", flush=True)
    print(f"expected.notes: {exp.get('notes')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
