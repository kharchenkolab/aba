"""Golden-context guard for guide.stream_response's pre-loop setup assembly (Item 2B).

The assembled system prompt + tool catalog are SHARED AGENT INPUTS (see .claude/
CLAUDE.md): any refactor of the setup phases (spec/thread resolution, history
assembly, tool-catalog assembly, system-prompt assembly) MUST keep them byte-
identical — a structural/passing-tests check is insufficient. This drives a
FAKE-session turn in-process, reads the turn-context sidecar guide dumps, and
asserts the `system` prompt (sha256 + length) and the offered tool names match a
frozen golden.

`system` here is the STABLE block (sidebar + focus + thread + stable system) —
the volatile per-turn dynamic tail (compute-env line, BM25 recipes) is sent
separately and NOT captured here, so the golden is deterministic.

Regenerate intentionally (after a deliberate prompt/tool change) with:
    ABA_UPDATE_CONTEXT_GOLDEN=1 python tests/test_turn_context_golden.py

Runs standalone (base env may lack pytest) or under pytest.
"""
import os
import sys

# The assembled system prompt renders some set/dict-derived segments in hash order,
# so its bytes vary across processes (PYTHONHASHSEED) even when content is identical
# — which would make a byte-exact golden flaky (and, in production, churns the
# prompt-cache prefix on every restart — logged as a finding). Pin the seed so THIS
# guard is deterministic; must happen before any set-using import → re-exec.
if os.environ.get("PYTHONHASHSEED") != "0" and "pytest" not in sys.modules:
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])  # standalone only; under pytest set PYTHONHASHSEED=0

import glob
import hashlib
import json
import re
import tempfile
from pathlib import Path


def _normalize(system: str) -> str:
    """Neutralize benign per-run variation so the guard is robust but still catches
    real content changes: mask entity/run ids + the temp runtime path, then sort
    lines (kills set-iteration-order noise). A refactor that adds/removes/edits any
    substantive line still changes the normalized hash."""
    s = re.sub(r'\b(prj|thr|run|ana|fig|res|cl|ds|nb|oq|job)_[0-9a-f]{6,}\b', r'\1_ID', system)
    s = re.sub(r'/tmp/aba_ctxgold_[A-Za-z0-9_]+', '/TMP', s)
    return "\n".join(sorted(s.splitlines()))

try:
    import pytest
    pytestmark = pytest.mark.platform
    def _fail(m): pytest.fail(m)
except ImportError:
    pytest = None
    def _fail(m): raise AssertionError(m)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = Path(__file__).resolve().parent / "turn_context.golden.json"

# Isolate all state to a temp dir BEFORE importing the app (config reads env at import).
_tmp = tempfile.mkdtemp(prefix="aba_ctxgold_")
os.environ["ABA_FAKE_SESSION"] = str(ROOT / "tests/fixtures/list_files.jsonl")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "t.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_TURN_LOG_DIR"] = str(Path(_tmp) / "turnlog")
os.environ.setdefault("DATA_DIR", str(ROOT / "backend/data"))
sys.path.insert(0, str(ROOT / "backend"))


def _capture() -> dict:
    """Drive one FAKE turn; return the assembled-context fingerprint."""
    from fastapi.testclient import TestClient
    from main import app
    with TestClient(app) as client:
        with client.stream("POST", "/api/chat",
                           json={"text": "what files are here?",
                                 "thread_id": "default",
                                 "focus_entity_id": "workspace"}) as r:
            for _ in r.iter_text():
                pass
    files = sorted(glob.glob(os.path.join(os.environ["ABA_TURN_LOG_DIR"], "*_run_*.json")))
    assert files, "no turn-context sidecar was dumped — did the turn start?"
    payload = json.loads(Path(files[-1]).read_text())
    system = payload.get("system") or ""
    names = sorted(n for n in (payload.get("tools") or []) if n)
    return {
        "tool_names": names,
        "n_tools": len(names),
        "system_len": len(system),
        "system_norm_sha256": hashlib.sha256(_normalize(system).encode("utf-8")).hexdigest(),
    }


def test_turn_context_matches_golden():
    cur = _capture()
    assert GOLDEN.exists(), (
        f"no golden at {GOLDEN.relative_to(ROOT)} — generate with "
        f"ABA_UPDATE_CONTEXT_GOLDEN=1 python tests/test_turn_context_golden.py")
    gold = json.loads(GOLDEN.read_text())
    if cur["tool_names"] != gold["tool_names"]:
        added = sorted(set(cur["tool_names"]) - set(gold["tool_names"]))
        removed = sorted(set(gold["tool_names"]) - set(cur["tool_names"]))
        _fail(f"offered tool catalog changed — added={added} removed={removed}")
    if cur["system_norm_sha256"] != gold["system_norm_sha256"]:
        _fail(f"assembled system prompt changed (normalized; len {gold['system_len']} → "
              f"{cur['system_len']}). A setup-phase refactor must preserve it; if the "
              f"change is intentional, regenerate the golden.")


if __name__ == "__main__":
    if os.environ.get("ABA_UPDATE_CONTEXT_GOLDEN") == "1":
        fp = _capture()
        GOLDEN.write_text(json.dumps(fp, indent=2) + "\n")
        print(f"wrote golden: {fp['n_tools']} tools, system {fp['system_len']} chars")
    else:
        test_turn_context_matches_golden()
        g = json.loads(GOLDEN.read_text())
        print(f"PASS test_turn_context_matches_golden ({g['n_tools']} tools, "
              f"system {g['system_len']} chars)")
