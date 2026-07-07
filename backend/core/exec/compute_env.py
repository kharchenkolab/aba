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
    # POSIX fallback (macOS has no /proc/meminfo) — host physical RAM via sysconf.
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages > 0 and page_size > 0:
            return round(pages * page_size / (1024 ** 3), 1)
    except (ValueError, OSError, AttributeError):
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


def context_line() -> str:
    """A compact per-turn line for the agent's dynamic system block: the compute
    mode + this node's capacity, and ON A CLUSTER the live Slurm landscape (the
    auto-surfaced planning cue — cheap, so it rides every turn; describe_compute
    gives full detail on demand). Empty string on any error (never break a turn)."""
    try:
        e = compute_env()                                # 20s-cached
        gpu = f", {e['node_gpus']} GPU" if e.get("node_gpus") else ""
        line = (f"Compute environment: {e.get('mode', 'local')} — this node "
                f"{e.get('node_cores')} cores / {e.get('node_mem_gb')} GB{gpu}")
        # Accelerator readiness — only surfaced when a GPU is in the picture. Tells the
        # agent whether a GPU step will actually accelerate or silently fall back to CPU.
        if "gpu_usable" in e:
            line += (". GPU usable (CUDA stack)" if e["gpu_usable"]
                     else f". WARNING: GPU present but NOT usable — {e.get('gpu_usable_reason', '')}; "
                          "a GPU step runs on CPU, so prefer CPU sizing or tell the user")
        wt = e.get("walltime_remaining_min")
        if wt is not None:
            line += f", ~{round(wt / 60, 1)}h walltime left"
        parts = e.get("partitions") or []
        if parts:
            line += ". Slurm available — partitions: " + "; ".join(
                f"{p['partition']} (<={p.get('cpus_per_node', '?')}c/node"
                + (",GPU" if p.get("gpu") else "")
                + f", {p.get('wait', '?')})" for p in parts[:6])
            line += (". For a heavy / parallel / GPU / long step, weigh Slurm vs local "
                     "(call describe_compute); a background/Slurm job is a FRESH process — "
                     "load inputs from disk, don't rely on kernel state.")
        else:
            line += (". Long cells run interactively (raise timeout_s if needed); use "
                     "background=True only to parallelize independent jobs or when the user "
                     "asks — it's a fresh process with no kernel state.")
        # tool_library Phase 1 (opt-in): the discovery cue for the in-kernel `aba`
        # read library — a per-turn line that reaches every model tier uniformly
        # (unlike a tool description, which lean tiers trim). Only when the flag is on.
        if os.environ.get("ABA_TOOL_LIB"):
            line += (". Entity graph: an `aba` object is in the run_python/run_r kernel. "
                     "READ with aba.find(type=…, contains=…) / aba.get(id) / aba.types() "
                     "(find() returns a LIST in one call — don't loop it). WRITE with "
                     "aba.create(type, title, **fields) / aba.relate(src, rel, dst) / "
                     "aba.update(id, **fields) (applied after the cell, provenance-stamped). "
                     "BATCH: do several aba ops in ONE run_python cell — read what you need, "
                     "compute, then create/update/relate together — instead of one op per cell; "
                     "and don't re-read state you already have. "
                     "aba.help() for the full reference, aba.ops(type) for a type's fields/edges.")
        return line
    except Exception:  # noqa: BLE001
        return ""


_CACHE: dict = {"ts": 0.0, "env": None}


def compute_env(ttl: float = 20.0) -> dict:
    """The current compute picture for routing + planning. Cached for `ttl`
    seconds (sinfo/squeue run at most once per window) so the router can call it
    on every run_python/run_r without re-querying the scheduler each cell. Pass
    ttl=0 for a fresh read (describe_compute does, since the agent wants current
    load). Local mode never touches Slurm, so it's cheap regardless."""
    import time
    now = time.time()
    if ttl and _CACHE["env"] is not None and (now - _CACHE["ts"]) < ttl:
        return _CACHE["env"]
    env = _build_compute_env()
    _CACHE.update(ts=now, env=env)
    return env


def _build_compute_env() -> dict:
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
    # Accelerator readiness: is a GPU both PRESENT and USABLE by our stack? A GPU node
    # is useless if the base torch is CPU-only (the scVI-on-CPU incident: right node,
    # idle GPU). Present = a local GPU or a gpu partition; usable = torch is a CUDA
    # build (node-independent — see torch_cuda_build). The agent weighs gpu_usable, not
    # just "a GPU exists," when placing a GPU step.
    gpu_present = bool(env["node_gpus"]) or any(p.get("gpu") for p in env.get("partitions") or [])
    if gpu_present:
        from core.exec.env_integrity import torch_cuda_build
        _cuda = torch_cuda_build()
        env["gpu_usable"] = _cuda is not None
        env["gpu_usable_reason"] = (
            f"CUDA torch ({_cuda})" if _cuda else
            "base torch is CPU-only — a GPU step would fall back to CPU (admin: set "
            "ABA_ACCELERATOR=cuda in config.env + rebuild the env)")
    return env


# ── Capability profile: which agent tools can actually run here ──────────────
# A fast, which()-based snapshot (no live sinfo/hpc_config — safe in the skill-
# discovery hot path). It answers "does a recipe's declared `requires_tools`
# resolve in THIS environment?", so discovery can gate/flag recipes needing a
# tool the machine can't run. Today the only environment-hard tool is
# `run_nextflow` (nf-core pipelines need a container engine or a cluster);
# run_python/run_r are always viable in ABA's stack. Cached per process
# (capabilities don't change mid-run); pass refresh=True (tests) to recompute.
_CONTAINER_ENGINES = ("docker", "singularity", "apptainer", "podman",
                      "charliecloud", "shifter", "sarus")
_ENV_PROFILE: Optional[dict] = None


def _build_env_profile() -> dict:
    import shutil
    engines = [e for e in _CONTAINER_ENGINES if shutil.which(e)]
    cluster = bool(shutil.which("sbatch") or shutil.which("sinfo"))
    nextflow = bool(shutil.which("nextflow")
                    or os.environ.get("ABA_NEXTFLOW_BIN")
                    or os.environ.get("ABA_NEXTFLOW_MODULE"))
    # nf-core needs a software backend (container engine) OR a cluster to run for
    # real; a bare `nextflow` binary with neither is not a real pipeline env.
    run_nextflow = nextflow and (bool(engines) or cluster)
    return {
        "run_python": True,
        "run_r": True,                 # R is part of ABA's standard tools env
        "run_nextflow": run_nextflow,
        "nextflow_present": nextflow,
        "container_engines": engines,
        "cluster": cluster,
        "gpu": bool(shutil.which("nvidia-smi")),
    }


def env_profile(*, refresh: bool = False) -> dict:
    """Cached capability snapshot of this runtime (see _build_env_profile)."""
    global _ENV_PROFILE
    if _ENV_PROFILE is None or refresh:
        _ENV_PROFILE = _build_env_profile()
    return _ENV_PROFILE


def tool_viable(tool: str, profile: Optional[dict] = None) -> bool:
    """Can this agent tool actually run here? Only `run_nextflow` is
    environment-hard today; run_python/run_r and any unmodeled tool are assumed
    viable so discovery never over-gates on a tool we don't understand."""
    prof = profile if profile is not None else env_profile()
    if tool == "run_nextflow":
        return bool(prof.get("run_nextflow"))
    return True
