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


def _cap_mem_gb(val) -> float | None:
    """Parse a squeue %m / SLURM mem string ('4G', '16000M', '32000') → GB float."""
    if not val:
        return None
    import re
    m = re.match(r"([\d.]+)\s*([KMGTP]?)", str(val).strip().upper())
    if not m:
        return None
    n = float(m.group(1))
    return round(n * {"K": 1e-6, "M": 1e-3, "G": 1.0, "T": 1000.0, "P": 1e6, "": 1e-3}.get(m.group(2), 1e-3), 3)


def aba_allocation_capacity() -> dict:
    """ABA's own schedulable capacity — for deciding whether a background job can run
    IN-PLACE (a subprocess in ABA's allocation) instead of via `sbatch`.

      inline_ok — safe to run heavy work here: True for a local submitter (ABA *is* the
                  compute) or a Slurm submitter while ABA itself is in an allocation; False on
                  a bare login node under a Slurm submitter (never hammer the login node).
      cores / mem_gb — the allocation's size (mem_gb may be None if the scheduler didn't report it).
    """
    from core.jobs.submitter import submitter_name
    s = session_allocation()
    submitter = submitter_name()
    inline_ok = (submitter != "slurm") or bool(s.get("on_slurm"))
    try:
        cores = int(str(s.get("alloc_cores") or s.get("cores") or 1).split()[0])
    except (ValueError, IndexError):
        cores = int(s.get("cores") or 1)
    used = 0.0
    try:
        from core.jobs.runner import _running_inline_cores
        used = _running_inline_cores()          # don't oversubscribe with concurrent inline jobs
    except Exception:  # noqa: BLE001
        used = 0.0
    try:
        from core.exec.compute_env import node_gpus as _node_gpus
        gpus = _node_gpus()                      # GPUs visible here = usable by an inline job
    except Exception:  # noqa: BLE001
        gpus = 0
    return {"inline_ok": inline_ok, "cores": max(1, cores),
            "mem_gb": _cap_mem_gb(s.get("alloc_mem")), "inline_used_cores": used,
            "gpus": gpus, "submitter": submitter, "on_slurm": bool(s.get("on_slurm"))}


def job_hpc_info(job: dict) -> dict:
    """Live scheduler info for ONE job (for the Jobs tab). Reads it from the
    submitter the job RECORDS it ran under: a weft-lane job (the cluster path) →
    WeftSubmitter().info (weft task state/node/placement); else a local in-process
    job. (The legacy sbatch lane is retired — an old `submitter=='slurm'` record
    just reads as local now.)"""
    submitter = (job.get("params") or {}).get("submitter")
    if submitter == "weft":
        from core.jobs.weft_submitter import WeftSubmitter
        return WeftSubmitter().info(job)
    return {"submitter": "local"}
