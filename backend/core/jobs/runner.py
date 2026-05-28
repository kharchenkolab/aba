"""
Background job queue (Phase 17).

A single async worker processes queued jobs one at a time. Each job is a
Python execution (the same sandbox path as run_python) that runs in a
cancellable subprocess. When a job finishes, its artifacts auto-register
with the focus context captured at submit time — so a figure produced by
a background pbmc3k run lands under the right analysis, exactly as if it
had run inline.

Single-process, sequential, in-memory worker — fine for the single-user
prototype. Concurrency / a real broker is a hardening-track concern.
"""
from __future__ import annotations
import asyncio
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from datetime import datetime, timezone

from config import DATA_DIR, ARTIFACTS_DIR
from core.graph.jobs import create_job, get_job, update_job
from core.hooks.dispatcher import dispatch


_QUEUE: "asyncio.Queue[str]" = asyncio.Queue()
_RUNNING: dict[str, subprocess.Popen] = {}
_CANCELLED: set[str] = set()
_WORKER_STARTED = False

# Emitted to anyone listening (the Queues view polls instead, but this is
# here for a future SSE channel).
_LISTENERS: list = []


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit_python_job(code: str, title: str, focus_entity_id: str | None,
                      timeout_s: int = 300, project_id: str | None = None) -> dict:
    """Create a queued job and enqueue it. Returns the job record. `project_id`
    is captured at submit time so the job runs in the right project's scratch
    workspace even if the active project changes before the worker picks it up."""
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job(
        job_id=job_id,
        kind="run_python",
        title=title or "Background analysis",
        focus_entity_id=focus_entity_id,
        params={"code": code, "timeout_s": timeout_s, "project_id": project_id},
    )
    _QUEUE.put_nowait(job_id)
    return job


def cancel_job(job_id: str) -> bool:
    """Cancel a queued or running job. Returns True if it was actionable. Fires
    the job's CancelToken so the shared exec core killpg's the whole process
    group (forked children die too), matching the synchronous Stop path."""
    job = get_job(job_id)
    if not job:
        return False
    if job["status"] in ("done", "failed", "cancelled"):
        return False
    _CANCELLED.add(job_id)
    from core.runtime import cancellation
    tok = cancellation.get(job_id)
    if tok is not None:
        tok.cancel("user cancelled job")
    update_job(job_id, status="cancelled", finished_at=_utcnow())
    return True


async def _run_one(job_id: str) -> None:
    job = get_job(job_id)
    if not job or job["status"] == "cancelled" or job_id in _CANCELLED:
        return

    params = job["params"] or {}
    code = params.get("code", "")
    timeout_s = max(5, min(int(params.get("timeout_s") or 300), 1800))
    project_id = params.get("project_id") or "default"
    focus_entity_id = job["focus_entity_id"]

    update_job(job_id, status="running", started_at=_utcnow())

    # P5: run through the SAME execution core as the synchronous run_python, so
    # the background job sees the project scratch workspace, the pylib overlay,
    # the conda tools env on PATH, and killpg cancellation. A per-job CancelToken
    # (keyed by job_id) lets cancel_job kill the whole process group.
    from core.exec.run import run_python_code
    from core.runtime import cancellation
    biomni = str(Path(__file__).resolve().parents[2] / "biomni")
    token = cancellation.acquire(job_id)
    try:
        loop = asyncio.get_event_loop()
        result_obj = await loop.run_in_executor(
            None,
            lambda: run_python_code(code, project_id=str(project_id), run_id=job_id,
                                    timeout_s=timeout_s, cancel_token=token,
                                    extra_syspath=[biomni]),
        )

        if job_id in _CANCELLED or result_obj.get("status") == "cancelled":
            update_job(job_id, status="cancelled", finished_at=_utcnow())
            return

        if "error" in result_obj:
            update_job(job_id, status="failed", error=result_obj["error"][:1000],
                       log_tail=result_obj["error"][:1500], finished_at=_utcnow())
            return

        stdout = result_obj.get("stdout", "")
        stderr = result_obj.get("stderr", "")
        log_tail = (stdout[-1500:] + ("\n" + stderr[-500:] if stderr else "")).strip()
        if result_obj.get("returncode") != 0:
            update_job(job_id, status="failed",
                       error=stderr[-1000:] or f"exit code {result_obj.get('returncode')}",
                       log_tail=log_tail, finished_at=_utcnow())
            return

        # Register artifacts via the on_job_complete hook (bio handler).
        dispatch("on_job_complete", {
            "tool_name": "run_python",
            "tool_input": {"code": code},
            "result_obj": result_obj,
            "focus_entity_id": focus_entity_id,
            "analysis_ctx": {"analysis_id": None, "turn_index": 0},
            "thread_id": None,
            "new_entities": [],
        })
        update_job(job_id, status="done", log_tail=log_tail, finished_at=_utcnow())
    except Exception as e:  # noqa: BLE001
        update_job(job_id, status="failed", error=str(e), finished_at=_utcnow())
    finally:
        cancellation.release(job_id)


async def _worker() -> None:
    while True:
        job_id = await _QUEUE.get()
        try:
            await _run_one(job_id)
        except Exception:  # noqa: BLE001
            pass
        finally:
            _QUEUE.task_done()


def start_worker() -> None:
    """Launch the worker task once, from FastAPI startup."""
    global _WORKER_STARTED
    if _WORKER_STARTED:
        return
    _WORKER_STARTED = True
    asyncio.get_event_loop().create_task(_worker())
