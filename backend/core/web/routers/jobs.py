"""Background-job routes — list/get/worker-status/hpc/progress/archive/cancel.
Extracted from main.py (Item 2A.3). Domain-neutral (core.jobs / core.graph.jobs /
core.exec). Mutating routes (archive, cancel) pin via Depends(require_project)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.graph.jobs import list_jobs, get_job
from core.jobs.runner import cancel_job
from core.web.deps import require_project

router = APIRouter()


@router.get("/api/jobs")
def jobs_list(limit: int = 50):
    return list_jobs(limit=limit)


@router.get("/api/jobs/worker")
def jobs_worker_status():
    """Liveness probe for the background-job worker. Phase A — the (i)
    drawer's Jobs tab uses this to flag a stalled/dead worker rather
    than silently lying about 'queued' rows that aren't progressing."""
    from core.jobs.runner import worker_status
    return worker_status()


def _live_job_log_tail(project_id: str | None, job_id: str, max_bytes: int = 4000) -> str | None:
    """Live tail of a RUNNING job's stdout. The DB `log_tail` and `run.log` are
    written only at finalize, so a running job would show NOTHING in the Jobs card
    until it ends. A Slurm job streams stdout unbuffered to `run_dir/job.log` (its
    `-o` file) — read that tail so the card updates live. Falls back to run.log."""
    if not project_id or not job_id:
        return None
    try:
        from core.config import project_work_dir
        d = project_work_dir(project_id) / job_id
        log = d / "job.log"
        if not log.exists():
            log = d / "run.log"
        if not log.exists():
            return None
        size = log.stat().st_size
        with log.open("rb") as f:
            f.seek(max(0, size - max_bytes))
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        if size > max_bytes:                       # drop a partial first line
            nl = text.find("\n")
            if nl >= 0:
                text = text[nl + 1:]
        return text.strip() or None
    except Exception:  # noqa: BLE001
        return None


@router.get("/api/jobs/{job_id}")
def jobs_get(job_id: str):
    j = get_job(job_id)
    if not j:
        raise HTTPException(404, f"job {job_id} not found")
    # A running/queued job's DB log_tail is empty (it's only written at finalize), so serve
    # the LIVE job.log tail — else the Jobs card's output pane stays blank until completion.
    if j.get("status") in ("running", "queued"):
        live = _live_job_log_tail((j.get("params") or {}).get("project_id"), job_id)
        if live:
            j["log_tail"] = live
    return j


@router.post("/api/jobs/{job_id}/archive")
def jobs_archive(job_id: str, _pid: str = Depends(require_project)):
    """Dismiss a terminal job from the Jobs list (soft archive — provenance kept; the job
    is still fetchable by id). Refuses an active (queued/running) job — cancel it first."""
    j = get_job(job_id)
    if not j:
        raise HTTPException(404, f"job {job_id} not found")
    from core.graph.jobs import archive_job
    ok = archive_job(job_id, project_id=(j.get("params") or {}).get("project_id"))
    if not ok:
        raise HTTPException(409, "job is active or already dismissed (cancel a running job first)")
    return {"ok": True}


@router.get("/api/jobs/{job_id}/hpc")
def jobs_hpc(job_id: str):
    """Live scheduler info for a Slurm-submitted job (state/node/elapsed/cores) —
    the (i) Jobs tab fetches this for a running HPC job. Empty for local jobs."""
    j = get_job(job_id)
    if not j:
        raise HTTPException(404, f"job {job_id} not found")
    from core.exec.hpc_session import job_hpc_info
    return job_hpc_info(j)


@router.get("/api/jobs/{job_id}/progress")
def jobs_progress(job_id: str):
    """Live task progress for a running Nextflow job — completed/running/queued counts + the
    current stage, read from its trace.txt. {} for non-pipeline jobs or before the trace exists.
    Polled by the Jobs card so an inline/local head shows progress, not just a spinner."""
    j = get_job(job_id)
    if not j:
        raise HTTPException(404, f"job {job_id} not found")
    from core.exec.nextflow import nextflow_job_progress
    return nextflow_job_progress(j)


@router.get("/api/hpc/session")
def hpc_session():
    """The ABA process' own compute allocation (Slurm node/cores/walltime, or the
    local CPU picture) for the (i) drawer's HPC session card."""
    from core.exec.hpc_session import session_allocation
    return session_allocation()


@router.post("/api/jobs/{job_id}/cancel")
async def jobs_cancel(job_id: str, _pid: str = Depends(require_project)):
    ok = cancel_job(job_id)
    if not ok:
        raise HTTPException(400, "job not found or not cancellable")
    # Notify the originating thread so the agent isn't left hanging on the deferred
    # tool — a background job's terminal transition (here, user-cancel) fires a
    # continuation exactly like done/failed do (continuation.py decides to skip if
    # the job had no thread). Best-effort: never fail the cancel on a notify error.
    job = get_job(job_id)
    try:
        from core.jobs.continuation import enqueue_continuation
        pid = (job.get("params") or {}).get("project_id") if job else None
        await enqueue_continuation(job or {}, str(pid) if pid else None)
    except Exception as e:  # noqa: BLE001
        print(f"[jobs.cancel] continuation after cancel failed for {job_id}: {e}", flush=True)
    return job
