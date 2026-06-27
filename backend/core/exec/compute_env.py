"""ComputeEnv — where can we run, and with what?

The routing-oriented description the ExecutionRouter and the `describe_compute`
tool both read: the LOCAL node (allocation-aware cores/mem/GPU + remaining
walltime) and, on a cluster, the live Slurm submission landscape (partitions +
load) — falling back to the deployment-configured catalog when live queries
aren't reachable. Mirrors core.exec.cpu's "size to the allocation, not the
hardware" for memory + GPU + time.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional


def _cgroup_mem_limit_gb() -> Optional[float]:
    """Memory ceiling from the cgroup (v2 memory.max / v1 limit_in_bytes), or
    None when unlimited/unreadable."""
    for p in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            raw = Path(p).read_text().strip()
            if raw and raw != "max":
                b = int(raw)
                if 0 < b < (1 << 62):          # huge value == the "unlimited" sentinel
                    return b / (1024 ** 3)
        except (OSError, ValueError):
            pass
    return None


def _slurm_mem_gb() -> Optional[float]:
    v = os.environ.get("SLURM_MEM_PER_NODE", "").strip().rstrip("M")
    if v.isdigit():
        return int(v) / 1024
    pc = os.environ.get("SLURM_MEM_PER_CPU", "").strip().rstrip("M")
    if pc.isdigit():
        from core.exec.cpu import effective_cpu_count
        return int(pc) * effective_cpu_count() / 1024
    return None


def effective_mem_gb() -> float:
    """RAM usable by this process: the allocation (cgroup / Slurm) if any, else
    host total. The memory mirror of cpu.effective_cpu_count()."""
    cands = [c for c in (_cgroup_mem_limit_gb(), _slurm_mem_gb()) if c]
    if cands:
        return round(min(cands), 1)
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal:"):
                return round(int(line.split()[1]) / (1024 ** 2), 1)   # kB → GB
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def node_gpus() -> int:
    """GPUs visible to this process (nvidia-smi honors CUDA_VISIBLE_DEVICES /
    cgroup) — what a LOCAL run could actually use. 0 if none / no driver."""
    try:
        p = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0:
            return sum(1 for ln in p.stdout.splitlines() if ln.strip().startswith("GPU "))
    except Exception:  # noqa: BLE001
        pass
    return 0


_SLURM_TIME_RE = re.compile(r"(?:(\d+)-)?(\d+):(\d+)(?::(\d+))?$")


def slurm_time_to_min(s: Optional[str]) -> Optional[float]:
    """Parse a Slurm duration (D-HH:MM:SS / HH:MM:SS / MM:SS) → minutes. None for
    UNLIMITED / INVALID / unparseable (treated as unbounded)."""
    s = (s or "").strip()
    if not s or s.upper() in ("UNLIMITED", "INFINITE", "INVALID", "NOT_SET", "N/A"):
        return None
    m = _SLURM_TIME_RE.match(s)
    if not m:
        return None
    d, a, b, c = m.groups()
    if c is not None:                      # D-HH:MM:SS or HH:MM:SS
        return int(d or 0) * 1440 + int(a) * 60 + int(b) + int(c) / 60.0
    return int(a) + int(b) / 60.0          # MM:SS


def compute_env() -> dict:
    """The current compute picture for routing + planning. Always describes the
    local node; on a cluster (or wherever Slurm is reachable) adds the live
    partition landscape, with the deployment-config catalog as the fallback."""
    from core.exec.cpu import effective_cpu_count
    from core.exec.hpc_session import session_allocation
    from core.jobs.submitter import submitter_name
    from core.jobs import slurm_live as sl

    alloc = session_allocation()
    walltime_min = slurm_time_to_min(alloc.get("time_left")) if alloc.get("on_slurm") else None

    env: dict = {
        "mode": submitter_name(),                       # "local" | "slurm" (dispatch target)
        "on_slurm": bool(alloc.get("on_slurm")),        # is ABA itself in a Slurm allocation
        "node_cores": effective_cpu_count(),
        "node_mem_gb": effective_mem_gb(),
        "node_gpus": node_gpus(),
        "walltime_remaining_min": walltime_min,         # None = unbounded (pure local)
    }
    # Surface the Slurm landscape whenever it's reachable (so the agent can see
    # what it could submit to), regardless of the configured dispatch mode.
    if env["mode"] == "slurm" or sl.slurm_available():
        live = sl.partitions_live()
        if live:
            q = sl.queue_depth()
            for p in live:
                p["wait"] = sl.wait_label(p, q)
            env["partitions"] = live
            env["partitions_source"] = "live"
        else:
            try:
                from core.jobs.hpc_config import hpc_config
                env["partitions"] = hpc_config().get("partitions") or []
                env["partitions_source"] = "config"
            except Exception:  # noqa: BLE001
                env["partitions"], env["partitions_source"] = [], "none"
        env["user_access"] = sl.user_access()
    return env
