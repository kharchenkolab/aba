"""Phase C — background-job → Guide turn auto-continuation.

Closes #296. When a background `run_python` job (e.g. scvi training)
finishes, the agent's turn that submitted it has already ended — so the
planned downstream steps (UMAP, clustering, etc.) never run. This
module re-enters the Guide loop on the originating thread with a synthetic
"the job is done — continue" message, so the plan completes itself.

Decided edge policy (per #296):
  * Always completes the requested work, even if the user moved on
    (deferred — fires when the thread next becomes idle).
  * QUEUES behind any actively-streaming turn on the same thread.
  * Labels as a continuation in the UI (frontend pattern-matches the
    "[continuation: …]" prefix on the synthetic user message — MVP for
    Phase C; a metadata-column approach is a Phase B follow-up).
  * Does NOT fire if the job was explicitly cancelled.
  * Does NOT fire if the originating turn had no thread_id (e.g. a
    workspace-level standalone job — no plan to continue).

Backend-agnostic: works the same whether the job ran via Phase A's local
worker, Phase B's arq worker, or Phase D's HPC poller. They all dispatch
on_job_complete; the runner calls into here right after that dispatch.
"""
from __future__ import annotations
import asyncio
import sys
from datetime import datetime, timezone


# How long to wait for the thread to go idle before giving up on the
# continuation. Bounded so a perpetually-active user-driven thread doesn't
# accumulate continuation tasks forever. 1 hour is generous — well past the
# typical "user reads + responds" cadence.
DEFER_TIMEOUT_S = 3600
DEFER_POLL_INTERVAL_S = 2.0


async def enqueue_continuation(job: dict, project_id: str | None) -> dict:
    """Called from the runner right after a job lands in done/failed/cancelled.

    Returns a small dict for logging / tests:
      {state: 'fired'|'deferred'|'skipped', reason: str}
    """
    if not project_id:
        return {"state": "skipped", "reason": "no project_id"}
    params = job.get("params") or {}
    thread_id = params.get("thread_id")
    if not thread_id:
        return {"state": "skipped", "reason": "no thread_id"}

    status = job.get("status")
    # Per spec — explicit cancellation kills the continuation.
    if status == "cancelled":
        return {"state": "skipped", "reason": "job cancelled"}
    # Only continue for jobs that actually produced something to continue
    # FROM. A failed job's continuation should give the agent the failure
    # context so it can decide (retry / fix / give up).  done + failed both
    # qualify; only cancelled is excluded above.
    if status not in ("done", "failed"):
        return {"state": "skipped", "reason": f"unexpected status {status!r}"}

    # Per spec — queue behind any actively-streaming turn on the same thread.
    from core.runtime import turn_sink as _ts
    active = _ts.active_for_thread(thread_id)
    if active is not None and not active._closed:
        # Defer: spawn a background task that waits for the thread to go
        # idle, then fires. _fire is awaitable; we don't block the runner.
        asyncio.create_task(
            _wait_then_fire(job, project_id, thread_id),
            name=f"continuation-defer:{job['id']}",
        )
        return {"state": "deferred", "reason": "thread streaming"}

    # No active turn — fire immediately.
    await _fire(job, project_id, thread_id)
    return {"state": "fired", "reason": "ok"}


async def _wait_then_fire(job: dict, project_id: str, thread_id: str) -> None:
    """Poll for the thread to become idle, then fire the continuation.
    Bounded by DEFER_TIMEOUT_S so we don't accumulate continuation tasks
    forever on a perpetually-active thread."""
    from core.runtime import turn_sink as _ts
    iterations = int(DEFER_TIMEOUT_S / DEFER_POLL_INTERVAL_S)
    for _ in range(iterations):
        await asyncio.sleep(DEFER_POLL_INTERVAL_S)
        active = _ts.active_for_thread(thread_id)
        if active is None or active._closed:
            await _fire(job, project_id, thread_id)
            return
    print(f"[continuation] gave up waiting for thread {thread_id} idle "
          f"after {DEFER_TIMEOUT_S}s for job {job.get('id')}",
          file=sys.stderr, flush=True)


def _count_artifacts_registered(job: dict, project_id: str | None) -> int:
    """How many entity rows landed for this job since it started? Used by
    the continuation message to decide whether the job actually produced
    anything (Fix #1, 2026-06-08): exit-code 0 + zero artifacts means the
    pipeline succeeded vacuously (e.g. silently-bad subprocess command),
    and we MUST NOT tell the agent 'artifacts are now registered'."""
    if not project_id:
        return 0
    started = job.get("started_at") or job.get("created_at")
    if not started:
        return 0
    try:
        import sqlite3
        from core.config import project_db_path
        db = project_db_path(project_id)
        if not db.exists():
            return 0
        c = sqlite3.connect(db)
        try:
            n = c.execute(
                "SELECT COUNT(*) FROM entities "
                "WHERE created_at >= ? AND id != 'workspace' "
                "AND type IN ('figure','table','cell','analysis')",
                (started,),
            ).fetchone()[0]
        finally:
            c.close()
        return int(n)
    except Exception:
        return 0


def _list_job_output_files(job_id: str, project_id: str | None) -> tuple[str | None, list[str]]:
    """Return (absolute_job_work_dir, [filenames]) for the files that the
    background job left behind, excluding the housekeeping pair (script.R
    / script.py and run.log). Used by the continuation message so the
    agent doesn't have to guess where its outputs live.

    Fix #6 (2026-06-08): root-cause of `Error in gzfile: cannot open the
    connection` from the live session — the background job wrote
    seurat_preprocessed.rds to <work>/job_<id>/, but the next R cell ran
    in <work>/ana_<id>/. The agent called readRDS('seurat_preprocessed.rds')
    with a bare relative path and got a wrong-cwd failure. Telling it the
    full path in the continuation removes the guessing."""
    if not project_id or not job_id:
        return None, []
    try:
        from core.config import project_work_dir
        wd = project_work_dir(project_id) / job_id
        if not wd.exists():
            return None, []
        skip = {"script.R", "script.py", "run.log"}
        files = sorted(
            p.name for p in wd.iterdir()
            if p.is_file() and p.name not in skip and not p.name.startswith(".")
        )
        return str(wd), files
    except Exception:
        return None, []


# Strong failure signals that survive a clean (exit-0) worker — e.g. a script
# that caught an install error and still returned 0 (the pagoda2/hdf5r case).
# Used to re-label a 'finished, no artifacts' job as an actual failure.
_JOB_FAIL_MARKERS = (
    "had non-zero exit status",
    "ERROR:",
    "is not available",
    "Execution halted",
    "Traceback (most recent call last)",
)


def _output_failure_lines(text: str) -> list[str]:
    """Lines from a job's log that indicate a real failure despite a 0 exit."""
    return [ln.strip() for ln in (text or "").splitlines()
            if any(m.lower() in ln.lower() for m in _JOB_FAIL_MARKERS)]


def _continuation_message_text(job: dict, project_id: str | None = None) -> str:
    """The synthetic user message the Guide sees as fresh evidence. Starts
    with the literal `[continuation: …]` prefix so the frontend can render
    it with a distinct badge instead of the user avatar.

    Three branches: failed (explicit error), done-with-artifacts (real
    success), and done-with-NO-artifacts (silent failure — agent should
    investigate the log, not trust the 'finished' claim).

    All success branches end with a "Files written to <abs_path>:" listing
    so the agent loads outputs by absolute path (Fix #6, 2026-06-08).

    Keep the success-with-artifacts case generic — the Guide already has
    the plan + the run's registered artifacts in its history; we just
    need to wake it up and tell it to continue."""
    title = job.get("title") or "background job"
    job_id = job.get("id") or "?"
    status = job.get("status") or "done"
    if status == "failed":
        err = (job.get("error") or "").strip().splitlines()
        err_one = err[0][:200] if err else "(no detail)"
        return (
            f"[continuation: background job `{job_id}` ({title}) FAILED]\n\n"
            f"Error: {err_one}\n\n"
            f"The background job you submitted failed. Look at the error, "
            f"decide whether to retry / fix / give up, and either continue "
            f"the plan or summarize what went wrong. Don't silently move on."
        )

    n_artifacts = _count_artifacts_registered(job, project_id)

    # Fix #6 — surface the job's absolute work dir + the files it left
    # behind. The background job ran in its own scratch (<work>/<job_id>/),
    # NOT in the kernel's per-thread scratch — so `readRDS("foo.rds")` from
    # the next kernel cell would fail with "cannot open the connection".
    # Show full paths so the agent reads from where the files actually live.
    abs_dir, files = _list_job_output_files(job_id, project_id)
    files_blurb = ""
    if abs_dir and files:
        # Cap the listing — most pipelines write a handful of outputs; if
        # someone dumps thousands of files we don't want to flood the model
        # context. Show first 20 + a count for the rest.
        head = files[:20]
        more = len(files) - len(head)
        lines = "\n".join(f"  - {abs_dir}/{n}" for n in head)
        more_line = f"\n  …and {more} more" if more > 0 else ""
        files_blurb = (
            f"\n\nFiles written to `{abs_dir}/` (load with absolute paths "
            f"in the next kernel cell — the kernel's cwd is the thread's "
            f"analysis dir, NOT this job's dir):\n{lines}{more_line}"
        )

    if n_artifacts == 0:
        # Job exited 0 but produced no registered artifacts. This is the
        # silent-failure shape (e.g. Rscript wrapper ate the script arg,
        # subprocess no-op'd, kernel produced no plots). DO NOT claim
        # artifacts are registered when none are.
        tail = (job.get("log_tail") or "").strip()
        # Masked failure: the worker exited 0 but the log reports an error — a
        # script that caught an install/exec error and still returned 0. Call it
        # a FAILURE so the agent fixes the cause instead of treating it as a
        # benign no-op (the pagoda2/hdf5r continuation that read "no artifacts").
        fail_lines = _output_failure_lines(tail)
        if fail_lines:
            blurb = "\n".join(fail_lines[-6:])[:600]
            return (
                f"[continuation: background job `{job_id}` ({title}) FAILED — its "
                f"output reports an error even though the worker exited 0 (the "
                f"script likely swallowed it). No artifacts were produced.]\n\n"
                f"Error(s) from the job log:\n```\n{blurb}\n```\n"
                f"Fix the cause (often a missing dependency / system library), then "
                f"retry — prefer ensure_capability over a hand-rolled install so the "
                f"runtime's dependency recovery applies.{files_blurb}"
            )
        tail_blurb = ""
        if tail:
            short_tail = tail[-400:] if len(tail) > 400 else tail
            tail_blurb = f"\n\nLog tail:\n```\n{short_tail}\n```"
        # Files-written list is most valuable in this branch: the job
        # produced real outputs (e.g. an .rds) that aren't registered
        # entities — without the listing, the agent thinks "0 artifacts"
        # means nothing happened and won't try to load them.
        return (
            f"[continuation: background job `{job_id}` ({title}) finished — "
            f"but no new artifacts were registered]\n\n"
            f"The job's worker exited cleanly, but no entities (figures / "
            f"tables / cells) were minted. If the job wrote intermediate "
            f"files (e.g. .rds, .h5ad, .parquet) that aren't auto-harvested, "
            f"load them by ABSOLUTE PATH in the next step. Otherwise the "
            f"script likely no-op'd (wrong interpreter, swallowed args, "
            f"wrote to the wrong dir) — inspect run.log."
            f"{files_blurb}{tail_blurb}"
        )

    return (
        f"[continuation: background job `{job_id}` ({title}) finished — "
        f"{n_artifacts} new artifact{'s' if n_artifacts != 1 else ''} registered]\n\n"
        f"The background job you submitted has completed. The new artifacts "
        f"are registered to this thread's Run. Continue with the next "
        f"step of the plan you were following — read the prior plan in this "
        f"thread to remember what comes next. If the plan is done, summarize "
        f"results and stop."
        f"{files_blurb}"
    )


async def _fire(job: dict, project_id: str, thread_id: str) -> None:
    """Actually start the continuation Guide turn on `thread_id`."""
    # Set project context so guide.py's stream_response writes the
    # continuation message + reads conversation history from the right DB.
    # (Race-condition note: this mutates a global DB_PATH. Acceptable for
    # Phase C / single-user; Phase B needs proper per-request scoping.)
    try:
        from core import projects
        projects.set_current(project_id)
    except Exception as e:  # noqa: BLE001
        print(f"[continuation] set_current({project_id!r}) failed for job "
              f"{job.get('id')}: {e}", file=sys.stderr, flush=True)
        return

    cont_text = _continuation_message_text(job, project_id=project_id)
    focus_entity_id = job.get("focus_entity_id") or "workspace"

    from core.runtime import turn_executor
    run_id = turn_executor.new_run_id()
    started_at = datetime.now(timezone.utc).isoformat()

    # Lazy-import guide.py so this module can be imported without dragging
    # the whole agent loop into a context that doesn't need it (tests).
    from guide import stream_response
    body_gen = stream_response(
        cont_text,
        focus_entity_id=focus_entity_id,
        thread_id=thread_id,
        run_id=run_id,
    )
    turn_executor.start_turn(
        run_id=run_id,
        thread_id=thread_id,
        started_at=started_at,
        body_gen=body_gen,
    )
    print(f"[continuation] fired run={run_id} thread={thread_id} for job {job.get('id')}",
          flush=True)
