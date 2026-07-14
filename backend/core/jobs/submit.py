"""Background-job SUBMISSION API — create a job + route it to its submitter (Item #2
runner.py split). Stateless: these go through get_submitter_for(); the async worker,
finalize/continuation, reconcile, and the shared queue state stay in runner.py.
Re-exported from core.jobs.runner so existing `from core.jobs.runner import
submit_python_job` (guide, run_exec, plan_etc, hpc_session, ...) keep working."""
from __future__ import annotations

import json
import uuid

from core import config
from core.graph.jobs import create_job


def _bg_submission(execution: str | None, estimate: dict | None) -> tuple[str, str | None]:
    """Submission target for a plain background job (python/r), shared with the nf path via
    resolve_submission_target. execution 'slurm' → sbatch; None → the deployment default
    (sbatch when ABA_BATCH_SUBMITTER=slurm, else the local lane — W2: a bare weft task);
    'local'/'auto' → 'inline' when the job's estimate fits ABA's allocation, else 'slurm'."""
    if (execution or "").lower() not in ("local", "auto"):
        from core.jobs.submitter import submitter_name
        if (execution or "").lower() == "slurm" or submitter_name() == "slurm":
            return "slurm", None
        # Non-slurm deployment, no explicit ask: the local background lane.
        # (Pre-W2 this returned 'slurm' unconditionally — a plain background=True
        # on a personal install died on a missing sbatch.)
        return "inline", "local deployment — background runs on the local lane"
    from core.exec.hpc_session import aba_allocation_capacity
    heaviest = {"cpus": (estimate or {}).get("cores"), "mem_gb": (estimate or {}).get("mem_gb"),
                "gpu": (estimate or {}).get("gpu")}
    return resolve_submission_target(execution.lower(), heaviest, aba_allocation_capacity())


def submit_python_job(code: str, title: str, focus_entity_id: str | None,
                      timeout_s: int = 300, project_id: str | None = None,
                      thread_id: str | None = None, run_id: str | None = None,
                      estimate: dict | None = None, env: str | None = None,
                      execution: str | None = None) -> dict:
    """Create a queued job and enqueue it. Returns the job record. `project_id`
    is captured at submit time so the job runs in the right project's scratch
    workspace even if the active project changes before the worker picks it up.
    `thread_id` + `run_id` (the active Run at submit time) are captured so the
    job's outputs attach to the originating Run/thread instead of orphaning.
    `execution` 'local'/'auto' runs it in-place in ABA's own allocation (no sbatch)
    when it fits; None/'slurm' sbatches (the default when ABA_BATCH_SUBMITTER=slurm)."""
    submission, submission_reason = _bg_submission(execution, estimate)
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job(
        job_id=job_id,
        kind="run_python",
        title=title or "Background analysis",
        focus_entity_id=focus_entity_id,
        params={"code": code, "timeout_s": timeout_s, "project_id": project_id,
                "thread_id": thread_id, "run_id": run_id, "estimate": estimate or {},
                "env": env, "execution": execution,
                "submission": submission, "submission_reason": submission_reason},
        project_id=project_id,
    )
    from core.jobs.submitter import get_submitter_for
    get_submitter_for(submission).submit(job)
    return job


def submit_r_job(code: str, title: str, focus_entity_id: str | None,
                 timeout_s: int = 600, project_id: str | None = None,
                 thread_id: str | None = None, run_id: str | None = None,
                 estimate: dict | None = None, env: str | None = None,
                 execution: str | None = None) -> dict:
    """Create a queued R job. Mirrors submit_python_job but with kind='run_r';
    the worker dispatches to run_r_code in core.exec.run, which invokes Rscript
    against the project's tools-env R + project library, captures stdout/stderr,
    and harvests artifacts. Used by run_r(background=True) — the proper path
    for long Seurat/DESeq2/etc. work that would otherwise force the agent to
    shell out via run_python(subprocess.run([\"Rscript\", ...]))."""
    submission, submission_reason = _bg_submission(execution, estimate)
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job(
        job_id=job_id,
        kind="run_r",
        title=title or "Background R analysis",
        focus_entity_id=focus_entity_id,
        params={"code": code, "timeout_s": timeout_s, "project_id": project_id,
                "thread_id": thread_id, "run_id": run_id, "estimate": estimate or {},
                "env": env, "execution": execution,
                "submission": submission, "submission_reason": submission_reason},
        project_id=project_id,
    )
    from core.jobs.submitter import get_submitter_for
    get_submitter_for(submission).submit(job)
    return job


def _running_inline_cores() -> float:
    """Cores committed to currently-RUNNING inline jobs (params.submission=='inline'), summed
    across project DBs — so the resolver won't oversubscribe ABA's allocation with concurrent
    inline jobs. Best-effort; 0 on any error (and in SINGLE-DB mode, where there are no per-
    project job tables to scan — the guard is a multi-project/production concern)."""
    from core.config import PROJECTS_DIR
    import sqlite3
    total = 0.0
    try:
        if not PROJECTS_DIR.exists():
            return 0.0
        for proj_dir in sorted(PROJECTS_DIR.iterdir()):
            db_file = proj_dir / "project.db"
            if not proj_dir.is_dir() or not db_file.exists() or proj_dir.name.startswith("_"):
                continue
            c = sqlite3.connect(db_file); c.row_factory = sqlite3.Row
            try:
                if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                    continue
                for r in c.execute("SELECT params FROM jobs WHERE status='running'").fetchall():
                    p = json.loads(r["params"] or "{}")
                    if p.get("submission") != "inline":
                        continue
                    lr = p.get("local_resources") or {}
                    est = p.get("estimate") or {}
                    total += float(lr.get("cores") or est.get("cores") or 1)
            finally:
                c.close()
    except Exception:  # noqa: BLE001
        return total
    return total


# AUTO only inlines genuinely SMALL jobs — beyond this a job fans out to Slurm even if it
# would fit ABA's allocation, because many-task/heavy work parallelizes better across the
# cluster and long jobs want Slurm's durability (inline dies with ABA). Explicit execution=local
# bypasses this ceiling (the user asked for local); the physical-fit + concurrency checks always apply.
def _auto_inline_ceiling() -> tuple[float, float]:
    return (config.settings.inline_auto_max_cores.get(),
            config.settings.inline_auto_max_mem_gb.get())


def resolve_submission_target(requested: str, heaviest: dict | None, capacity: dict) -> tuple[str, str]:
    """Decide WHERE a background job runs — 'inline' (a subprocess in ABA's own allocation,
    no sbatch) vs 'slurm' (sbatch a dedicated allocation) — and why. Called when the job would
    run with a local executor (requested execution in local/auto). `heaviest` is the biggest
    single task's {cpus, mem_gb} that must fit ABA's *free* capacity. See misc/inplace_submission.md."""
    if capacity.get("submitter") != "slurm":
        return "inline", "local submitter — runs in ABA's process"
    if not capacity.get("inline_ok"):
        return "slurm", "ABA is on a login node (not in a compute allocation) — using Slurm"
    # A GPU job must not run inline on a GPU-less allocation (it would silently fall to
    # CPU). Route to Slurm so the submitter picks a GPU partition from estimate.gpu.
    if (heaviest or {}).get("gpu") and not capacity.get("gpus"):
        return "slurm", "needs a GPU ABA's allocation lacks — using Slurm (GPU partition)"
    cap_c, cap_m = capacity.get("cores"), capacity.get("mem_gb")
    used_c = capacity.get("inline_used_cores") or 0          # cores already committed to running inline jobs
    avail_c = (cap_c - used_c) if cap_c else cap_c
    h_c, h_m = (heaviest or {}).get("cpus"), (heaviest or {}).get("mem_gb")
    # Physical/concurrency fit (applies to explicit local AND auto): must fit what's FREE now.
    over = []
    if h_c and avail_c is not None and h_c > avail_c:
        over.append(f"{h_c:g}c > {avail_c:g}c free" + (f" ({used_c:g}c in use)" if used_c else ""))
    if h_m and cap_m and h_m > cap_m:
        over.append(f"{h_m:g}GB > {cap_m:g}GB")
    if over:
        return "slurm", "doesn't fit ABA's free allocation (" + ", ".join(over) + ") — using Slurm"
    # AUTO preference: keep inline for SMALL jobs only; fan bigger work out for parallelism/durability.
    if requested == "auto":
        max_c, max_m = _auto_inline_ceiling()
        if (h_c and h_c > max_c) or (h_m and h_m > max_m):
            return "slurm", (f"auto: job ({h_c or '?'}c/{h_m or '?'}GB) is substantial — Slurm fan-out "
                             f"for parallelism (inline is for small/quick work)")
    return "inline", f"fits ABA's allocation ({cap_c}c" + (f"/{cap_m:g}GB" if cap_m else "") + ") — runs in-place"


def submit_nextflow_job(pipeline: str, title: str, focus_entity_id: str | None,
                        revision: str | None = None, profile: str | None = None,
                        nf_params: dict | None = None, outdir: str | None = None,
                        timeout_s: int | None = None, execution: str | None = None,
                        project_id: str | None = None, thread_id: str | None = None,
                        run_id: str | None = None, estimate: dict | None = None) -> dict:
    """Create a queued Nextflow pipeline job and enqueue it (kind='run_nextflow').
    The worker / Slurm entry dispatches to core.exec.nextflow.run_nextflow_code,
    which runs the head process (Nextflow fans tasks out via the site executor),
    harvests --outdir, and writes a kind:workflow exec record. `nf_params` are the
    pipeline's `--<k> <v>` params. Used by run_nextflow(background=True).

    `execution` is "slurm" (default: fan each task out as its own Slurm job — for heavy
    real data) or "local" (run tasks on the head's own, larger, allocation — far faster
    for small/test pipelines whose per-task compute is dwarfed by Slurm queue latency).

    `timeout_s` is the head's app-level kill timeout; left None it defaults to the
    chosen allocation's generous Slurm walltime (head_timeout_s) — NOT a runtime estimate,
    since the head mostly waits in the queue (a walltime overrun is a Slurm kill → auto-resume)."""
    from core.exec.nextflow import nextflow_config, head_timeout_s
    ncfg = nextflow_config()
    requested = (execution or ncfg.get("execution") or "slurm").lower()
    if requested not in ("slurm", "local", "auto"):
        requested = "slurm"
    # The estimate sizes the local allocation AND routes "auto". Local mode runs every task
    # on ONE allocation, so it's sized to the pipeline's heaviest task (from its declared
    # nf-core resources). Best-effort: a fetch miss → auto falls back to slurm, and a chosen
    # local run uses the flat nextflow.local default.
    local_resources, resource_estimate, est = None, None, None
    if requested in ("local", "auto"):
        try:
            from core.exec.nextflow_resources import estimate_pipeline_resources
            est = estimate_pipeline_resources(pipeline, revision, profile)
        except Exception:  # noqa: BLE001 — never block a submit on the estimate
            est = None
    if requested == "auto":
        execution = "local" if (est and est.get("local_viable")) else "slurm"
    else:
        execution = requested
    if execution == "local" and est:
        local_resources = est.get("recommended_local")
    # Submission target: run IN-PLACE (no sbatch) in ABA's own allocation when the executor is
    # local AND the heaviest task fits ABA's capacity; else sbatch. execution=slurm always sbatches.
    submission, submission_reason = "slurm", None
    if execution == "local":
        from core.exec.hpc_session import aba_allocation_capacity
        cap = aba_allocation_capacity()
        submission, submission_reason = resolve_submission_target(
            requested, (est or {}).get("heaviest_task") if est else None, cap)
        if submission == "inline":
            # size the local executor pool to ABA's OWN allocation (not the dedicated-head default)
            local_resources = {"cores": cap["cores"],
                               "mem_gb": cap.get("mem_gb") or (local_resources or {}).get("mem_gb")}
    if est:
        resource_estimate = {**{k: est.get(k) for k in
                                ("heaviest_task", "caps", "local_viable", "reason")},
                             "requested": requested, "resolved": execution}
    if timeout_s is None:
        timeout_s = head_timeout_s(ncfg["local"] if execution == "local" else ncfg["head"])
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job(
        job_id=job_id,
        kind="run_nextflow",
        title=title or f"Nextflow: {pipeline}",
        focus_entity_id=focus_entity_id,
        params={"pipeline": pipeline, "revision": revision, "profile": profile,
                "nf_params": nf_params or {}, "outdir": outdir, "execution": execution,
                "submission": submission, "submission_reason": submission_reason,
                "local_resources": local_resources, "resource_estimate": resource_estimate,
                "code": f"nextflow run {pipeline}" + (f" -profile {profile}" if profile else ""),
                "timeout_s": timeout_s, "project_id": project_id,
                "thread_id": thread_id, "run_id": run_id, "estimate": estimate or {}},
        project_id=project_id,
    )
    from core.jobs.submitter import get_submitter_for
    get_submitter_for(submission).submit(job)
    return job


def submit_import_run_job(source_dir: str, title: str, run_id: str,
                          focus_entity_id: str | None = None,
                          pipeline: str | None = None, revision: str | None = None,
                          project_id: str | None = None, thread_id: str | None = None,
                          timeout_s: int | None = None) -> dict:
    """Create + enqueue a queued import job (kind='import_run'). The worker dispatches to
    core.exec.import_run.import_run_code, which SCRAPES the external `source_dir` and returns the
    standard result_obj — so the artifacts attach to the pre-created Run (`run_id`) and the
    continuation presents it, exactly like a finished pipeline (misc/external_import.md).

    Always runs INLINE (submission='inline'): a scrape is local I/O in ABA's own allocation — no
    compute to fan out, nothing to sbatch. The Run entity itself is created by the caller
    (open_imported_run) BEFORE submit, so `run_id` names it and harvested children attach to it."""
    job_id = f"job_{uuid.uuid4().hex[:10]}"
    job = create_job(
        job_id=job_id,
        kind="import_run",
        title=title or f"Import: {source_dir}",
        focus_entity_id=focus_entity_id,
        params={"source_dir": source_dir, "run_id": run_id,
                "pipeline": pipeline, "revision": revision,
                "submission": "inline",
                "code": f"import_run {source_dir}",
                "timeout_s": timeout_s or 1800, "project_id": project_id,
                "thread_id": thread_id},
        project_id=project_id,
    )
    from core.jobs.submitter import get_submitter_for
    get_submitter_for("inline").submit(job)
    return job

