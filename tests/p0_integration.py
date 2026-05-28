"""
P0 integration gate: boot the app in-process (FastAPI TestClient) in fake mode,
run a scripted Guide turn that calls run_python to make a histogram, and assert
the figure auto-registers as an entity. This exercises the live path Stage 2
touched: run_python (now executor + scratch) → on_post_tool registration hook.

No model / API key (ABA_FAKE_SESSION). Isolated DB + artifact + work dirs.

Run:
    .venv/bin/python tests/p0_integration.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p0int_")
os.environ["ABA_FAKE_SESSION"] = str(ROOT / "tests/fixtures/produce_hist.jsonl")
os.environ["ABA_DB_PATH_OVERRIDE"] = str(Path(_tmp) / "p0.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(ROOT / "backend/data")     # holds cells.csv for the fixture
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient                 # noqa: E402
from main import app                                      # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def parse_sse(body: str):
    return [json.loads(l[6:]) for l in body.splitlines() if l.startswith("data: ")]


def main() -> int:
    with TestClient(app) as client:
        with client.stream("POST", "/api/chat", json={"text": "plot mt_fraction"}) as resp:
            check("chat endpoint 200", resp.status_code == 200, str(resp.status_code))
            body = "".join(resp.iter_text())
        events = parse_sse(body)
        kinds = [e.get("type") or e.get("event") for e in events]
        print(f"  (SSE event kinds: {sorted(set(k for k in kinds if k))})")

        # run_python ran and returned a plot somewhere in the stream.
        blob = json.dumps(events)
        check("run_python tool ran", "run_python" in blob)
        check("a plot artifact url was produced", "/artifacts/" in blob and ".png" in blob)

        # The figure auto-registered as an entity (the on_post_tool hook).
        ents = client.get("/api/entities").json()
        rows = ents if isinstance(ents, list) else ents.get("entities", ents.get("items", []))
        figures = [e for e in rows if e.get("type") == "figure"]
        check("a figure entity was registered", len(figures) >= 1, f"{len(rows)} entities total")
        if figures:
            ap = figures[0].get("artifact_path") or ""
            check("figure artifact_path points at the artifact store",
                  ap.startswith("/artifacts/"), ap)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P0 INTEGRATION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
