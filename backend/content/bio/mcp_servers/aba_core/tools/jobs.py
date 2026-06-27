"""Background-job introspection + control tools (J-1, J-2).

The agent submits long jobs via `run_python(background=True)` or
`run_r(background=True)` and gets back a job_id. Without these tools,
the agent has no way to peek at a job's status mid-run — the user
asking "is it still running?" forces a deflection to the UI Queues panel.

`get_job_status` exposes a small read-only view of one job (defaults to
the thread's most recent job). `cancel_job` is destructive — the agent
must confirm with the user before calling it (enforced at the prompt
layer; see behavior.md "Ask before running large or destructive ops").

Backend-agnostic design: both tools read from / write to the local
`jobs` table. The local worker today, the arq worker tomorrow, and a
future HPC poller all maintain the same fields. The only backend-specific
piece is `cancel_job`'s underlying kill hook — local uses killpg via
the cancel_token; an HPC backend would dispatch `scancel <slurm_id>`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _elapsed_s(job: dict) -> float | None:
    """How long has this job been running (or how long did it take)?
    Returns None if it never started. For terminal states, returns the
    total wall time start→finish; for in-flight states, returns wall
    time since start."""
    started = _parse_iso(job.get("started_at"))
    if started is None:
        return None
    finished = _parse_iso(job.get("finished_at"))
    if finished is None:
        finished = datetime.now(timezone.utc)
    return round((finished - started).total_seconds(), 1)


def _resolve_default_job_id(ctx: dict) -> str | None:
    """If the agent omits job_id, default to the most-recent job
    submitted from this thread (the one it almost certainly means)."""
    thread_id = ctx.get("thread_id")
    project_id = ctx.get("project_id")
    if not thread_id:
        return None
    from core.graph.jobs import list_jobs
    rows = list_jobs(limit=50, project_id=project_id) or []
    for r in rows:
        params = r.get("params") or {}
        if params.get("thread_id") == thread_id:
            return r["id"]
    return None


def _read_run_log_tail(project_id: str | None, job_id: str,
                      max_bytes: int = 4000) -> str | None:
    """Pull a fresher tail than what's persisted to the DB column
    (which the runner updates periodically, not on every chunk). Reads
    the last `max_bytes` of <work>/<job_id>/run.log directly."""
    if not project_id or not job_id:
        return None
    try:
        from core.config import project_work_dir
        log = project_work_dir(project_id) / job_id / "run.log"
        if not log.exists():
            return None
        size = log.stat().st_size
        offset = max(0, size - max_bytes)
        with log.open("rb") as f:
            f.seek(offset)
            data = f.read()
        # Drop a possibly-partial first line if we seeked into the middle.
        text = data.decode("utf-8", errors="replace")
        if offset > 0:
            nl = text.find("\n")
            if nl >= 0:
                text = text[nl + 1:]
        return text
    except Exception:
        return None


def register_jobs_tools(mcp: FastMCP) -> None:
    """Register the background-job introspection / control tools on `mcp`."""

    @mcp.tool()
    def describe_compute() -> dict:
        """Describe the compute environment so you can plan WHERE a step runs.
        Call this before a heavy / parallel / GPU / long step — especially on a
        cluster — to see this node's capacity and, when Slurm is available, the
        live partitions you could submit to and how busy they are.

        How to decide:
        - LOCAL (mode=local): run interactively — kernel state persists, and a
          long cell is fine with a higher `timeout_s`. Only set `background=True`
          when the USER asks, or to queue several independent jobs in parallel.
          Don't background just to "avoid a timeout".
        - SLURM (mode=slurm): send a step to Slurm (run_python/run_r with
          `background=True` + `est_cores`/`est_mem_gb`/`est_gpu`/
          `estimated_runtime_min`) when it needs MORE cores/mem/GPU than this node
          has, or might approach the allocation's remaining walltime, or would be
          meaningfully faster AND that speedup beats the partition's queue wait
          (read the `wait` label). Otherwise run interactively.
        - ALWAYS: a background/Slurm job is a FRESH process with NONE of your
          interactive objects — load inputs from disk, write outputs to disk.

        Returns: `mode`, `node_cores`/`node_mem_gb`/`node_gpus`,
        `walltime_remaining_min` (null = unbounded), and on a cluster
        `partitions` (each with `cpus_per_node`, `mem_gb_per_node`, `gpu`,
        `nodes_idle`, `max_walltime`, `wait`) + `partitions_source`
        (live|config) + `user_access`. `summary` is a one-line digest.
        """
        from core.exec.compute_env import compute_env
        e = compute_env()
        gpu = f" / {e['node_gpus']} GPU" if e.get("node_gpus") else " / no GPU"
        local = f"this node {e['node_cores']} cores / {e['node_mem_gb']} GB{gpu}"
        wt = e.get("walltime_remaining_min")
        if wt is not None:
            local += f" · ~{round(wt / 60, 1)}h walltime left"
        e["summary"] = f"Mode: {e['mode']}. {local}."
        if e.get("partitions"):
            e["summary"] += f" Slurm partitions ({e.get('partitions_source')}): " + "; ".join(
                f"{p['partition']} (≤{p.get('cpus_per_node', '?')} cores/node"
                + (", GPU" if p.get("gpu") else "")
                + f", {p.get('wait', '?')})" for p in e["partitions"])
        return e

    @mcp.tool()
    def get_job_status(job_id: str | None = None,
                       aba_ctx_id: str | None = None) -> dict:
        """Inspect a background job (read-only). Returns the current status,
        elapsed time, work dir, and a tail of the live run log.

        If `job_id` is omitted, defaults to the most recently submitted job
        on this thread (the one the user almost always means). Useful when
        the user asks "is it still running?" — answer from here instead of
        deflecting to the UI's Queues panel.

        Returns:
          - `id`, `kind` (run_python|run_r), `title`, `status`
            (queued|running|done|failed|cancelled)
          - `started_at`, `finished_at`, `elapsed_s`
          - `work_dir` — absolute path to <work>/<job_id>/ where the job's
            outputs live. Use full paths to read them; the kernel's cwd
            is the thread's analysis dir, not this dir.
          - `log_tail` — last ~4KB of run.log (live, not DB-cached)
          - `error` — the error message if the job failed
        """
        from core.runtime.tool_ctx import peek_ctx
        from core.graph.jobs import get_job
        ctx = peek_ctx(aba_ctx_id)
        project_id = ctx.get("project_id")

        resolved_id = job_id or _resolve_default_job_id(ctx)
        if not resolved_id:
            return {"error": "no job_id given and no recent job for this thread"}

        job = get_job(resolved_id, project_id=project_id)
        if not job:
            return {"error": f"job {resolved_id!r} not found"}

        # Live log tail beats the DB column; runner only flushes that
        # periodically, so a job that's actively streaming will look
        # frozen from the DB perspective.
        live_tail = _read_run_log_tail(project_id, resolved_id)
        log_tail = live_tail if live_tail is not None else (job.get("log_tail") or "")

        work_dir = None
        try:
            from core.config import project_work_dir
            if project_id:
                work_dir = str(project_work_dir(project_id) / resolved_id)
        except Exception:
            pass

        return {
            "id":          job["id"],
            "kind":        job.get("kind"),
            "title":       job.get("title"),
            "status":      job["status"],
            "started_at":  job.get("started_at"),
            "finished_at": job.get("finished_at"),
            "elapsed_s":   _elapsed_s(job),
            "work_dir":    work_dir,
            "log_tail":    log_tail,
            "error":       job.get("error"),
        }

    @mcp.tool()
    def cancel_job(job_id: str,
                   aba_ctx_id: str | None = None) -> dict:
        """Cancel a queued or running background job. Destructive — the
        running subprocess is killpg'd (locally) or scancel'd (HPC); any
        in-progress work is lost. ALWAYS confirm with the user before
        calling this; never cancel a job because it's "taking too long"
        without their go-ahead.

        Returns:
          - `ok`: True if the cancel signal was sent
          - `prior_status`: the status the job had before cancel
          - `error`: explanation when ok=False (e.g. already terminal,
            unknown job)
        """
        from core.runtime.tool_ctx import peek_ctx
        from core.graph.jobs import get_job
        from core.jobs.runner import cancel_job as _runner_cancel
        ctx = peek_ctx(aba_ctx_id)
        project_id = ctx.get("project_id")

        job = get_job(job_id, project_id=project_id)
        if not job:
            return {"ok": False, "error": f"job {job_id!r} not found"}
        prior = job["status"]
        if prior in ("done", "failed", "cancelled"):
            return {"ok": False, "prior_status": prior,
                    "error": f"job already {prior}; nothing to cancel"}

        ok = _runner_cancel(job_id, project_id=project_id)
        return {"ok": bool(ok), "prior_status": prior}
