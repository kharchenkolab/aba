"""HPC session reporting for the (i) drawer (ondemand.md P6, Q3).

``session_allocation()`` describes the compute the ABA process ITSELF is running
on: a Slurm job's node / cores / walltime-left when on a cluster, else the local
CPU picture. Distinct from sizing (core.exec.cpu) — this is for the monitor card,
so the user sees "where am I running and with what".
"""
from __future__ import annotations

import os
import subprocess
from typing import Optional


def _slurm_job_id() -> Optional[str]:
    return os.environ.get("SLURM_JOB_ID") or os.environ.get("SLURM_JOBID") or None


def session_allocation() -> dict:
    """The ABA process' own allocation. Always returns ``cores``/``thread_cap``
    (the numbers kernels are actually sized to); adds Slurm node/walltime/partition
    when running as a Slurm job."""
    from core.exec.cpu import effective_cpu_count, default_thread_cap
    from core.jobs.submitter import submitter_name

    out: dict = {
        "submitter": submitter_name(),
        "on_slurm": False,
        "cores": effective_cpu_count(),
        "thread_cap": default_thread_cap(),
    }
    jid = _slurm_job_id()
    if not jid:
        return out
    out["on_slurm"] = True
    out["slurm_job_id"] = jid
    try:
        # %N node, %L time left, %P partition, %C cores, %m min-mem, %M time used
        p = subprocess.run(["squeue", "-j", jid, "-h", "-o", "%N|%L|%P|%C|%m|%M"],
                           capture_output=True, text=True, timeout=10)
        line = (p.stdout or "").strip()
        if line:
            f = line.split("|")
            keys = ["node", "time_left", "partition", "alloc_cores", "alloc_mem", "elapsed"]
            out.update({k: (f[i] if i < len(f) and f[i] else None) for i, k in enumerate(keys)})
    except Exception:  # noqa: BLE001
        pass
    # Env fallbacks (present even if squeue is unavailable from the node).
    out.setdefault("alloc_cores", os.environ.get("SLURM_CPUS_ON_NODE"))
    return out


def job_hpc_info(job: dict) -> dict:
    """Live scheduler info for ONE job (for the Jobs tab). Uses the SlurmSubmitter
    regardless of the active submitter, since the job records how it was run."""
    if (job.get("params") or {}).get("submitter") == "slurm":
        from core.jobs.slurm_submitter import SlurmSubmitter
        return SlurmSubmitter().info(job)
    return {"submitter": "local"}
