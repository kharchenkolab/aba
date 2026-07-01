"""P1 — settle_deferred_job resolves a parked deferred turn on job termination.

The single primitive that resolves a background job's held tool_use for EVERY kind
(run_python/run_r/run_nextflow) and EVERY terminal status (done/failed/cancelled/
interrupted): writes a terminal tool_result + transitions the turn out of
AWAITING_TOOL_RESULT. Purely DB-driven (the parked turn lives in the runs table), so it
also works after a restart. Idempotent.

Run: .venv/bin/python tests/test_settle_deferred.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_settle_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import _conn, init_db  # noqa: E402
init_db()
from core.runtime import checkpoint as ck  # noqa: E402
from core.runtime.turn import Turn, TurnState  # noqa: E402


def _park(run_id: str, job_id: str, tool_use_id: str, thread_id: str = "thr_x") -> None:
    ck.checkpoint(Turn(
        run_id=run_id, session_id="sess", turn_index=0, agent_spec_name="guide",
        state=TurnState.AWAITING_TOOL_RESULT, focus_entity_id="workspace",
        thread_id=thread_id, entity_id="workspace",
        pending_deferred={"tool_name": "run_nextflow", "tool_use_id": tool_use_id,
                          "deferred_id": job_id, "started_at": "t", "timeout_s": None},
    ))


def _job(job_id: str, status: str, kind: str = "run_nextflow", error: str | None = None) -> dict:
    return {"id": job_id, "status": status, "kind": kind, "error": error,
            "title": "test job", "params": {"thread_id": "thr_x"}}


def _tool_results_for(tool_use_id: str) -> list[dict]:
    out: list[dict] = []
    with _conn() as c:
        for r in c.execute("SELECT content FROM messages WHERE role='user'").fetchall():
            try:
                blocks = json.loads(r["content"])
            except (json.JSONDecodeError, TypeError):
                continue
            for b in blocks if isinstance(blocks, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") == tool_use_id:
                    c2 = b.get("content")
                    out.append(json.loads(c2) if isinstance(c2, str) else c2)
    return out


def _state(run_id: str) -> str:
    return ck.load_turn(run_id).state.value


def test_done_resolves_and_transitions():
    _park("run_d", "job_d", "tu_d")
    assert ck.settle_deferred_job(_job("job_d", "done")) is True
    res = _tool_results_for("tu_d")
    assert len(res) == 1 and res[0]["status"] == "ok", res
    assert _state("run_d") == "done"
    assert ck.load_turn("run_d").pending_deferred is None


def test_cancelled_and_failed_content_and_state():
    _park("run_c", "job_c", "tu_c")
    assert ck.settle_deferred_job(_job("job_c", "cancelled")) is True
    assert _tool_results_for("tu_c")[0]["status"] == "cancelled"
    assert _state("run_c") == "failed"          # cancelled/failed → turn FAILED

    _park("run_f", "job_f", "tu_f")
    assert ck.settle_deferred_job(_job("job_f", "failed", error="boom line\nmore")) is True
    rf = _tool_results_for("tu_f")[0]
    assert rf["status"] == "error" and "boom line" in rf["error"]   # 'error' → frontend ✗
    assert _state("run_f") == "failed"


def test_idempotent_no_duplicate():
    _park("run_i", "job_i", "tu_i")
    assert ck.settle_deferred_job(_job("job_i", "done")) is True
    # second call finds no awaiting turn → no-op, no duplicate tool_result
    assert ck.settle_deferred_job(_job("job_i", "done")) is False
    assert len(_tool_results_for("tu_i")) == 1


def test_no_parked_turn_is_noop():
    assert ck.settle_deferred_job(_job("job_missing", "done")) is False


def test_kind_agnostic():
    # settle keys off deferred_id, not kind — run_python/run_r resolve the same way
    for kind, rid, jid, tid in (("run_python", "run_p", "job_p", "tu_p"),
                                ("run_r", "run_rr", "job_rr", "tu_rr")):
        _park(rid, jid, tid)
        assert ck.settle_deferred_job(_job(jid, "done", kind=kind)) is True
        assert _tool_results_for(tid)[0]["status"] == "ok"
        assert _state(rid) == "done"


def test_survives_restart_dbdriven():
    # No in-memory Turn object is held — settle finds the parked turn purely from the
    # runs table (what happens after a process restart: reap leaves awaiting turns intact).
    _park("run_x", "job_x", "tu_x")
    del_turn = ck.load_turn("run_x")            # prove it's persisted + awaiting
    assert del_turn.state == TurnState.AWAITING_TOOL_RESULT
    assert ck.settle_deferred_job(_job("job_x", "done")) is True
    assert _state("run_x") == "done"


def main() -> int:
    tests = [test_done_resolves_and_transitions, test_cancelled_and_failed_content_and_state,
             test_idempotent_no_duplicate, test_no_parked_turn_is_noop, test_kind_agnostic,
             test_survives_restart_dbdriven]
    failed = []
    for t in tests:
        try:
            t(); print(f"OK  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(t.__name__); print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"\n{'all ' + str(len(tests)) + ' passed' if not failed else str(len(failed)) + ' failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
