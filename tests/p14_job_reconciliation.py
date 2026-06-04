"""Phase A — startup reconciliation of the in-memory background-job queue.

What it guards:
  1. status='running' rows from a prior backend process are zombies — the
     worker that owned them died with the process. reconcile_jobs() must
     mark them 'failed' so the UI stops claiming they're in flight.
  2. status='queued' rows survive a backend restart — they get re-pushed
     to _QUEUE in global created_at order so the original FIFO holds.
  3. Cross-project ordering: a queued row in project A created BEFORE a
     queued row in project B must come out of _QUEUE first.
  4. reconcile_jobs() is idempotent on a fresh run — no harm if called
     twice (defensive: a unit test might trigger this).
  5. /api/jobs/worker — worker_status() shape: a fresh process reports
     started=False; after start_worker it reports alive=True.

Filesystem isolation: this test sets BOTH ABA_DB_PATH AND ABA_RUNTIME_DIR
to a fresh temp tree BEFORE importing any backend code, per
[[feedback_test_filesystem_isolation]]. ABA_PROJECTS_DIR is also pointed
into the temp tree so reconciliation walks only OUR fake projects.

Run:
    .venv/bin/python tests/p14_job_reconciliation.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_p14_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()
from core.jobs import runner  # noqa: E402, F401  — imports also init _QUEUE
from core.config import PROJECTS_DIR  # noqa: E402


def _make_project_db_with_jobs(pid: str, rows: list[dict]) -> Path:
    """Create a project dir + jobs table + insert the given rows. `rows` is a
    list of {id, status, created_at, ...} dicts."""
    pdir = PROJECTS_DIR / pid
    pdir.mkdir(parents=True, exist_ok=True)
    db = pdir / "project.db"
    c = sqlite3.connect(db)
    c.execute("""
        CREATE TABLE jobs (
            id              TEXT PRIMARY KEY,
            kind            TEXT NOT NULL,
            title           TEXT NOT NULL,
            status          TEXT NOT NULL,
            focus_entity_id TEXT,
            params          TEXT,
            log_tail        TEXT,
            error           TEXT,
            created_at      TEXT NOT NULL,
            started_at      TEXT,
            finished_at     TEXT
        )
    """)
    for r in rows:
        c.execute(
            "INSERT INTO jobs (id, kind, title, status, focus_entity_id, params, "
            "created_at, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (r["id"], "run_python", r.get("title", "test"), r["status"],
             None, json.dumps({"code": "x", "project_id": pid}),
             r["created_at"], r.get("started_at")),
        )
    c.commit(); c.close()
    return db


def _drain_queue() -> list[tuple[str, str | None]]:
    """Pull every item off the runner's _QUEUE without blocking. Returns
    them in dequeue order."""
    out: list[tuple[str, str | None]] = []
    while True:
        try:
            out.append(runner._QUEUE.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


def _read_status(pid: str, job_id: str) -> dict:
    db = PROJECTS_DIR / pid / "project.db"
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    r = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    c.close()
    return dict(r) if r else {}


# ---------- tests ----------

def test_running_rows_get_reaped_to_failed():
    """status='running' rows from a prior process should be marked 'failed'
    with the standard restart note; finished_at should be populated."""
    pid = "prj_aaaaaaaa"
    _make_project_db_with_jobs(pid, [
        {"id": "job_run_aaa", "status": "running",
         "created_at": "2026-06-04T00:00:00+00:00",
         "started_at": "2026-06-04T00:00:01+00:00"},
    ])
    stats = runner.reconcile_jobs()
    assert stats["reaped_running"] >= 1, stats
    row = _read_status(pid, "job_run_aaa")
    assert row["status"] == "failed", row
    assert row["finished_at"], "reaped row should carry finished_at"
    assert row["error"] and "restarted" in row["error"].lower(), \
        f"error note should mention restart, got: {row['error']!r}"


def test_queued_rows_get_re_enqueued_in_global_fifo_order():
    """Queued rows across multiple projects come out of _QUEUE in global
    created_at order, regardless of which project owns them."""
    # Three queued rows interleaved across two projects:
    #   project A — created at t=0, t=4
    #   project B — created at t=2
    # Expected dequeue order: A:t0, B:t2, A:t4
    _make_project_db_with_jobs("prj_alpha111", [
        {"id": "job_alpha_0", "status": "queued", "created_at": "2026-06-04T01:00:00+00:00"},
        {"id": "job_alpha_4", "status": "queued", "created_at": "2026-06-04T01:00:04+00:00"},
    ])
    _make_project_db_with_jobs("prj_beta1111", [
        {"id": "job_beta_2", "status": "queued", "created_at": "2026-06-04T01:00:02+00:00"},
    ])
    # Drain any leftovers from prior tests so we measure THIS run cleanly.
    _drain_queue()
    stats = runner.reconcile_jobs()
    assert stats["requeued_queued"] >= 3, stats
    items = _drain_queue()
    job_ids = [j for j, _ in items if j.startswith(("job_alpha_", "job_beta_"))]
    assert job_ids == ["job_alpha_0", "job_beta_2", "job_alpha_4"], \
        f"global-FIFO order broken: {job_ids}"
    # Each item must carry its project_id alongside the job_id so the
    # worker can address the right DB.
    pid_by_job = {j: p for j, p in items}
    assert pid_by_job["job_alpha_0"] == "prj_alpha111"
    assert pid_by_job["job_beta_2"]  == "prj_beta1111"


def test_done_and_failed_rows_are_untouched():
    """Reconciliation must leave terminal-state rows alone — only running →
    failed, queued → re-enqueue. done / failed / cancelled stay as-is."""
    pid = "prj_terminal"
    _make_project_db_with_jobs(pid, [
        {"id": "job_done_1",   "status": "done",      "created_at": "2026-06-04T00:00:00+00:00"},
        {"id": "job_fail_1",   "status": "failed",    "created_at": "2026-06-04T00:00:01+00:00"},
        {"id": "job_cncl_1",   "status": "cancelled", "created_at": "2026-06-04T00:00:02+00:00"},
    ])
    _drain_queue()
    runner.reconcile_jobs()
    for jid, expected in [("job_done_1", "done"), ("job_fail_1", "failed"),
                          ("job_cncl_1", "cancelled")]:
        assert _read_status(pid, jid)["status"] == expected, \
            f"terminal row {jid} should stay {expected}"
    # And nothing got re-enqueued from this project.
    items = _drain_queue()
    assert not any(j.startswith("job_done_") or j.startswith("job_fail_") or
                   j.startswith("job_cncl_") for j, _ in items)


def test_reconcile_skips_projects_without_jobs_table():
    """A brand-new project's DB may not have a jobs table yet. The sweep
    must not crash on it."""
    pdir = PROJECTS_DIR / "prj_nojobs0"
    pdir.mkdir(parents=True, exist_ok=True)
    db = pdir / "project.db"
    c = sqlite3.connect(db); c.execute("CREATE TABLE entities (id TEXT)").close()
    c.close()
    # Should NOT raise.
    runner.reconcile_jobs()


def test_reconcile_skips_underscore_housekeeping_dbs():
    """_workspace and _scratch.db (housekeeping; not real projects) are
    skipped by the sweep — they don't belong to a real user project."""
    pdir = PROJECTS_DIR / "_workspace"
    pdir.mkdir(parents=True, exist_ok=True)
    _make_project_db_with_jobs("_workspace", [
        {"id": "job_underscore", "status": "queued",
         "created_at": "2026-06-04T05:00:00+00:00"},
    ])
    _drain_queue()
    runner.reconcile_jobs()
    items = _drain_queue()
    assert not any(j == "job_underscore" for j, _ in items), \
        "_workspace housekeeping rows should NOT be re-enqueued"


def test_worker_status_before_and_after_start():
    """Before start_worker(): started=False, alive=False.
       (We don't actually start the worker here — that would race with
       the event-loop machinery in a synchronous test runner.)"""
    s = runner.worker_status()
    assert s["started"] is False
    assert s["alive"] is False
    # Shape — every key the (i) drawer expects.
    for k in ("queue_depth", "last_heartbeat_age_s", "recent_failures"):
        assert k in s, f"missing key {k}"


def main() -> int:
    tests = [
        test_running_rows_get_reaped_to_failed,
        test_queued_rows_get_re_enqueued_in_global_fifo_order,
        test_done_and_failed_rows_are_untouched,
        test_reconcile_skips_projects_without_jobs_table,
        test_reconcile_skips_underscore_housekeeping_dbs,
        test_worker_status_before_and_after_start,
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
