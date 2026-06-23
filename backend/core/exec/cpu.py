"""CPU-allocation awareness for sizing BLAS/OMP thread pools.

The host core count (``os.cpu_count()`` / ``/proc/cpuinfo``) is the WRONG number
to size a kernel's BLAS/OMP thread pool to. On a scheduler-managed node (Slurm,
k8s, …) the process is *allocated* a small slice of a big machine, but OpenBLAS,
OpenMP, MKL, etc. still detect every host core and spawn one thread per core. On
a node with a per-user process ceiling (RLIMIT_NPROC) that pegs
``pthread_create`` to ``EAGAIN`` ("Resource temporarily unavailable" /
"can't start new thread") and kills the kernel — even though only 1 CPU was
allocated. (Observed live on an OnDemand Slurm node: ``SLURM_CPUS_ON_NODE=1`` but
``Cpus_allowed_list: 0-55`` → OpenBLAS tried 56 threads and ``IRkernel::installspec``
died at "thread 6 of 56".)

So size thread pools to the ALLOCATION, not the hardware.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

# When NOTHING allocates CPUs for us (unscheduled workstation / bare SIF run on a
# fat box), cap BLAS/OMP threads here: more than this on typical bio matrices is
# slower (oversubscription) and collides with data-loader workers. This cap is
# NOT applied when there's an explicit allocation — a Heavy node allocated 16
# cores gets 16 BLAS threads (the operator asked for them).
_UNSCHEDULED_CAP = 8
# Absolute guard against a pathological allocation value spawning insane thread
# counts (e.g. a mis-set SLURM var). Real allocations are far below this.
_HARD_CEILING = 128

_THREAD_ENV_VARS = (
    "OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS", "BLIS_NUM_THREADS",
)


def _cgroup_cpu_quota() -> int | None:
    """CPUs implied by the cgroup CPU quota (v2 ``cpu.max``, else v1 cfs quota).
    ``None`` when unlimited or unreadable."""
    try:  # cgroup v2
        raw = Path("/sys/fs/cgroup/cpu.max").read_text().split()
        if raw and raw[0] != "max":
            quota = int(raw[0])
            period = int(raw[1]) if len(raw) > 1 else 100000
            if period > 0:
                return max(1, math.ceil(quota / period))
    except (OSError, ValueError):
        pass
    try:  # cgroup v1
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text())
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text())
        if quota > 0 and period > 0:
            return max(1, math.ceil(quota / period))
    except (OSError, ValueError):
        pass
    return None


def _affinity_cpus() -> int:
    """CPUs this process may actually run on (cpuset affinity mask), else the
    host core count."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def _allocation_cpus() -> int | None:
    """CPUs from an EXPLICIT allocation signal — an operator override
    (``ABA_CPU_LIMIT``), the Slurm allocation (``SLURM_CPUS_PER_TASK`` /
    ``SLURM_CPUS_ON_NODE``), or the cgroup CPU quota. ``None`` when nothing
    explicitly allocates CPUs for us (an unscheduled box). The minimum wins when
    several disagree."""
    cands: list[int] = []
    ovr = os.environ.get("ABA_CPU_LIMIT", "").strip()
    if ovr.isdigit():
        cands.append(int(ovr))
    for var in ("SLURM_CPUS_PER_TASK", "SLURM_CPUS_ON_NODE"):
        v = os.environ.get(var, "").strip()
        if v.isdigit():
            cands.append(int(v))
    q = _cgroup_cpu_quota()
    if q:
        cands.append(q)
    cands = [c for c in cands if c and c > 0]
    return min(cands) if cands else None


def effective_cpu_count() -> int:
    """CPUs actually usable by this process: the explicit allocation if any
    (never more than the affinity mask permits), else the affinity mask. This is
    the *allocation*, not the host core count — on a Slurm node allocated 4 of 56
    CPUs it returns 4."""
    aff = _affinity_cpus()
    alloc = _allocation_cpus()
    return aff if alloc is None else max(1, min(alloc, aff))


def default_thread_cap() -> int:
    """Threads to give BLAS/OMP pools.

    - ``ABA_KERNEL_THREADS`` overrides everything (explicit operator intent).
    - With an explicit CPU allocation (Slurm/cgroup/``ABA_CPU_LIMIT``) we honor
      it IN FULL — a Heavy node allocated 16 cores gets 16 BLAS threads — bounded
      only by the affinity mask and a pathological-value guard. This is what
      makes multicore production nodes use the cores they were given.
    - With NO allocation signal (unscheduled workstation / bare SIF on a fat
      box) we fall back to ``min(cpu, 8)`` so small matrices don't oversubscribe.

    The failure this prevents: on a node allocated 1 of 56 CPUs, an uncapped
    OpenBLAS spawns 56 threads per kernel and dies on the per-user process
    limit (pthread EAGAIN)."""
    ovr = os.environ.get("ABA_KERNEL_THREADS", "").strip()
    if ovr.isdigit() and int(ovr) > 0:
        return int(ovr)
    aff = _affinity_cpus()
    alloc = _allocation_cpus()
    if alloc is not None:
        return max(1, min(alloc, aff, _HARD_CEILING))
    return max(1, min(aff, _UNSCHEDULED_CAP))


def pin_blas_threads() -> int:
    """Set the BLAS/OMP thread-pool env vars process-wide so EVERY child the
    backend later spawns (Jupyter/IR kernels, ``IRkernel::installspec``,
    micromamba, ``Rscript``) inherits a sane cap instead of spawning one thread
    per host core. Uses ``setdefault`` so an operator/launch-script value is
    respected. Idempotent. Returns the cap applied.

    Call once at backend startup, before numpy/torch import in-process."""
    n = str(default_thread_cap())
    for var in _THREAD_ENV_VARS:
        os.environ.setdefault(var, n)
    return int(n)
