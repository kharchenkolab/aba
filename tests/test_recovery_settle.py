"""P3 — recovery-complete: dropped jobs settle on restart; cancelled jobs aren't resumed.

  1. reconcile_jobs() (startup sweep) marks orphaned local 'running' rows failed AND now
     settles their parked deferred turn — so a job that was running when ABA crashed doesn't
     leave its chat tool line spinning forever after the restart.
  2. _maybe_resume_nextflow_job refuses to resume a user-cancelled job (cancel_requested) —
     closing the cancel-vs-auto-resume race where a scancel that outraced the status write
     would otherwise be re-submitted on restart.

Run: .venv/bin/python tests/test_recovery_settle.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_recov_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()
from core.jobs import runner  # noqa: E402
from core.jobs.runner import reconcile_jobs, _maybe_resume_nextflow_job  # noqa: E402
from core.config import PROJECTS_DIR  # noqa: E402


def _make_project_db(pid: str, job_id: str, status: str, params: dict,
                     kind: str = "run_python") -> None:
    """p14-style: a project.db with one jobs row (reconcile is a filesystem scan of
    PROJECTS_DIR/*/project.db, independent of single/multi mode)."""
    import sqlite3
    pdir = PROJECTS_DIR / pid; pdir.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(pdir / "project.db")
    c.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, title TEXT, status TEXT, "
              "focus_entity_id TEXT, params TEXT, log_tail TEXT, error TEXT, "
              "created_at TEXT NOT NULL, started_at TEXT, finished_at TEXT)")
    c.execute("INSERT INTO jobs (id,kind,title,status,params,created_at,started_at) "
              "VALUES (?,?,?,?,?,?,?)",
              (job_id, kind, "dropped", status, json.dumps(params),
               "2026-07-01T00:00:00+00:00", "2026-07-01T00:00:01+00:00"))
    c.commit(); c.close()


def _job_params(pid: str, job_id: str) -> dict:
    import sqlite3
    c = sqlite3.connect(PROJECTS_DIR / pid / "project.db")
    row = c.execute("SELECT params FROM jobs WHERE id=?", (job_id,)).fetchone()
    c.close()
    return json.loads(row[0]) if row and row[0] else {}


def _job_status(pid: str, job_id: str) -> str:
    import sqlite3
    c = sqlite3.connect(PROJECTS_DIR / pid / "project.db")
    row = c.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    c.close()
    return row[0] if row else ""


def _spy_settle():
    """Replace runner._settle_job_deferred with a recorder; returns (calls, restore)."""
    calls: list[tuple[str, str | None]] = []
    orig = runner._settle_job_deferred
    runner._settle_job_deferred = lambda jid, pid: calls.append((jid, pid))
    return calls, (lambda: setattr(runner, "_settle_job_deferred", orig))


def test_reconcile_reaps_and_settles_dropped_job():
    # A local job that was 'running' when ABA died. On restart reconcile must reap it to
    # 'failed' AND settle its parked deferred turn (so the chat tool line doesn't spin).
    _make_project_db("prj_droptest", "job_drop", "running",
                     {"thread_id": "thr_d", "project_id": "prj_droptest", "code": "x"})
    settled, restore = _spy_settle()
    try:
        reconcile_jobs()
    finally:
        restore()
    assert _job_status("prj_droptest", "job_drop") == "failed", "orphaned running row reaped"
    assert ("job_drop", "prj_droptest") in settled, f"reaped drop must be settled: {settled}"


def test_reconcile_kills_orphaned_inline_process():
    # Full path: a running INLINE job whose head (a child of the dead prior instance) is still
    # alive. On restart reconcile must reap the row AND kill the leftover process, matched by
    # the run's scratch token (params.run_id) in its command line.
    import subprocess
    import time
    token = "ana_reconkill_" + os.urandom(5).hex()
    proc = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(120)  # {token}"],
                            start_new_session=True)
    _make_project_db("prj_orphankill", "job_orphan", "running",
                     {"project_id": "prj_orphankill", "run_id": token, "submission": "inline"})
    settled, restore = _spy_settle()
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.05)                              # let it appear in /proc
        stats = reconcile_jobs()
    finally:
        restore()
        if proc.poll() is None:
            proc.kill()
    assert _job_status("prj_orphankill", "job_orphan") == "failed", "orphaned inline row reaped"
    assert stats.get("killed_orphan_procs", 0) >= 1, f"orphan process must be killed: {stats}"
    for _ in range(60):
        if proc.poll() is not None:
            break
        time.sleep(0.05)
    assert proc.poll() is not None, "the leftover inline head process must be dead"


def test_reconcile_resumes_inline_pipeline():
    # A running INLINE nextflow head that was mid-run when ABA died/timed-out. Its run_id-keyed
    # work-dir on scratch is a `-resume` cache, so reconcile must RE-ENQUEUE it (queued, resume
    # counter bumped) rather than fail it — and must NOT settle its deferred turn (the job is
    # resuming, not terminal). This is the durability fix for the inline path.
    _make_project_db("prj_nfresume", "job_nf", "running",
                     {"project_id": "prj_nfresume", "run_id": "ana_res1", "submission": "inline",
                      "pipeline": "nf-core/rnaseq"}, kind="run_nextflow")
    settled, restore = _spy_settle()
    try:
        stats = reconcile_jobs()
    finally:
        restore()
    assert _job_status("prj_nfresume", "job_nf") == "queued", "inline pipeline re-enqueued, not failed"
    assert _job_params("prj_nfresume", "job_nf").get("nf_resumes") == 1, "resume counter bumped"
    assert ("job_nf", "prj_nfresume") not in settled, "a resuming job must NOT be settled"
    assert stats.get("resumed_inline", 0) >= 1, f"resumed_inline stat: {stats}"


def test_reconcile_fails_inline_pipeline_past_cap():
    # A pipeline that has already burned its resume budget is failed, not resurrected forever.
    _make_project_db("prj_nfcap", "job_cap", "running",
                     {"project_id": "prj_nfcap", "run_id": "ana_cap", "submission": "inline",
                      "pipeline": "nf-core/rnaseq", "nf_resumes": 3}, kind="run_nextflow")
    settled, restore = _spy_settle()
    try:
        reconcile_jobs()
    finally:
        restore()
    assert _job_status("prj_nfcap", "job_cap") == "failed", "over-cap pipeline reaped"
    assert ("job_cap", "prj_nfcap") in settled, "a reaped job is settled"


def test_reconcile_does_not_resume_cancelled_pipeline():
    # A user-cancelled inline pipeline must be reaped, never resumed (mirror of the slurm guard).
    _make_project_db("prj_nfcancel", "job_canc", "running",
                     {"project_id": "prj_nfcancel", "run_id": "ana_canc", "submission": "inline",
                      "pipeline": "nf-core/rnaseq", "cancel_requested": True}, kind="run_nextflow")
    reconcile_jobs()
    assert _job_status("prj_nfcancel", "job_canc") == "failed", "cancelled pipeline not resumed"


def test_reconcile_does_not_settle_slurm_or_terminal():
    # Slurm jobs are exempt from reaping (poll re-adopts them) → not settled here; and a
    # terminal row is left alone.
    _make_project_db("prj_slurmtest", "job_slurm", "running",
                     {"submitter": "slurm", "project_id": "prj_slurmtest"})
    _make_project_db("prj_donetest0", "job_done", "done", {"project_id": "prj_donetest0"})
    settled, restore = _spy_settle()
    try:
        reconcile_jobs()
    finally:
        restore()
    assert _job_status("prj_slurmtest", "job_slurm") == "running", "slurm job not reaped"
    assert settled == [], f"neither slurm nor terminal jobs should be settled: {settled}"


def test_cancelled_job_not_auto_resumed():
    class _StubSub:
        name = "slurm"
        def submit(self, job):
            raise AssertionError("must NOT resume a user-cancelled job")

    job = {"id": "job_x", "kind": "run_nextflow",
           "params": {"cancel_requested": True, "submitter": "slurm"}}
    result = {"slurm_terminal_fail": "CANCELLED"}
    assert _maybe_resume_nextflow_job(_StubSub(), job, result, "pid") is False


def test_non_cancelled_terminal_fail_still_eligible():
    # Sanity: without the cancel marker, a slurm_terminal_fail is still a resume candidate
    # (the guard is specific to cancel_requested). We stop before submit via the cap.
    job = {"id": "job_y", "kind": "run_nextflow",
           "params": {"submitter": "slurm", "nf_resumes": 99}}   # over cap → returns False w/o submit
    result = {"slurm_terminal_fail": "TIMEOUT", "error": "head died"}

    class _Sub:
        name = "slurm"
        def submit(self, job):
            raise AssertionError("over-cap should not submit")
    assert _maybe_resume_nextflow_job(_Sub(), job, result, "pid") is False
    assert "gave up after" in result.get("error", "")             # proves it reached the cap branch, not the cancel guard


def test_reap_orphan_processes_kills_by_token():
    # A prior-instance orphan is identified by its per-run scratch token (run_id) in the
    # process cmdline. Spawn a stand-in in its OWN session (like a reparented orphan), reap
    # by token, assert it dies. Guards the restart path that stops inline heads leaking.
    import subprocess
    import time
    from core.jobs.runner import _reap_orphan_processes
    token = "ana_reaptest_" + os.urandom(5).hex()
    p = subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(120)  # {token}"],
                         start_new_session=True)
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline and p.poll() is None:
            time.sleep(0.05)                          # let it appear in /proc
        assert _reap_orphan_processes([token]) >= 1
        for _ in range(60):
            if p.poll() is not None:
                break
            time.sleep(0.05)
        assert p.poll() is not None                   # the token match killed it
    finally:
        if p.poll() is None:
            p.kill()


def test_reap_orphan_processes_noop_on_empty_or_unmatched():
    from core.jobs.runner import _reap_orphan_processes
    assert _reap_orphan_processes([]) == 0
    assert _reap_orphan_processes([""]) == 0          # empty token ignored
    assert _reap_orphan_processes(["no_such_token_zzz_" + os.urandom(4).hex()]) == 0


def main() -> int:
    tests = [test_reconcile_reaps_and_settles_dropped_job,
             test_reconcile_kills_orphaned_inline_process,
             test_reconcile_resumes_inline_pipeline,
             test_reconcile_fails_inline_pipeline_past_cap,
             test_reconcile_does_not_resume_cancelled_pipeline,
             test_reconcile_does_not_settle_slurm_or_terminal,
             test_cancelled_job_not_auto_resumed,
             test_non_cancelled_terminal_fail_still_eligible,
             test_reap_orphan_processes_kills_by_token,
             test_reap_orphan_processes_noop_on_empty_or_unmatched]
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
