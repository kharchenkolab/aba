"""BatchSubmitter — *where* a background job actually runs (ondemand.md P6).

ABA's background jobs (run_python/run_r with background=True) are dispatched
through a BatchSubmitter so the placement is swappable:

  - ``LocalSubmitter`` (default): the in-process async worker on THIS node —
    today's behavior, unchanged.
  - ``SlurmSubmitter``: ``sbatch`` onto the cluster, with a shared-filesystem
    sentinel for completion (no callbacks/webhooks). ABA itself runs as a Slurm
    job on a compute node and submits further jobs from there.

Selection: ``ABA_BATCH_SUBMITTER=local|slurm`` (default ``local``); the OOD
``before.sh`` sets ``slurm`` on a cluster deployment.

The job ROW (core/graph/jobs) is created the same way for every submitter; the
submitter only decides how it RUNS and how its status/cancel/monitoring resolve.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from core import config


@runtime_checkable
class BatchSubmitter(Protocol):
    name: str

    def submit(self, job: dict) -> None:
        """Dispatch a created job row for execution. May annotate the row
        (e.g. write the scheduler id into params) via update_job."""

    def cancel(self, job: dict) -> None:
        """Stop a queued/running job (CancelToken locally; ``scancel`` on Slurm).
        The caller marks the row cancelled afterward."""

    def poll(self, job: dict) -> Optional[dict]:
        """For jobs that run EXTERNALLY (Slurm): return a result-shaped dict once
        the job has terminated, so the poll loop can finalize it (harvest +
        continuation). Returns None while still running. LocalSubmitter always
        returns None — the in-process worker owns its jobs' lifecycle."""

    def info(self, job: dict) -> dict:
        """Live scheduler info for the (i) monitor (Slurm: squeue/sacct fields:
        scheduler id, state, node, elapsed, cores, mem). Cheap/best-effort."""


def submitter_name() -> str:
    return config.settings.batch_submitter.get().strip().lower()


def _local_lane() -> "BatchSubmitter":
    """The local background lane (weft rewrite W2): a bare weft task when the
    compute substrate is up — durable across restarts, placement-bearing exec
    records — else the legacy in-process worker, loudly (transparent
    degradation, never silent)."""
    from core.jobs.weft_submitter import WeftSubmitter, weft_available
    if weft_available():
        return WeftSubmitter()
    print("[jobs] compute substrate offline — background job falls back to the "
          "in-process worker (no durable state across restarts)")
    from core.jobs.runner import LocalSubmitter
    return LocalSubmitter()


def _slurm_lane(kind: str | None = None) -> "BatchSubmitter":
    """The cluster lane (W3.3): a weft task on the deployment's slurm-kind
    site when one is declared (weft-sites.yaml) and the substrate is up.
    Nextflow heads ride the SAME bare weft task: the command
    `python -m core.jobs.slurm_entry` dispatches `run_nextflow` on the node
    (slurm_entry.py), the node runs the head over the shared FS (host-by-default),
    and WeftSubmitter already forwards the nextflow spec + routes resume by
    weft_id (runner.py). `kind` is retained for the call interface.

    Weft-only (W3.4 tail): the legacy sbatch lane is GONE. A cluster deployment
    declares a slurm-kind weft site (`host:` omitted = local transport on the
    submit node); with none declared we degrade to the LOCAL weft lane so jobs
    still run (on this node) — never a hard failure, never sbatch."""
    del kind  # nextflow no longer special-cased — same lane as python/R
    from core.jobs.weft_submitter import WeftSubmitter, weft_slurm_site
    site = weft_slurm_site()
    if site:
        return WeftSubmitter(site=site)
    print("[jobs] ABA_BATCH_SUBMITTER=slurm but no slurm-kind weft site declared "
          "(weft-sites.yaml) — running background jobs on the LOCAL weft lane")
    return _local_lane()


def get_submitter(kind: str | None = None) -> "BatchSubmitter":
    """The active submitter for this deployment. Lazy imports avoid a cycle
    (runner ⇆ submitter) and keep Slurm code off the import path when local.
    `kind` (job kind) routes nextflow heads to their special-cased lane."""
    name = submitter_name()
    if name == "slurm":
        return _slurm_lane(kind)
    if name == "worker":     # explicit legacy escape hatch (in-process worker)
        from core.jobs.runner import LocalSubmitter
        return LocalSubmitter()
    return _local_lane()


def get_submitter_for(target: str, kind: str | None = None) -> "BatchSubmitter":
    """Submitter for a per-job submission target (see in-place submission,
    misc/inplace_submission.md): 'inline' → the local lane (run the job on THIS
    node — a bare weft task, or the in-process worker when the substrate is
    offline); 'slurm' → the cluster lane (a weft task on the declared slurm
    site, else legacy sbatch). Anything else → the deployment default. This is
    the seam that lets a small job run in-place even when the deployment
    default is Slurm."""
    if target == "inline":
        return _local_lane()
    if target == "slurm":
        return _slurm_lane(kind)
    return get_submitter(kind)
