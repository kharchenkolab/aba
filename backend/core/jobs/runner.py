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
                      timeout_s: int = 300) -> dict:
    """Create a queued job and enqueue it. Returns the job record."""
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job(
        job_id=job_id,
        kind="run_python",
        title=title or "Background analysis",
        focus_entity_id=focus_entity_id,
        params={"code": code, "timeout_s": timeout_s},
    )
    _QUEUE.put_nowait(job_id)
    return job


def cancel_job(job_id: str) -> bool:
    """Cancel a queued or running job. Returns True if it was actionable."""
    job = get_job(job_id)
    if not job:
        return False
    if job["status"] in ("done", "failed", "cancelled"):
        return False
    _CANCELLED.add(job_id)
    proc = _RUNNING.get(job_id)
    if proc and proc.poll() is None:
        proc.terminate()
    update_job(job_id, status="cancelled", finished_at=_utcnow())
    return True


async def _run_one(job_id: str) -> None:
    job = get_job(job_id)
    if not job or job["status"] == "cancelled" or job_id in _CANCELLED:
        return

    params = job["params"] or {}
    code = params.get("code", "")
    timeout_s = max(5, min(int(params.get("timeout_s") or 300), 600))
    focus_entity_id = job["focus_entity_id"]

    update_job(job_id, status="running", started_at=_utcnow())

    tmp_dir = Path("/tmp") / f"aba_job_{uuid.uuid4().hex}"
    tmp_dir.mkdir()
    try:
        full_code = f"DATA_DIR = {str(DATA_DIR)!r}\n" + code
        (tmp_dir / "script.py").write_text(full_code)
        env = os.environ.copy()
        env["MPLBACKEND"] = "Agg"

        # Run in a thread so we don't block the event loop; keep a handle
        # for cancellation.
        loop = asyncio.get_event_loop()

        def _spawn() -> tuple[int, str, str]:
            proc = subprocess.Popen(
                [sys.executable, str(tmp_dir / "script.py")],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                env=env, cwd=str(tmp_dir),
            )
            _RUNNING[job_id] = proc
            try:
                out, err = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, err = proc.communicate()
                err = (err or "") + f"\n[timed out after {timeout_s}s]"
            return proc.returncode, out or "", err or ""

        rc, stdout, stderr = await loop.run_in_executor(None, _spawn)
        _RUNNING.pop(job_id, None)

        if job_id in _CANCELLED:
            return  # already marked cancelled

        # Collect artifacts the same way run_python does.
        plots = []
        for png in tmp_dir.glob("*.png"):
            dest_name = f"{uuid.uuid4().hex}.png"
            shutil.move(str(png), str(ARTIFACTS_DIR / dest_name))
            plots.append({"url": f"/artifacts/{dest_name}", "original_name": png.name})

        result_obj = {
            "stdout": stdout[:4000], "stderr": stderr[:2000],
            "returncode": rc, "plots": plots,
        }

        log_tail = (stdout[-1500:] + ("\n" + stderr[-500:] if stderr else "")).strip()

        if rc != 0:
            update_job(job_id, status="failed",
                       error=stderr[-1000:] or f"exit code {rc}",
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
        _RUNNING.pop(job_id, None)
        shutil.rmtree(tmp_dir, ignore_errors=True)


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
