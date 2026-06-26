"""
Background job queue (Phase 17 + Phase A restart-survival).

A single async worker processes queued jobs one at a time. Each job is a
Python execution (the same sandbox path as run_python) that runs in a
cancellable subprocess. When a job finishes, its artifacts auto-register
with the focus context captured at submit time — so a figure produced by
a background pbmc3k run lands under the right analysis, exactly as if it
had run inline.

Single-process, sequential, in-memory worker — fine for the single-user
prototype. arq + Redis (per misc/agent_advise.md §12.6) is Phase B, when
multiuser launch is on deck.

**Phase A — restart survival (2026-06-04):**
- Jobs live in per-project DBs but the worker is global; _QUEUE carries
  (job_id, project_id) tuples so the worker doesn't depend on whichever
  project the HTTP layer happens to be serving at run time.
- start_worker() does a reconciliation pass at startup:
  - status='queued' rows across every project's DB are re-enqueued in
    global created_at order.
  - status='running' rows from a prior process are reaped to 'failed'
    with a clear note — they can't possibly still be running, the worker
    that owned them died with the process.
- _worker() updates _LAST_HEARTBEAT each iteration; /api/jobs/worker
  exposes a liveness probe.
- Exceptions inside _worker / _run_one are LOGGED (not silently
  swallowed) and propagate into the job's `error` column so a failed
  job no longer looks 'queued' forever.
"""
from __future__ import annotations
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from datetime import datetime, timezone

from core.config import DATA_DIR, ARTIFACTS_DIR
from core.graph.jobs import create_job, get_job, update_job
from core.hooks.dispatcher import dispatch
from core.jobs.submitter import get_submitter


_QUEUE: "asyncio.Queue[tuple[str, str | None]]" = asyncio.Queue()
_RUNNING: dict[str, subprocess.Popen] = {}
_CANCELLED: set[str] = set()
_WORKER_STARTED = False


class LocalSubmitter:
    """The default BatchSubmitter (ondemand.md P6): run the job in THIS process'
    async worker — today's behavior, exactly. submit() enqueues; the worker owns
    the lifecycle (so poll() returns None); cancel() fires the per-job CancelToken
    so the shared exec core killpg's the whole group."""
    name = "local"

    def submit(self, job: dict) -> None:
        pid = (job.get("params") or {}).get("project_id")
        _QUEUE.put_nowait((job["id"], pid))

    def cancel(self, job: dict) -> None:
        _CANCELLED.add(job["id"])
        from core.runtime import cancellation
        tok = cancellation.get(job["id"])
        if tok is not None:
            tok.cancel("user cancelled job")

    def poll(self, job: dict):
        return None  # the in-process worker drives status; nothing to poll

    def info(self, job: dict) -> dict:
        return {"submitter": "local"}

# Liveness / observability for /api/jobs/worker. Updated on every loop
# iteration of _worker(); a stale heartbeat means the worker hung or
# crashed (the latter shouldn't happen now — see _worker's outer try).
_LAST_HEARTBEAT: float = 0.0
_LAST_JOB_AT: float = 0.0          # when the worker last picked up a job
_RECENT_FAILURES: list[dict] = []  # last 10 worker-level failures (capped)
_RECENT_FAILURES_CAP = 10

# Emitted to anyone listening (the Queues view polls instead, but this is
# here for a future SSE channel).
_LISTENERS: list = []


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_slurm_params(params_text) -> bool:
    """True if a job row's params JSON marks it Slurm-submitted. Such jobs run
    independently on the cluster and survive an ABA restart, so reconcile must
    NOT reap (running) or re-enqueue (queued) them — the poll loop re-adopts
    them via the shared-FS sentinel."""
    if not params_text:
        return False
    try:
        return (json.loads(params_text) or {}).get("submitter") == "slurm"
    except Exception:  # noqa: BLE001
        return False


def _write_job_run_log(result_obj: dict, stdout: str, stderr: str,
                       job_id: str, project_id: str) -> None:
    """Persist combined stdout + stderr to <job_dir>/run.log so a later
    agent turn can read what the job actually did. The job_dir is the
    per-job scratch dir created by run_python_code (it lands a
    `script.py` and any artifacts there). We write next to those.

    Fix #2 (2026-06-08): without this, agents who needed to debug a
    silently-failing background job had to re-submit the job with
    explicit subprocess redirection — wasting a 10-minute round-trip."""
    from pathlib import Path as _P
    # run_python_code returns the working dir as `cwd` (or `workdir`).
    cwd = result_obj.get("cwd") or result_obj.get("workdir")
    # Fallback: per-project scratch convention used by the executor.
    if not cwd:
        try:
            from core.config import project_work_dir
            cwd = str(project_work_dir(project_id) / job_id)
        except Exception:
            return
    if not cwd:
        return
    log_path = _P(cwd) / "run.log"
    parts: list[str] = []
    if stdout:
        parts.append("=== STDOUT ===\n" + stdout.rstrip())
    if stderr:
        parts.append("=== STDERR ===\n" + stderr.rstrip())
    rc = result_obj.get("returncode")
    if rc is not None:
        parts.append(f"=== EXIT {rc} ===")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("\n\n".join(parts) + "\n")


async def _continue_after_failure(job_id: str, lookup_pid: str | None,
                                   effective_pid: str) -> None:
    """Fire the continuation hook for a job that just failed so the agent
    can decide whether to retry / debug / give up, instead of pretending
    everything's fine. The hook itself decides not to fire if the job had
    no thread or was cancelled."""
    try:
        from core.jobs.continuation import enqueue_continuation
        fresh = get_job(job_id, project_id=lookup_pid) or {}
        result = await enqueue_continuation(fresh, str(effective_pid))
        if result.get("state") != "skipped":
            print(f"[jobs.continuation] job={job_id} (failure) → {result}", flush=True)
    except Exception as e:  # noqa: BLE001
        _record_worker_failure("continuation-after-failure", job_id, e)


def _record_worker_failure(where: str, job_id: str | None, exc: BaseException) -> None:
    """Capture a worker-level failure for /api/jobs/worker. NOT for routine
    job failures (those land in the job's `error` column). This is for the
    pathological case: the worker itself threw before/after it could write
    the job row."""
    rec = {
        "ts": _utcnow(),
        "where": where,
        "job_id": job_id,
        "error": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc()[-2000:],
    }
    _RECENT_FAILURES.append(rec)
    if len(_RECENT_FAILURES) > _RECENT_FAILURES_CAP:
        del _RECENT_FAILURES[:-_RECENT_FAILURES_CAP]
    # Always print so it shows up in uvicorn.log too — invisibility was the
    # core bug of the previous `except Exception: pass`.
    print(f"[jobs.worker] {where} error (job={job_id}): {rec['error']}",
          file=sys.stderr, flush=True)
    print(rec["traceback"], file=sys.stderr, flush=True)


def submit_python_job(code: str, title: str, focus_entity_id: str | None,
                      timeout_s: int = 300, project_id: str | None = None,
                      thread_id: str | None = None, run_id: str | None = None,
                      estimate: dict | None = None, env: str | None = None) -> dict:
    """Create a queued job and enqueue it. Returns the job record. `project_id`
    is captured at submit time so the job runs in the right project's scratch
    workspace even if the active project changes before the worker picks it up.
    `thread_id` + `run_id` (the active Run at submit time) are captured so the
    job's outputs attach to the originating Run/thread instead of orphaning."""
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job(
        job_id=job_id,
        kind="run_python",
        title=title or "Background analysis",
        focus_entity_id=focus_entity_id,
        params={"code": code, "timeout_s": timeout_s, "project_id": project_id,
                "thread_id": thread_id, "run_id": run_id, "estimate": estimate or {},
                "env": env},
        project_id=project_id,
    )
    get_submitter().submit(job)
    return job


def submit_r_job(code: str, title: str, focus_entity_id: str | None,
                 timeout_s: int = 600, project_id: str | None = None,
                 thread_id: str | None = None, run_id: str | None = None,
                 estimate: dict | None = None, env: str | None = None) -> dict:
    """Create a queued R job. Mirrors submit_python_job but with kind='run_r';
    the worker dispatches to run_r_code in core.exec.run, which invokes Rscript
    against the project's tools-env R + project library, captures stdout/stderr,
    and harvests artifacts. Used by run_r(background=True) — the proper path
    for long Seurat/DESeq2/etc. work that would otherwise force the agent to
    shell out via run_python(subprocess.run([\"Rscript\", ...]))."""
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job(
        job_id=job_id,
        kind="run_r",
        title=title or "Background R analysis",
        focus_entity_id=focus_entity_id,
        params={"code": code, "timeout_s": timeout_s, "project_id": project_id,
                "thread_id": thread_id, "run_id": run_id, "estimate": estimate or {},
                "env": env},
        project_id=project_id,
    )
    get_submitter().submit(job)
    return job


def cancel_job(job_id: str, project_id: str | None = None) -> bool:
    """Cancel a queued or running job. Returns True if it was actionable. Fires
    the job's CancelToken so the shared exec core killpg's the whole process
    group (forked children die too), matching the synchronous Stop path."""
    job = get_job(job_id, project_id=project_id)
    if not job:
        return False
    if job["status"] in ("done", "failed", "cancelled"):
        return False
    # The active submitter stops the actual execution — CancelToken+killpg
    # locally, `scancel <id>` on Slurm.
    get_submitter().cancel(job)
    update_job(job_id, project_id=project_id, status="cancelled", finished_at=_utcnow())
    return True


async def _finalize_job(job: dict, result_obj: dict, lookup_pid: str | None,
                        effective_pid: str) -> None:
    """Shared completion path for a finished background job — used by BOTH the
    in-process worker (_run_one) and the Slurm poll loop. Maps the result to a
    status, persists the run log, registers artifacts (on_job_complete hook), and
    fires the continuation so the agent's plan resumes. ``lookup_pid`` is the DB
    project (where the row lives); ``effective_pid`` is where the work ran."""
    job_id = job["id"]
    params = job.get("params") or {}
    code = params.get("code", "")
    kind = job.get("kind") or "run_python"
    focus_entity_id = job.get("focus_entity_id")

    if job_id in _CANCELLED or result_obj.get("status") == "cancelled":
        update_job(job_id, project_id=lookup_pid, status="cancelled", finished_at=_utcnow())
        return
    if "error" in result_obj:
        update_job(job_id, project_id=lookup_pid, status="failed",
                   error=result_obj["error"][:1000],
                   log_tail=result_obj["error"][:1500], finished_at=_utcnow())
        await _continue_after_failure(job_id, lookup_pid, effective_pid)
        return
    stdout = result_obj.get("stdout", "")
    stderr = result_obj.get("stderr", "")
    log_tail = (stdout[-1500:] + ("\n" + stderr[-500:] if stderr else "")).strip()
    try:
        _write_job_run_log(result_obj, stdout, stderr, job_id, effective_pid)
    except Exception:  # noqa: BLE001
        pass
    if result_obj.get("returncode") != 0:
        update_job(job_id, project_id=lookup_pid, status="failed",
                   error=stderr[-1000:] or f"exit code {result_obj.get('returncode')}",
                   log_tail=log_tail, finished_at=_utcnow())
        await _continue_after_failure(job_id, lookup_pid, effective_pid)
        return
    dispatch("on_job_complete", {
        "tool_name": kind,
        "tool_input": {"code": code},
        "result_obj": result_obj,
        "focus_entity_id": focus_entity_id,
        "analysis_ctx": {"analysis_id": params.get("run_id"), "turn_index": 0},
        "thread_id": params.get("thread_id"),
        "project_id": effective_pid,
        "job_id": job_id,
        "new_entities": [],
    })
    update_job(job_id, project_id=lookup_pid, status="done", log_tail=log_tail,
               finished_at=_utcnow())
    try:
        from core.jobs.continuation import enqueue_continuation
        fresh = get_job(job_id, project_id=lookup_pid) or {}
        result = await enqueue_continuation(fresh, str(effective_pid))
        if result.get("state") != "skipped":
            print(f"[jobs.continuation] job={job_id} → {result}", flush=True)
    except Exception as e:  # noqa: BLE001
        _record_worker_failure("continuation", job_id, e)


async def _run_one(job_id: str, project_id: str | None = None) -> None:
    job = get_job(job_id, project_id=project_id)
    if not job:
        # Job row vanished between enqueue and run (only really possible if a
        # test wiped the DB out from under us). Skip silently — no row to
        # update, nothing to report.
        return
    if job["status"] == "cancelled" or job_id in _CANCELLED:
        return

    params = job["params"] or {}
    code = params.get("code", "")
    timeout_s = max(5, min(int(params.get("timeout_s") or 300), 1800))
    # Prefer the project_id we dequeued; fall back to params (legacy rows).
    effective_pid = project_id or params.get("project_id") or "default"
    focus_entity_id = job["focus_entity_id"]

    update_job(job_id, project_id=project_id, status="running", started_at=_utcnow())

    # P5: run through the SAME execution core as the synchronous run_python, so
    # the background job sees the project scratch workspace, the pylib overlay,
    # the conda tools env on PATH, and killpg cancellation. A per-job CancelToken
    # (keyed by job_id) lets cancel_job kill the whole process group.
    # B2 (2026-06-08): branch on job["kind"] so R jobs route through run_r_code
    # (script.R + Rscript) instead of the Python path.
    from core.exec.run import run_python_code, run_r_code
    from core.runtime import cancellation
    token = cancellation.acquire(job_id)
    kind = job.get("kind") or "run_python"
    try:
        loop = asyncio.get_event_loop()
        if kind == "run_r":
            result_obj = await loop.run_in_executor(
                None,
                lambda: run_r_code(code, project_id=str(effective_pid), run_id=job_id,
                                   timeout_s=timeout_s, cancel_token=token,
                                   env=params.get("env")),
            )
        else:
            result_obj = await loop.run_in_executor(
                None,
                lambda: run_python_code(code, project_id=str(effective_pid), run_id=job_id,
                                        timeout_s=timeout_s, cancel_token=token,
                                        env=params.get("env")),
            )

        await _finalize_job(job, result_obj, project_id, str(effective_pid))
    except Exception as e:  # noqa: BLE001
        # Surface the failure into the job row + the worker-failure log,
        # instead of silently swallowing it.
        try:
            update_job(job_id, project_id=project_id, status="failed",
                       error=f"worker exception: {type(e).__name__}: {e}"[:1000],
                       finished_at=_utcnow())
        except Exception:  # noqa: BLE001
            pass
        _record_worker_failure("_run_one", job_id, e)
    finally:
        cancellation.release(job_id)


async def _worker() -> None:
    """Main worker loop. Updates _LAST_HEARTBEAT each iteration so a stale
    timestamp is a hang signal. Outer try ensures one bad job can't kill
    the worker — failures are logged, the loop continues."""
    global _LAST_HEARTBEAT, _LAST_JOB_AT
    while True:
        _LAST_HEARTBEAT = time.time()
        try:
            # Wait at most 5s so the heartbeat keeps fresh even when the
            # queue is idle (without this, an idle worker looks dead).
            try:
                item = await asyncio.wait_for(_QUEUE.get(), timeout=5.0)
            except asyncio.TimeoutError:
                continue
            # Backwards-compat: a bare job_id string from some legacy caller
            # is still accepted (treated as current-project).
            if isinstance(item, tuple):
                job_id, project_id = item
            else:
                job_id, project_id = item, None
            _LAST_JOB_AT = time.time()
            try:
                await _run_one(job_id, project_id)
            except Exception as e:  # noqa: BLE001
                _record_worker_failure("_worker.dispatch", job_id, e)
            finally:
                _QUEUE.task_done()
        except Exception as e:  # noqa: BLE001
            # asyncio.Queue.get() shouldn't raise other than CancelledError; if
            # something pathological happens up here, log + back off briefly so
            # we don't tight-loop a broken queue.
            _record_worker_failure("_worker", None, e)
            await asyncio.sleep(1.0)


def reconcile_jobs() -> dict:
    """Restart-survival sweep. Run ONCE at startup, BEFORE _worker() begins
    pulling from _QUEUE.

    For every project DB:
      - status='running' rows are zombies (the prior worker that owned them
        died with the process). Mark them 'failed' with a clear note. The
        on_job_complete hook is NOT fired — there are no real artifacts.
      - status='queued' rows are re-enqueued into the in-memory queue in
        global created_at order so the original FIFO is preserved.

    Returns a small stats dict for logging / tests. Safe to call multiple
    times (queued rows won't double-enqueue because the second pass finds
    the same rows and the in-memory queue is the SAME _QUEUE — but in
    practice we only call this once at startup)."""
    from core.config import PROJECTS_DIR
    import sqlite3

    reaped = 0
    requeued: list[tuple[str, str, str]] = []   # (created_at, job_id, project_id)
    reap_note = "backend restarted while job was running — orphaned"

    if not PROJECTS_DIR.exists():
        return {"reaped_running": 0, "requeued_queued": 0, "projects_scanned": 0}

    n_projects = 0
    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue
        db_file = proj_dir / "project.db"
        if not db_file.exists():
            continue
        # Project id is the dir name (matches the convention everywhere else).
        pid = proj_dir.name
        if pid.startswith("_"):
            # Skip _scratch / _workspace housekeeping DBs.
            continue
        try:
            c = sqlite3.connect(db_file); c.row_factory = sqlite3.Row
            # Tolerate DBs without a jobs table (very old / freshly-created
            # projects that never submitted a job).
            has = c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'"
            ).fetchone()
            if not has:
                c.close()
                continue
            n_projects += 1
            # 1. Reap orphan running rows — EXCEPT Slurm jobs, which keep running
            #    on the cluster across an ABA restart (the poll loop re-adopts them).
            now = _utcnow()
            running = c.execute("SELECT id, params FROM jobs WHERE status='running'").fetchall()
            reap_ids = [r["id"] for r in running if not _is_slurm_params(r["params"])]
            for jid in reap_ids:
                c.execute("UPDATE jobs SET status='failed', error=COALESCE(error,'') || ?, "
                          "finished_at=? WHERE id=?", (reap_note, now, jid))
            reaped += len(reap_ids)
            # 2. Collect queued rows for re-enqueue.
            # Re-enqueue queued rows to the LOCAL worker — but NOT Slurm jobs:
            # they were already sbatch'd, so re-enqueuing would double-run them
            # locally. The poll loop watches them instead.
            rows = c.execute(
                "SELECT id, created_at, params FROM jobs WHERE status='queued' ORDER BY created_at"
            ).fetchall()
            for r in rows:
                if _is_slurm_params(r["params"]):
                    continue
                requeued.append((r["created_at"], r["id"], pid))
            c.commit()
            c.close()
        except sqlite3.Error as e:
            _record_worker_failure("reconcile_jobs", None, e)

    # Global FIFO across all projects.
    requeued.sort(key=lambda t: t[0])
    for _, job_id, pid in requeued:
        _QUEUE.put_nowait((job_id, pid))

    stats = {
        "reaped_running": reaped,
        "requeued_queued": len(requeued),
        "projects_scanned": n_projects,
    }
    print(f"[jobs.reconcile] {stats}", flush=True)
    return stats


def worker_status() -> dict:
    """Snapshot for /api/jobs/worker. `alive` is True iff the heartbeat is
    fresh (< 15s old) AND the worker task exists. Stale heartbeat = hung /
    crashed worker."""
    now = time.time()
    age = now - _LAST_HEARTBEAT if _LAST_HEARTBEAT else None
    return {
        "alive": _WORKER_STARTED and age is not None and age < 15.0,
        "started": _WORKER_STARTED,
        "last_heartbeat_age_s": round(age, 2) if age is not None else None,
        "last_job_at_age_s": round(now - _LAST_JOB_AT, 2) if _LAST_JOB_AT else None,
        "queue_depth": _QUEUE.qsize(),
        "recent_failures": list(_RECENT_FAILURES),
    }


_SLURM_POLL_S = 8.0


def _active_slurm_jobs() -> list[dict]:
    """All queued/running jobs (across project DBs) that were submitted to Slurm —
    the rows the poll loop must watch. Each carries ``project_id`` (the DB it lives
    in) so _finalize_job updates the right project."""
    from core.config import PROJECTS_DIR
    from core.graph.jobs import _row_to_job
    import sqlite3
    out: list[dict] = []
    if not PROJECTS_DIR.exists():
        return out
    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        db_file = proj_dir / "project.db"
        if not proj_dir.is_dir() or not db_file.exists() or proj_dir.name.startswith("_"):
            continue
        try:
            c = sqlite3.connect(db_file)
            c.row_factory = sqlite3.Row
            if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                c.close()
                continue
            for r in c.execute("SELECT * FROM jobs WHERE status IN ('queued','running')").fetchall():
                job = _row_to_job(r)
                if (job.get("params") or {}).get("submitter") == "slurm":
                    job["project_id"] = proj_dir.name
                    out.append(job)
            c.close()
        except Exception:  # noqa: BLE001
            continue
    return out


async def _slurm_poll_loop() -> None:
    """Watch Slurm-submitted jobs for completion (the shared-FS ``done`` sentinel)
    and reflect RUNNING in the UI. Runs only when ABA_BATCH_SUBMITTER=slurm; the
    local _worker still handles any local jobs. Completion routes through the
    SHARED _finalize_job (artifacts + continuation), exactly like a local job."""
    from core.jobs.submitter import get_submitter, submitter_name
    if submitter_name() != "slurm":
        return
    sub = get_submitter()
    print("[jobs.slurm] poll loop started", flush=True)
    while True:
        try:
            for job in _active_slurm_jobs():
                pid = job.get("project_id")
                result = sub.poll(job)
                if result is not None:
                    await _finalize_job(job, result, pid, str(pid or "default"))
                elif job.get("status") == "queued":
                    # Flip to running once Slurm starts it (UI only; squeue is
                    # called only for not-yet-running rows, bounding the cost).
                    if (sub.info(job) or {}).get("state") == "RUNNING":
                        update_job(job["id"], project_id=pid, status="running",
                                   started_at=_utcnow())
        except Exception as e:  # noqa: BLE001
            _record_worker_failure("slurm-poll", None, e)
        await asyncio.sleep(_SLURM_POLL_S)


def start_worker() -> None:
    """Launch the worker task once, from FastAPI startup.

    Reconciles ZOMBIE jobs FIRST so the worker picks up any queued rows that
    survived a restart, and so users don't see 'running' rows lying about
    state from a dead process."""
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    try:
        reconcile_jobs()
    except Exception as e:  # noqa: BLE001
        # Reconciliation failing must not block the worker from starting —
        # log it and continue. The worker still serves NEW jobs even if the
        # zombie sweep had a problem.
        _record_worker_failure("reconcile_jobs", None, e)
    _WORKER_STARTED = True
    asyncio.get_event_loop().create_task(_worker())
    # On a Slurm deployment, also watch sbatch'd jobs for their completion
    # sentinel (the local worker only handles in-process jobs).
    from core.jobs.submitter import submitter_name
    if submitter_name() == "slurm":
        asyncio.get_event_loop().create_task(_slurm_poll_loop())
