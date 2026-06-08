"""J-1/J-2 — get_job_status + cancel_job MCP tools.

Four layers:
- Unit: _elapsed_s + _resolve_default_job_id helpers.
- get_job_status: returns proper shape on a real job row + live log tail.
- get_job_status default: defaults to the thread's most-recent job.
- cancel_job: marks the job cancelled, refuses double-cancel.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_jobs_tools_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core import projects                                # noqa: E402
from core.jobs.runner import submit_r_job, submit_python_job  # noqa: E402
from core.graph.jobs import get_job, update_job          # noqa: E402
from content.bio.mcp_servers.aba_core.tools.jobs import (  # noqa: E402
    _elapsed_s, _resolve_default_job_id, _read_run_log_tail,
)

projects.init()


def _fresh_project(name: str) -> str:
    p = projects.create_project(name)
    projects.set_current(p["id"])
    return p["id"]


# ─── unit helpers ──────────────────────────────────────────────────────
def test_elapsed_s_unstarted_returns_none():
    assert _elapsed_s({"started_at": None, "finished_at": None}) is None


def test_elapsed_s_running_uses_now():
    """Job started but not finished — elapsed counts up from started_at."""
    job = {"started_at": "2026-06-08T00:00:00+00:00", "finished_at": None}
    # We can't pin "now" without monkeypatch; just check it's > 0 + nonzero.
    e = _elapsed_s(job)
    assert e is not None and e > 0


def test_elapsed_s_finished_uses_finish_time():
    job = {"started_at": "2026-06-08T00:00:00+00:00",
           "finished_at": "2026-06-08T00:00:42+00:00"}
    assert _elapsed_s(job) == 42.0


def test_resolve_default_job_id_picks_thread_match():
    """When multiple jobs are queued, the resolver picks the most recent
    one for THIS thread (not just the most recent overall)."""
    pid = _fresh_project("default-job-pick")
    j_other = submit_r_job('cat("a\\n")', title="other-thread", focus_entity_id=None,
                           timeout_s=60, project_id=pid,
                           thread_id="thr_OTHER", run_id=None)
    time.sleep(0.05)
    j_mine = submit_r_job('cat("b\\n")', title="my-thread", focus_entity_id=None,
                          timeout_s=60, project_id=pid,
                          thread_id="thr_ME", run_id=None)
    resolved = _resolve_default_job_id({"thread_id": "thr_ME", "project_id": pid})
    assert resolved == j_mine["id"], f"expected {j_mine['id']}, got {resolved}"


def test_resolve_default_returns_none_for_unknown_thread():
    pid = _fresh_project("default-job-empty")
    submit_r_job('cat("x\\n")', title="t", focus_entity_id=None,
                 timeout_s=60, project_id=pid, thread_id="thr_X", run_id=None)
    assert _resolve_default_job_id({"thread_id": "thr_OTHER", "project_id": pid}) is None


# ─── get_job_status integration ────────────────────────────────────────
def _call_tool(tool_name: str, **kwargs):
    """Build a fresh aba_core server, invoke a registered tool by walking
    the FastMCP registry. Avoids the gateway/transport for unit testing."""
    from content.bio.mcp_servers.aba_core.server import make_server
    mcp = make_server()
    # FastMCP exposes tools via _tool_manager._tools (private but stable).
    tools = mcp._tool_manager._tools  # noqa: SLF001
    t = tools.get(tool_name)
    assert t is not None, f"tool {tool_name!r} not registered"
    return t.fn(**kwargs)


def test_get_job_status_shape_on_real_job():
    pid = _fresh_project("status-shape")
    job = submit_r_job('cat("noop\\n")', title="Shape test", focus_entity_id=None,
                       timeout_s=60, project_id=pid,
                       thread_id="thr_shape", run_id=None)
    # No ctx → no project_id → unable to look up. We bypass MCP and call
    # the underlying impl with a stashed ctx.
    from core.runtime.tool_ctx import stash_ctx, pop_ctx
    cid = stash_ctx({"thread_id": "thr_shape", "project_id": pid})
    try:
        out = _call_tool("get_job_status", job_id=job["id"], aba_ctx_id=cid)
    finally:
        pop_ctx(cid)
    assert out["id"] == job["id"]
    assert out["kind"] == "run_r"
    assert out["status"] in ("queued", "running", "done")
    assert "work_dir" in out and out["work_dir"].endswith(job["id"])
    # log_tail may be '' if the runner hasn't started yet — but the key
    # must exist (shape contract).
    assert "log_tail" in out
    assert "elapsed_s" in out


def test_get_job_status_defaults_to_thread_most_recent():
    pid = _fresh_project("status-default")
    submit_r_job('cat("a\\n")', title="A", focus_entity_id=None, timeout_s=60,
                 project_id=pid, thread_id="thr_def", run_id=None)
    time.sleep(0.05)
    j2 = submit_r_job('cat("b\\n")', title="B", focus_entity_id=None, timeout_s=60,
                      project_id=pid, thread_id="thr_def", run_id=None)
    from core.runtime.tool_ctx import stash_ctx, pop_ctx
    cid = stash_ctx({"thread_id": "thr_def", "project_id": pid})
    try:
        # No job_id → resolver should pick j2 (most recent)
        out = _call_tool("get_job_status", aba_ctx_id=cid)
    finally:
        pop_ctx(cid)
    assert out["id"] == j2["id"], f"expected {j2['id']}, got {out.get('id')}"


def test_get_job_status_unknown_job():
    pid = _fresh_project("status-unknown")
    from core.runtime.tool_ctx import stash_ctx, pop_ctx
    cid = stash_ctx({"thread_id": "thr_u", "project_id": pid})
    try:
        out = _call_tool("get_job_status", job_id="job_does_not_exist",
                         aba_ctx_id=cid)
    finally:
        pop_ctx(cid)
    assert "error" in out and "not found" in out["error"]


def test_get_job_status_no_thread_no_id():
    """No id and no thread to resolve from — clean error, not a crash."""
    pid = _fresh_project("status-bare")
    from core.runtime.tool_ctx import stash_ctx, pop_ctx
    cid = stash_ctx({"project_id": pid})  # no thread_id
    try:
        out = _call_tool("get_job_status", aba_ctx_id=cid)
    finally:
        pop_ctx(cid)
    assert "error" in out


# ─── log-tail liveness ─────────────────────────────────────────────────
def test_log_tail_reads_live_run_log():
    """Runner periodically flushes log_tail to the DB, but for a job
    that's actively running, the live file on disk has more — verify
    we prefer reading the file directly."""
    pid = _fresh_project("log-live")
    from core.config import project_work_dir
    job_id = "job_fake_live_99"
    wd = project_work_dir(pid) / job_id
    wd.mkdir(parents=True, exist_ok=True)
    # Write something to run.log that is NOT in any DB column
    (wd / "run.log").write_text("=== STDOUT ===\nstep 1\nstep 2\nstep 3\n")
    tail = _read_run_log_tail(pid, job_id)
    assert tail is not None
    assert "step 3" in tail


def test_log_tail_returns_none_when_no_log():
    pid = _fresh_project("log-missing")
    assert _read_run_log_tail(pid, "job_never_existed") is None


# ─── cancel_job ────────────────────────────────────────────────────────
def test_cancel_job_marks_cancelled():
    pid = _fresh_project("cancel-basic")
    job = submit_r_job('cat("noop\\n")', title="cancel me", focus_entity_id=None,
                       timeout_s=60, project_id=pid,
                       thread_id="thr_cancel", run_id=None)
    from core.runtime.tool_ctx import stash_ctx, pop_ctx
    cid = stash_ctx({"thread_id": "thr_cancel", "project_id": pid})
    try:
        out = _call_tool("cancel_job", job_id=job["id"], aba_ctx_id=cid)
    finally:
        pop_ctx(cid)
    assert out["ok"] is True
    assert out["prior_status"] == "queued"
    # The DB row should reflect the cancellation.
    fresh = get_job(job["id"], project_id=pid)
    assert fresh["status"] == "cancelled"


def test_cancel_job_refuses_terminal():
    pid = _fresh_project("cancel-terminal")
    job = submit_r_job('cat("noop\\n")', title="done", focus_entity_id=None,
                       timeout_s=60, project_id=pid,
                       thread_id="thr_t", run_id=None)
    # Manually mark done so we don't have to actually run it.
    update_job(job["id"], project_id=pid, status="done")
    from core.runtime.tool_ctx import stash_ctx, pop_ctx
    cid = stash_ctx({"thread_id": "thr_t", "project_id": pid})
    try:
        out = _call_tool("cancel_job", job_id=job["id"], aba_ctx_id=cid)
    finally:
        pop_ctx(cid)
    assert out["ok"] is False
    assert out["prior_status"] == "done"
    assert "already" in (out.get("error") or "")


def test_cancel_job_unknown_id():
    pid = _fresh_project("cancel-unknown")
    from core.runtime.tool_ctx import stash_ctx, pop_ctx
    cid = stash_ctx({"thread_id": "thr_u", "project_id": pid})
    try:
        out = _call_tool("cancel_job", job_id="job_does_not_exist",
                         aba_ctx_id=cid)
    finally:
        pop_ctx(cid)
    assert out["ok"] is False
    assert "not found" in (out.get("error") or "")


# ─── runner ─────────────────────────────────────────────────────────────────
TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
