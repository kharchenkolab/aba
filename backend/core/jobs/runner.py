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

# Background jobs exist to run LONG work, so they do NOT inherit the interactive
# 30-min kernel ceiling (that's only to keep the live kernel snappy). Default a
# generous 1 h when the submitter gave no size; the 24 h cap is a hung-job
# backstop. The submitter (run_python tool) sizes the real ceiling from the
# agent's estimated_runtime_min — see content/bio/tools/run_exec._background_timeout_s.
BACKGROUND_DEFAULT_TIMEOUT_S = 3600        # 1 h
BACKGROUND_MAX_TIMEOUT_S = 24 * 3600       # 24 h

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


def _settle_job_deferred(job_id: str, lookup_pid: str | None) -> None:
    """Resolve the parked deferred tool_use for a now-terminal background job (idempotent):
    writes the terminal tool_result + transitions the turn out of AWAITING_TOOL_RESULT, so
    the chat tool line resolves instead of spinning forever. Best-effort — never let it
    break job finalization. Call BEFORE the continuation so the tool_result lands in-order.

    Binds the job's project: the runs/messages tables are per-project (context-bound DB),
    so settle must run under the job's project or it looks in the wrong DB and finds nothing."""
    try:
        from core.runtime.checkpoint import settle_deferred_job
        from core import projects as _projects
        with _projects.bind(str(lookup_pid) if lookup_pid else None):
            fresh = get_job(job_id, project_id=lookup_pid)
            if fresh:
                settle_deferred_job(fresh)
    except Exception as e:  # noqa: BLE001
        _record_worker_failure("settle_deferred", job_id, e)


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


def _bg_submission(execution: str | None, estimate: dict | None) -> tuple[str, str | None]:
    """Submission target for a plain background job (python/r), shared with the nf path via
    resolve_submission_target. execution None/'slurm' → 'slurm' (sbatch, today's default);
    'local'/'auto' → 'inline' when the job's estimate fits ABA's allocation, else 'slurm'."""
    if (execution or "").lower() not in ("local", "auto"):
        return "slurm", None
    from core.exec.hpc_session import aba_allocation_capacity
    heaviest = {"cpus": (estimate or {}).get("cores"), "mem_gb": (estimate or {}).get("mem_gb")}
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
    import os
    return (float(os.environ.get("ABA_INLINE_AUTO_MAX_CORES") or 8),
            float(os.environ.get("ABA_INLINE_AUTO_MAX_MEM_GB") or 32))


def resolve_submission_target(requested: str, heaviest: dict | None, capacity: dict) -> tuple[str, str]:
    """Decide WHERE a background job runs — 'inline' (a subprocess in ABA's own allocation,
    no sbatch) vs 'slurm' (sbatch a dedicated allocation) — and why. Called when the job would
    run with a local executor (requested execution in local/auto). `heaviest` is the biggest
    single task's {cpus, mem_gb} that must fit ABA's *free* capacity. See misc/inplace_submission.md."""
    if capacity.get("submitter") != "slurm":
        return "inline", "local submitter — runs in ABA's process"
    if not capacity.get("inline_ok"):
        return "slurm", "ABA is on a login node (not in a compute allocation) — using Slurm"
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


def _submitter_for_job(job: dict):
    """The submitter that actually OWNS a created job's execution — used for cancel.
    In-place submission (misc/inplace_submission.md) means the deployment default is
    NOT reliable: with ABA_BATCH_SUBMITTER=slurm a small job may still have run INLINE,
    and routing its cancel to SlurmSubmitter would `scancel` a job with no Slurm id (a
    no-op) while the inline process keeps running (orphaned head). So pick by the target
    resolved at submit (params.submission); fall back to hard evidence it reached Slurm
    (submitter/slurm_id), else the deployment default (legacy rows predating IP)."""
    from core.jobs.submitter import get_submitter_for
    params = job.get("params") or {}
    target = params.get("submission")
    if target in ("inline", "slurm"):
        return get_submitter_for(target)
    if params.get("submitter") == "slurm" or params.get("slurm_id"):
        return get_submitter_for("slurm")
    return get_submitter()


def cancel_job(job_id: str, project_id: str | None = None) -> bool:
    """Cancel a queued or running job. Returns True if it was actionable. Fires
    the job's CancelToken so the shared exec core killpg's the whole process
    group (forked children die too), matching the synchronous Stop path."""
    job = get_job(job_id, project_id=project_id)
    if not job:
        return False
    if job["status"] in ("done", "failed", "cancelled"):
        return False
    # Persist the cancel INTENT before stopping execution. If ABA dies between the
    # scancel and the status write, on restart the poll loop would see sacct=CANCELLED
    # with no result.json → slurm_terminal_fail → and would otherwise AUTO-RESUME a job
    # the user cancelled. The marker lets _maybe_resume_nextflow_job refuse (see there).
    update_job(job_id, project_id=project_id,
               params={**(job.get("params") or {}), "cancel_requested": True})
    # The submitter that OWNS this job stops the actual execution — CancelToken+killpg
    # locally/inline, `scancel <id>` on Slurm. Must match how the job was submitted, not
    # the deployment default (an inline job on a slurm deploy would otherwise orphan).
    _submitter_for_job(job).cancel(job)
    update_job(job_id, project_id=project_id, status="cancelled", finished_at=_utcnow())
    # Resolve the parked deferred tool_use so the chat tool line settles (the endpoint
    # additionally fires the continuation to notify the agent). Idempotent.
    _settle_job_deferred(job_id, project_id)
    return True


def _write_workflow_exec_record_for_job(job: dict, result_obj: dict,
                                        lookup_pid: str | None, effective_pid: str) -> None:
    """Provenance for a Nextflow job: a kind:workflow exec record (engine + the
    reproducible `nextflow run …` command + pipeline/revision/profile/params +
    per-process container images + produced). Injects exec_id so the harvested
    outputs attach to it, exactly like the script path. Best-effort."""
    try:
        params = job.get("params") or {}
        wf = result_obj.get("workflow") or {}
        cwd = result_obj.get("cwd")
        from core.graph import exec_records as _er
        from core.exec.fingerprint import code_hash
        from core import projects as _projects
        produced: list[dict] = []
        from core.entity_types import registry
        groups = list(registry.artifact_groups().items())
        groups.append(("files", "file"))
        for grp, knd in groups:
            for i, a in enumerate(result_obj.get(grp) or []):
                if isinstance(a, dict):
                    produced.append({"kind": knd, "idx": i, "url": a.get("url"),
                                     "name": a.get("original_name") or a.get("name")})
                else:
                    produced.append({"kind": knd, "idx": i})
        inputs: list[dict] = []
        if job.get("focus_entity_id"):
            inputs.append({"ref": job["focus_entity_id"], "kind": "entity"})
        cmd_str = wf.get("command") or params.get("code", "")
        with _projects.bind(str(lookup_pid or effective_pid)):
            eid = _er.create(
                thread_id=str(params.get("thread_id") or "default"),
                run_id=params.get("run_id"),
                tool_use_id=None,
                tool_name="run_nextflow",
                status="ok",
                code=cmd_str,
                code_hash=code_hash(cmd_str),
                started_at=job.get("started_at") or _utcnow(),
                completed_at=_utcnow(),
                cwd=cwd,
                payload={
                    "kind": "workflow",
                    "engine": wf.get("engine") or {"name": "nextflow"},
                    "env": {"per_process_images": wf.get("per_process_images") or []},
                    "params": {"pipeline": wf.get("pipeline"), "revision": wf.get("revision"),
                               "profile": wf.get("profile"), "params": wf.get("params") or {}},
                    "inputs": inputs,
                    "produced": produced,
                    "outputs": (wf.get("outputs") or [])[:50],
                    "task_summary": wf.get("task_summary") or {},
                    "multiqc": wf.get("multiqc") or {},
                    "failure": wf.get("failure") or {},
                    "stdout_tail": (result_obj.get("stdout") or "")[-2000:],
                    "stderr_tail": (result_obj.get("stderr") or "")[-2000:],
                    "exit_code": result_obj.get("returncode"),
                },
            )
        if eid:
            result_obj["exec_id"] = eid
    except Exception as e:  # noqa: BLE001
        _record_worker_failure("exec_record_workflow", job.get("id"), e)


def _write_exec_record_for_job(job: dict, result_obj: dict,
                               lookup_pid: str | None, effective_pid: str) -> None:
    """Provenance (provenance.md Phase 1): a backgrounded/Slurm job writes the
    SAME exec record an interactive run does — code + env descriptor + produced +
    inputs + seed + kind — so its artifacts are revisable/reproducible. Injects
    `exec_id` into result_obj; _on_post_tool_register_artifacts then attaches it.
    Best-effort — never blocks completion."""
    try:
        params = job.get("params") or {}
        code = params.get("code", "")
        kind = job.get("kind") or "run_python"
        cwd = result_obj.get("cwd")
        if not cwd:
            return
        # Nextflow → a kind:workflow record (engine + reproducible command +
        # per-process container images + params), not a script record.
        if kind == "run_nextflow":
            _write_workflow_exec_record_for_job(job, result_obj, lookup_pid, effective_pid)
            return
        lang = "r" if kind == "run_r" else "python"
        from core.graph import exec_records as _er
        from core.exec.fingerprint import code_hash, env_fingerprint
        from core import projects as _projects
        pkg = result_obj.get("package_versions") or {}
        langver = result_obj.get("language_version") or ""
        # Match the interactive produced[] shape — pin_artifact needs the `url`.
        produced: list[dict] = []
        from core.entity_types import registry
        _groups = list(registry.artifact_groups().items())   # plots->figure, tables->table
        _groups.append(("files", "file"))                    # generic file artifacts (no registered type)
        for grp, knd in _groups:
            for i, a in enumerate(result_obj.get(grp) or []):
                if isinstance(a, dict):
                    produced.append({"kind": knd, "idx": i, "url": a.get("url"),
                                     "name": a.get("original_name") or a.get("name")})
                else:
                    produced.append({"kind": knd, "idx": i})
        inputs: list[dict] = []
        if job.get("focus_entity_id"):
            inputs.append({"ref": job["focus_entity_id"], "kind": "entity"})
        with _projects.bind(str(lookup_pid or effective_pid)):
            eid = _er.create(
                thread_id=str(params.get("thread_id") or "default"),
                run_id=params.get("run_id"),
                tool_use_id=None,
                tool_name=kind,
                status="ok",
                code=code,
                code_hash=code_hash(code),
                started_at=job.get("started_at") or _utcnow(),
                completed_at=_utcnow(),
                cwd=cwd,
                payload={
                    "executor": f"background:{lang}",
                    "kind": "script",
                    "language": lang,
                    "language_version": langver,
                    "package_versions": pkg,
                    "env_fingerprint": env_fingerprint(langver, pkg),
                    "seed": result_obj.get("seed"),
                    "inputs": inputs,
                    "produced": produced,
                    "stdout_tail": (result_obj.get("stdout") or "")[-2000:],
                    "stderr_tail": (result_obj.get("stderr") or "")[-2000:],
                    "exit_code": result_obj.get("returncode"),
                },
            )
        if eid:
            result_obj["exec_id"] = eid
    except Exception as e:  # noqa: BLE001
        _record_worker_failure("exec_record", job.get("id"), e)


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
        _settle_job_deferred(job_id, lookup_pid)
        return
    if "error" in result_obj:
        update_job(job_id, project_id=lookup_pid, status="failed",
                   error=result_obj["error"][:1000],
                   log_tail=result_obj["error"][:1500], finished_at=_utcnow())
        _settle_job_deferred(job_id, lookup_pid)
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
        _settle_job_deferred(job_id, lookup_pid)
        await _continue_after_failure(job_id, lookup_pid, effective_pid)
        return
    # Provenance: stamp the exec record + inject exec_id BEFORE registration, so
    # the produced artifacts attach to it (revisable like an interactive run).
    _write_exec_record_for_job(job, result_obj, lookup_pid, effective_pid)
    # Bind the project for the WHOLE registration. The Slurm poll loop and the
    # local worker call _finalize_job with NO project bound, so without this the
    # on_job_complete handler's get_entity/update_entity hit the schema-less
    # `_workspace` DB — refresh_output_manifest's get_entity raises, is swallowed,
    # and the Run is never updated. This is why background-job outputs never
    # attached to their Run (the live 2026-06-29 Seurat re-render dance).
    from core import projects as _projects
    with _projects.bind(str(effective_pid)):
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
    _settle_job_deferred(job_id, lookup_pid)
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
    # Honor the submitter's ceiling (sized from the agent's estimate); background
    # jobs are NOT clamped to the interactive 30-min cap — only the 24 h backstop.
    timeout_s = max(5, min(int(params.get("timeout_s") or BACKGROUND_DEFAULT_TIMEOUT_S),
                           BACKGROUND_MAX_TIMEOUT_S))
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
    # Execute UNDER the Run captured at submit (active_run_id), not the job's own
    # id — so artifacts land in the Run's work dir (which refresh_output_manifest
    # scans) and attach to the Run automatically, instead of being orphaned in a
    # job-scoped dir the agent then has to re-materialize. Falls back to job_id
    # when the submit had no open Run. stream=True tees output to a live job.log.
    exec_run_id = params.get("run_id") or job_id
    try:
        loop = asyncio.get_event_loop()
        if kind == "run_nextflow":
            from core.exec.nextflow import run_nextflow_code
            result_obj = await loop.run_in_executor(
                None,
                lambda: run_nextflow_code(
                    params.get("pipeline") or "", project_id=str(effective_pid),
                    run_id=exec_run_id, revision=params.get("revision"),
                    profile=params.get("profile"), params=params.get("nf_params") or {},
                    outdir=params.get("outdir"),
                    execution=params.get("execution"),          # inline head must use executor=local
                    local_resources=params.get("local_resources"),
                    timeout_s=timeout_s, cancel_token=token, stream=True),
            )
        elif kind == "run_r":
            result_obj = await loop.run_in_executor(
                None,
                lambda: run_r_code(code, project_id=str(effective_pid), run_id=exec_run_id,
                                   timeout_s=timeout_s, cancel_token=token,
                                   env=params.get("env"), stream=True),
            )
        else:
            result_obj = await loop.run_in_executor(
                None,
                lambda: run_python_code(code, project_id=str(effective_pid), run_id=exec_run_id,
                                        timeout_s=timeout_s, cancel_token=token,
                                        env=params.get("env"), stream=True),
            )

        await _finalize_job(job, result_obj, project_id, str(effective_pid))
    except Exception as e:  # noqa: BLE001
        # Surface the failure into the job row + the worker-failure log,
        # instead of silently swallowing it.
        try:
            update_job(job_id, project_id=project_id, status="failed",
                       error=f"worker exception: {type(e).__name__}: {e}"[:1000],
                       log_tail=traceback.format_exc()[-2000:],
                       finished_at=_utcnow())
        except Exception:  # noqa: BLE001
            pass
        _record_worker_failure("_run_one", job_id, e)
        # A worker-level crash (run_python_code itself threw — not a non-zero
        # exit) must STILL resume the agent's plan; otherwise the turn hangs
        # forever on a job that died with nobody notified. Fire the SAME failure
        # continuation a result-level failure uses (it was wired only into
        # _finalize_job's paths, so an exception here bypassed it entirely).
        try:
            await _continue_after_failure(job_id, project_id, str(effective_pid))
        except Exception as ce:  # noqa: BLE001
            _record_worker_failure("_run_one/continue", job_id, ce)
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
    reaped_targets: list[tuple[str, str]] = []  # (job_id, project_id) — settle their deferred turns
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
            reaped_targets.extend((jid, pid) for jid in reap_ids)
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

    # Recovery: resolve each dropped (orphaned-running) job's parked deferred tool_use so a
    # crash doesn't leave its chat tool line spinning forever. Done AFTER the per-project
    # sqlite transactions close (settle opens its own project-scoped connection). Idempotent.
    for jid, pid in reaped_targets:
        _settle_job_deferred(jid, pid)

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


# A Nextflow head's wall-clock lifetime = task compute + the UNPREDICTABLE time its
# task jobs spend queued, so an infrastructure kill (walltime/node-fail/preempt) is
# expected on a busy cluster — not a pipeline error. We auto-resume it (same run_id →
# same work-dir → `nextflow -resume` skips finished tasks), capped so a head that
# can't make progress eventually fails instead of looping forever.
_NF_MAX_RESUMES = 3


def _reset_nextflow_run_dir(job: dict) -> None:
    """Clear the dead head's CONTROL sentinels (done/result.json in the job dir) so
    the next poll doesn't read stale state. Leaves the Nextflow work-dir (a separate
    run_id-keyed dir) untouched — that IS the -resume cache."""
    try:
        from core.data.workspace import scratch_dir
        pid = (job.get("params") or {}).get("project_id") or "default"
        rd = scratch_dir(str(pid), job["id"])
        for f in ("done", "result.json"):
            p = rd / f
            if p.exists():
                p.unlink()
    except Exception:  # noqa: BLE001
        pass


def _maybe_resume_nextflow_job(sub, job: dict, result: dict, pid: str | None) -> bool:
    """If a Nextflow head died at the Slurm level (no result written) and we're
    under the resume cap, re-submit the SAME job (→ same work-dir → `-resume`) and
    return True (handled — don't finalize as failed). Otherwise False."""
    if (job.get("kind") != "run_nextflow" or not isinstance(result, dict)
            or not result.get("slurm_terminal_fail")):
        return False
    params = job.get("params") or {}
    # Never resurrect a job the user cancelled: a scancel looks exactly like an infra
    # kill to the poll loop (sacct terminal + no result.json), so without this a cancel
    # that raced a crash would auto-resume. cancel_job persists this before the scancel.
    if params.get("cancel_requested"):
        return False
    resumes = int(params.get("nf_resumes") or 0)
    st = result.get("slurm_terminal_fail")
    if resumes >= _NF_MAX_RESUMES:
        result["error"] = (f"{result.get('error') or 'slurm head died'} — gave up after "
                           f"{_NF_MAX_RESUMES} auto-resume attempts.")
        return False
    update_job(job["id"], project_id=pid, status="queued", error=None,
               params={**params, "nf_resumes": resumes + 1, "slurm_id": None})
    fresh = get_job(job["id"], project_id=pid) or {**job,
            "params": {**params, "nf_resumes": resumes + 1, "slurm_id": None}}
    _reset_nextflow_run_dir(fresh)
    try:
        sub.submit(fresh)
    except Exception as e:  # noqa: BLE001
        _record_worker_failure("nextflow-resume", job.get("id"), e)
        return False
    print(f"[jobs.slurm] nextflow head {job['id']} died ({st}); auto-resumed "
          f"(attempt {resumes + 1}/{_NF_MAX_RESUMES}, -resume from the work-dir)", flush=True)
    return True


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
                    # Auto-resume a Nextflow head killed by Slurm (walltime/node-fail)
                    # before it could finish — re-submit with -resume instead of failing.
                    if _maybe_resume_nextflow_job(sub, job, result, pid):
                        continue
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
