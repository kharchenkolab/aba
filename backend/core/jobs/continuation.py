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


def _continuation_message_text(job: dict, project_id: str | None = None) -> str:
    """The synthetic user message the Guide sees as fresh evidence. Starts
    with the literal `[continuation: …]` prefix so the frontend can render
    it with a distinct badge instead of the user avatar.

    Three branches: failed (explicit error), done-with-artifacts (real
    success), and done-with-NO-artifacts (silent failure — agent should
    investigate the log, not trust the 'finished' claim).

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
    if n_artifacts == 0:
        # Job exited 0 but produced no registered artifacts. This is the
        # silent-failure shape (e.g. Rscript wrapper ate the script arg,
        # subprocess no-op'd, kernel produced no plots). DO NOT claim
        # artifacts are registered when none are.
        tail = (job.get("log_tail") or "").strip()
        tail_blurb = ""
        if tail:
            short_tail = tail[-400:] if len(tail) > 400 else tail
            tail_blurb = f"\n\nLog tail:\n```\n{short_tail}\n```"
        return (
            f"[continuation: background job `{job_id}` ({title}) finished — "
            f"but no new artifacts were registered]\n\n"
            f"The job's worker exited cleanly, but nothing was produced. "
            f"Possibilities: the script no-op'd (silently swallowed args / "
            f"wrong interpreter), wrote outputs to the wrong directory, "
            f"or skipped the artifact-producing steps. Inspect the job "
            f"directory's run.log to see what actually happened, then "
            f"either re-run with the bug fixed or summarize the failure."
            f"{tail_blurb}"
        )

    return (
        f"[continuation: background job `{job_id}` ({title}) finished — "
        f"{n_artifacts} new artifact{'s' if n_artifacts != 1 else ''} registered]\n\n"
        f"The background job you submitted has completed. The new artifacts "
        f"are registered to this thread's Run. Continue with the next "
        f"step of the plan you were following — read the prior plan in this "
        f"thread to remember what comes next. If the plan is done, summarize "
        f"results and stop."
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
