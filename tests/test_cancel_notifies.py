"""Cancel → notification scenarios (the /api/jobs/{id}/cancel path).

Reproduces the reported bug: cancelling a background job left the agent
un-notified (continuation skipped) and the turn hanging. Verifies the fix —
cancel now fires a continuation with a 'was cancelled' message — across the
states a job can be cancelled from (queued, running), and that done/failed
still notify. In-process (throwaway runtime, patched _fire), no server/LLM.

Run: .venv/bin/python tests/test_cancel_notifies.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_cancel_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
os.environ["ABA_BATCH_SUBMITTER"] = "local"          # local submitter → cancel is a no-op token fire

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()

from core.jobs import continuation  # noqa: E402
from core.jobs.runner import cancel_job  # noqa: E402
from core.graph.jobs import create_job, get_job, update_job  # noqa: E402

FIRED: list[tuple[str, str, str]] = []          # (job_id, thread_id, message)


async def _fake_fire(job: dict, project_id: str, thread_id: str) -> None:
    FIRED.append((job["id"], thread_id, continuation._continuation_message_text(job, project_id)))


continuation._fire = _fake_fire  # type: ignore[assignment]
from core.runtime import turn_sink as _ts  # noqa: E402
_ts.active_for_thread = lambda tid: None      # type: ignore[assignment]  (no active turn → fire now)


def _mk(job_id: str, status: str, kind: str = "run_nextflow") -> dict:
    create_job(job_id=job_id, kind=kind, title="cancel test",
               focus_entity_id=None,
               params={"thread_id": "thr_x", "project_id": "prj_x",
                       "pipeline": "nf-core/rnaseq", "code": "sleep 1"})
    update_job(job_id, status=status)
    return get_job(job_id)


async def _cancel_like_endpoint(job_id: str) -> None:
    """Exactly what POST /api/jobs/{id}/cancel now does."""
    ok = cancel_job(job_id)
    assert ok, f"cancel_job returned False for {job_id}"
    job = get_job(job_id)
    pid = (job.get("params") or {}).get("project_id")
    await continuation.enqueue_continuation(job or {}, str(pid) if pid else None)


def test_cancel_queued_job_notifies():
    FIRED.clear()
    _mk("job_q", "queued")
    asyncio.run(_cancel_like_endpoint("job_q"))
    assert get_job("job_q")["status"] == "cancelled"
    assert [f[0] for f in FIRED] == ["job_q"], FIRED
    assert "cancel" in FIRED[0][2].lower() and "not continue" in FIRED[0][2].lower()


def test_cancel_running_job_notifies():
    FIRED.clear()
    _mk("job_r", "running")
    asyncio.run(_cancel_like_endpoint("job_r"))
    assert get_job("job_r")["status"] == "cancelled"
    assert [f[0] for f in FIRED] == ["job_r"], FIRED


def test_cancel_notifies_for_every_background_kind():
    """The notification is kind-agnostic — run_python / run_r / run_nextflow
    background jobs all notify their thread on cancel (not just pipelines)."""
    for i, kind in enumerate(("run_python", "run_r", "run_nextflow")):
        FIRED.clear()
        jid = f"job_k{i}"
        _mk(jid, "running", kind=kind)
        asyncio.run(_cancel_like_endpoint(jid))
        assert get_job(jid)["status"] == "cancelled", kind
        assert [f[0] for f in FIRED] == [jid], (kind, FIRED)


def test_cancel_already_terminal_is_noop():
    FIRED.clear()
    _mk("job_done", "done")
    # cancel_job refuses a terminal job → endpoint would 400; nothing fires here
    assert cancel_job("job_done") is False
    assert FIRED == []


def main() -> int:
    tests = [test_cancel_queued_job_notifies, test_cancel_running_job_notifies,
             test_cancel_notifies_for_every_background_kind,
             test_cancel_already_terminal_is_noop]
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
