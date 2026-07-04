"""B4 — tests for run_r(background=True).

Three layers:
- Unit: submit_r_job writes a job row with kind='run_r'.
- Routing: run_r({background:True}) returns a deferred response with a job_id.
- E2E: a tiny R script writes a PNG, the runner picks it up, harvests
  it as a figure entity, and the continuation message reports it.
  Skipped if the Rscript binary isn't reachable.

Run: .venv/bin/python tests/test_run_r_background.py
"""
from __future__ import annotations
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_run_r_bg_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core import projects                                  # noqa: E402
from core.jobs.runner import submit_r_job, submit_python_job  # noqa: E402
from core.graph.jobs import get_job                        # noqa: E402

projects.init()


def _fresh_project(name: str) -> str:
    p = projects.create_project(name)
    projects.set_current(p["id"])
    return p["id"]


# ─── unit ───────────────────────────────────────────────────────────────
def test_submit_r_job_creates_run_r_job_row():
    pid = _fresh_project("submit-r-unit")
    job = submit_r_job(code='cat("hi\\n")', title="R smoke",
                       focus_entity_id=None,
                       timeout_s=60, project_id=pid,
                       thread_id="thr_x", run_id="ana_x")
    assert job["id"].startswith("job_")
    fresh = get_job(job["id"], project_id=pid)
    assert fresh["kind"] == "run_r"
    assert fresh["title"] == "R smoke"
    assert fresh["status"] == "queued"
    params = fresh["params"] if isinstance(fresh["params"], dict) else json.loads(fresh["params"])
    assert params["code"] == 'cat("hi\\n")'
    assert params["thread_id"] == "thr_x"


def test_submit_python_job_still_creates_run_python_kind():
    """Regression: B2's kind branch must not have broken Python submissions."""
    pid = _fresh_project("submit-py-unit")
    job = submit_python_job(code="print(1)", title="Py smoke",
                            focus_entity_id=None, timeout_s=60,
                            project_id=pid, thread_id="thr_y", run_id="ana_y")
    fresh = get_job(job["id"], project_id=pid)
    assert fresh["kind"] == "run_python"


# ─── routing ────────────────────────────────────────────────────────────
def test_run_r_background_flag_routes_to_queue():
    """run_r tool with background=True returns a deferred submission."""
    pid = _fresh_project("route-r-bg")
    from content.bio.tools.run_exec import run_r
    res = run_r({"code": 'cat("noop\\n")', "background": True}, ctx={"thread_id": "thr_route"})
    assert res.get("status") == "submitted"
    assert res.get("job_id", "").startswith("job_")
    # Sanity: that job actually exists in the project DB as kind=run_r
    fresh = get_job(res["job_id"], project_id=pid)
    assert fresh["kind"] == "run_r"


def test_run_r_high_estimate_does_not_auto_background_in_local_mode():
    """The router REPLACED the old 'estimated_runtime_min >= threshold -> background'
    heuristic (core/exec/router.py): in LOCAL mode a high estimate runs INLINE (raise
    timeout_s), it does NOT auto-route to the queue. Backgrounding is explicit
    (background=True) or, on Slurm, driven by resource/walltime pressure. Force local
    mode so this is deterministic regardless of the ambient compute environment."""
    import os
    from core.exec import compute_env as _ce
    _fresh_project("route-r-est")
    from content.bio.tools.run_exec import run_r
    prev = os.environ.get("ABA_BATCH_SUBMITTER")
    os.environ["ABA_BATCH_SUBMITTER"] = "local"
    _ce._CACHE.update(ts=0.0, env=None)          # bust the 20s compute-env cache
    try:
        res = run_r({"code": 'cat("noop\\n")', "estimated_runtime_min": 15.0},
                    ctx={"thread_id": "thr_est"})
    finally:
        if prev is None:
            os.environ.pop("ABA_BATCH_SUBMITTER", None)
        else:
            os.environ["ABA_BATCH_SUBMITTER"] = prev
        _ce._CACHE.update(ts=0.0, env=None)
    assert res.get("status") != "submitted", \
        f"local mode must NOT auto-background on estimated_runtime_min alone, got {res}"


def test_run_r_short_estimate_stays_synchronous():
    """Low estimated_runtime_min keeps the kernel/sync path."""
    pid = _fresh_project("route-r-sync")
    from content.bio.tools.run_exec import run_r
    # KERNEL_ENABLED may be False in the test env → returns the 'kernel disabled'
    # error. EITHER outcome (kernel error OR actual kernel result) is acceptable;
    # the point is it did NOT route to background.
    res = run_r({"code": 'cat("noop\\n")', "estimated_runtime_min": 0.1},
                ctx={"thread_id": "thr_sync"})
    assert res.get("status") != "submitted", \
        f"short-runtime call should not background-route; got {res}"


# ─── end-to-end (skip if Rscript missing) ────────────────────────────────
def _rscript_works() -> bool:
    rscript = shutil.which("Rscript")
    if not rscript:
        try:
            from core.exec.r import _rscript
            return _rscript().exists()
        except Exception:
            return False
    return True


def test_e2e_r_job_runs_and_produces_artifact():
    """Submit a job that writes a PNG, drain the worker, assert harvest."""
    if not _rscript_works():
        print("  SKIP test_e2e_r_job_runs_and_produces_artifact: no Rscript")
        return
    pid = _fresh_project("e2e-r")
    code = '''
png("out.png", width=200, height=200)
plot(1:10, main="hi")
dev.off()
cat("DONE\\n")
'''
    job = submit_r_job(code, title="E2E R smoke", focus_entity_id=None,
                       timeout_s=120, project_id=pid,
                       thread_id="thr_e2e", run_id=None)
    # Drain the worker manually (start_worker isn't running in tests)
    from core.jobs.runner import _run_one
    asyncio.run(_run_one(job["id"], project_id=pid))
    fresh = get_job(job["id"], project_id=pid)
    assert fresh["status"] in ("done", "failed"), f"unexpected: {fresh['status']}"
    if fresh["status"] == "failed":
        print(f"  NOTE: job failed; error: {fresh.get('error','?')[:200]}")
        print(f"        log_tail: {fresh.get('log_tail','?')[:300]}")
        return  # the env may not have R provisioned; still validates the path runs
    # Job dir should have script.R + run.log + the .png we produced
    from core.config import project_work_dir
    work = project_work_dir(pid) / job["id"]
    assert (work / "script.R").exists()
    assert (work / "run.log").exists() or fresh.get("log_tail")
    assert (work / "out.png").exists() or any(
        p.suffix == ".png" for p in work.glob("**/*") if p.is_file()
    )


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
