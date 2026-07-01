"""Phase C — background-job auto-continuation (#296).

Tests the policy decisions, not the live Guide turn (that's an e2e
concern — see misc/scrna_test_findings.md). We patch out the actual
start_turn so the fire path runs to completion against a stub.

Edge cases the spec calls out:
  1. job with no thread_id → skipped (no plan to continue)
  2. job status='cancelled' → FIRES (acknowledge-and-stop message; the agent
     must not be left hanging on the deferred tool)
  3. job status='done' + idle thread → fired immediately
  4. job status='failed' + idle thread → fired (failure-aware message)
  5. job status='done' + thread streaming → deferred
  6. deferred case fires once the active turn closes (smoke-tests the wait loop)

Filesystem isolation: temp ABA_DB_PATH + ABA_RUNTIME_DIR + ABA_PROJECTS_DIR
per [[feedback_test_filesystem_isolation]].

Run:
    .venv/bin/python tests/p15_continuation.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_p15_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()

from core.jobs import continuation  # noqa: E402

# ----------------------------------------------------------------------
# Test-mode patching: _fire is the only thing we need to substitute.
# Tracks every (job_id, thread_id) it would have launched.
FIRED: list[tuple[str, str]] = []


async def _fake_fire(job: dict, project_id: str, thread_id: str) -> None:
    FIRED.append((job["id"], thread_id))


continuation._fire = _fake_fire  # type: ignore[assignment]

# Tighten the defer wait so the deferred-fires test runs quickly.
continuation.DEFER_TIMEOUT_S = 4
continuation.DEFER_POLL_INTERVAL_S = 0.1


# Stub a turn_sink.active_for_thread that we can flip during a test.
ACTIVE_THREADS: dict[str, "_FakeSink"] = {}


class _FakeSink:
    def __init__(self, closed: bool = False):
        self._closed = closed


def _set_active(thread_id: str, closed: bool = False) -> None:
    ACTIVE_THREADS[thread_id] = _FakeSink(closed=closed)


def _clear_active(thread_id: str) -> None:
    ACTIVE_THREADS.pop(thread_id, None)


def _patched_active_for_thread(tid: str):
    return ACTIVE_THREADS.get(tid)


from core.runtime import turn_sink as _ts  # noqa: E402
_ts.active_for_thread = _patched_active_for_thread  # type: ignore[assignment]


def _job(id="job_t1", status="done", thread_id="thr_main", title="Test job",
         err: str | None = None):
    return {
        "id": id, "title": title, "status": status,
        "focus_entity_id": "workspace", "error": err, "log_tail": "",
        "params": {"thread_id": thread_id, "project_id": "prj_test"},
    }


# ----------------------------------------------------------------------

def _reset():
    FIRED.clear()
    ACTIVE_THREADS.clear()


def test_skips_when_no_thread_id():
    _reset()
    j = _job(thread_id=None)
    out = asyncio.run(continuation.enqueue_continuation(j, "prj_test"))
    assert out["state"] == "skipped", out
    assert out["reason"] == "no thread_id", out
    assert FIRED == []


def test_skips_when_no_project_id():
    _reset()
    j = _job()
    out = asyncio.run(continuation.enqueue_continuation(j, None))
    assert out["state"] == "skipped", out
    assert out["reason"] == "no project_id", out
    assert FIRED == []


def test_fires_for_cancelled_job():
    """A user-cancelled background job now FIRES a continuation (a distinct
    'was cancelled — acknowledge and stop' message) so the originating turn
    isn't left hanging on the deferred tool. (Was previously skipped.)"""
    _reset()
    j = _job(status="cancelled", thread_id="thr_cancel")
    out = asyncio.run(continuation.enqueue_continuation(j, "prj_test"))
    assert out["state"] == "fired", out
    assert FIRED == [("job_t1", "thr_cancel")]


def test_skips_when_status_is_unexpected():
    _reset()
    # 'queued' shouldn't trigger continuation; the runner only invokes
    # enqueue_continuation post-resolution.
    j = _job(status="queued")
    out = asyncio.run(continuation.enqueue_continuation(j, "prj_test"))
    assert out["state"] == "skipped", out
    assert FIRED == []


def test_fires_immediately_when_idle():
    _reset()
    j = _job(status="done", thread_id="thr_idle")
    out = asyncio.run(continuation.enqueue_continuation(j, "prj_test"))
    assert out["state"] == "fired", out
    assert FIRED == [("job_t1", "thr_idle")]


def test_fires_for_failed_job_too():
    """A failed job still continues so the agent can react (retry/fix/give
    up) — not silently disappear."""
    _reset()
    j = _job(status="failed", thread_id="thr_failed", err="boom")
    out = asyncio.run(continuation.enqueue_continuation(j, "prj_test"))
    assert out["state"] == "fired", out
    assert FIRED == [("job_t1", "thr_failed")]


def test_defers_when_thread_is_streaming():
    """Active sink that's not yet closed → deferred (returns immediately;
    actual fire happens off the background task)."""
    _reset()
    _set_active("thr_busy", closed=False)
    j = _job(status="done", thread_id="thr_busy")
    out = asyncio.run(continuation.enqueue_continuation(j, "prj_test"))
    assert out["state"] == "deferred", out
    # Not fired yet — the defer task is in the background
    assert FIRED == []


def test_deferred_fires_once_thread_closes():
    """End-to-end of the wait loop: defer, then close the active sink, then
    verify _fire was called within DEFER_TIMEOUT_S."""
    _reset()
    _set_active("thr_wait", closed=False)

    async def _scenario():
        # Run enqueue + a small "user closed their turn" simulation in parallel
        await continuation.enqueue_continuation(_job(thread_id="thr_wait"),
                                                "prj_test")
        await asyncio.sleep(0.3)        # let the defer task settle
        _set_active("thr_wait", closed=True)
        # Wait long enough for the next poll iteration to detect closure
        await asyncio.sleep(0.6)

    asyncio.run(_scenario())
    assert FIRED == [("job_t1", "thr_wait")], FIRED


def test_continuation_message_distinguishes_success_vs_failure():
    """The synthetic text starts with the [continuation: …] prefix the
    frontend pattern-matches AND differentiates failure for the agent."""
    ok = continuation._continuation_message_text(_job(status="done"))
    fail = continuation._continuation_message_text(
        _job(status="failed", err="ImportError: no module foo"))
    assert ok.startswith("[continuation: "), ok[:60]
    assert fail.startswith("[continuation: "), fail[:60]
    assert "finished" in ok.lower()
    assert "fail" in fail.lower()
    assert "ImportError" in fail


def test_continuation_message_cancelled_tells_agent_to_stop():
    """A cancelled job's message must tell the agent NOT to continue the plan —
    just acknowledge + ask how to proceed."""
    msg = continuation._continuation_message_text(_job(status="cancelled"))
    assert msg.startswith("[continuation: "), msg[:60]
    assert "cancel" in msg.lower()
    assert "not continue" in msg.lower() or "do not" in msg.lower()


def test_continuation_message_nextflow_completion_is_pipeline_aware():
    """A SUCCEEDED Nextflow pipeline must not get the run_python 'no-op / no artifacts minted'
    message — its outputs harvest as files, not figure/table/cell entities. It should be told
    the pipeline completed and to interpret the QC."""
    job = {"id": "job_nf", "title": "Nextflow: nf-core/rnaseq", "status": "done",
           "kind": "run_nextflow", "focus_entity_id": "workspace", "error": None, "log_tail": "",
           "params": {"thread_id": "thr_x", "project_id": "prj_test",
                      "pipeline": "nf-core/rnaseq", "revision": "3.21.0", "run_id": "ana_x"}}
    msg = continuation._continuation_message_text(job, project_id="prj_test")
    assert "COMPLETED" in msg and "Interpret the results" in msg, msg[:200]
    assert "nf-core/rnaseq" in msg
    for bad in ("no-op", "were minted", "swallowed args", "wrong interpreter"):
        assert bad not in msg, f"leaked run_python framing: {bad!r}"


def main() -> int:
    tests = [
        test_skips_when_no_thread_id,
        test_continuation_message_nextflow_completion_is_pipeline_aware,
        test_skips_when_no_project_id,
        test_fires_for_cancelled_job,
        test_skips_when_status_is_unexpected,
        test_fires_immediately_when_idle,
        test_fires_for_failed_job_too,
        test_defers_when_thread_is_streaming,
        test_deferred_fires_once_thread_closes,
        test_continuation_message_distinguishes_success_vs_failure,
        test_continuation_message_cancelled_tells_agent_to_stop,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
    if failed:
        print(f"\n{len(failed)} / {len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
