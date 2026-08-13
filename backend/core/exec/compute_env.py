"""ComputeEnv — where can we run, and with what?

The routing-oriented description the ExecutionRouter and the `describe_compute`
tool both read: the LOCAL node (allocation-aware cores/mem/GPU + remaining
walltime) and, when a slurm-kind weft site is declared, the live cluster
submission landscape (partitions + load) read through the weft SitePort —
falling back to the deployment-configured catalog when live queries aren't
reachable. Mirrors core.exec.cpu's "size to the allocation, not the hardware"
for memory + GPU + time.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from core import config


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
            # The usable branch carries its reason too: on a jobs.gpu_env_pack
            # site the reason IS the instruction (batch lane only, this node is
            # CPU) — "GPU usable" alone would invite an interactive GPU step.
            line += ((f". GPU usable — {e['gpu_usable_reason']}"
                      if e.get("gpu_usable_reason") else ". GPU usable (CUDA stack)")
                     if e["gpu_usable"]
                     else f". WARNING: GPU present but NOT usable — {e.get('gpu_usable_reason', '')}; "
                          "a GPU step runs on CPU, so prefer CPU sizing or tell the user")
        wt = e.get("walltime_remaining_min")
        if wt is not None:
            line += f", ~{round(wt / 60, 1)}h walltime left"
            # The clock alone does not tell the agent whether DEFERRING saves the
            # work — a different question whose answer flips with the dispatch
            # mode. Seen in the placement study: with 20 min left and no cluster,
            # the agent refused to start an hour of work and then offered the
            # "background" lane, which on local mode is this same node, dying with
            # the session.
            # The behaviour rule (system_bundle/rules/behavior.md, "The session
            # clock matters only when the work approaches it") keys on this fact,
            # so the two ship together — that pair is what was measured. Rendered
            # only when a clock exists, so an unbounded local install is untouched.
            line += (" — the session ends then and anything still running in it dies. "
                     + ("A background job is dispatched to the cluster and OUTLIVES the "
                        "session, so its results are recoverable from a later one"
                        if e.get("mode") == "slurm" else
                        "Background jobs die with it too (they run on this node), so "
                        "deferring does NOT rescue work that will not fit: say so, and "
                        "offer a longer session or a smaller run"))
        # The 30-min interactive cap is a HARD property of this lane, not a judgement a
        # scheduler can change, so it renders either way. It used to sit only on the
        # no-partitions branch — meaning on a real cluster, the one case where a long
        # step is actually at stake, the cue offered "weigh Slurm vs local" and never
        # mentioned the limit that settles the question.
        line += (". Interactive cells are capped at 30 min (a larger timeout_s is "
                 "clamped); anything longer must use background=True, a fresh "
                 "process with no kernel state")
        parts = e.get("partitions") or []
        if parts:
            line += ". Slurm available — partitions: " + "; ".join(
                f"{p['partition']} (<={p.get('cpus_per_node', '?')}c/node"
                + (",GPU" if p.get("gpu") else "")
                + f", {p.get('wait', '?')})" for p in parts[:6])
            line += (". For a heavy / parallel / GPU / long step, weigh Slurm vs local "
                     "(call describe_compute); a background/Slurm job is a FRESH process — "
                     "load inputs from disk, don't rely on kernel state")
        remotes = e.get("remote_sites") or []
        if remotes:
            line += (f". Remote machines available: {', '.join(remotes)} — run a step there "
                     f"with run_python/run_r site=<name> (prefer the machine holding the "
                     f"inputs; a synchronous site= step runs in a PERSISTENT session there, "
                     f"state persists between site= calls; describe_compute for capacity).")
        # This project's named isolated envs — the fresh-thread rediscovery cue.
        # REGISTRY-ONLY: this line renders every turn, so NO substrate/adapter call
        # here (env_status et al. belong in inspect_env). Empty registry → no clause
        # (byte-identical line to today). Lazy import; never break a turn.
        try:
            from core.compute import named_envs as _ne
            from core import projects as _proj
            _pid = _proj.current()
            _names = _ne.list_names(_pid) if _pid else []
            if _names:
                _active = {"python": _ne.get_active(_pid, "python"),
                           "r": _ne.get_active(_pid, "r")}
                _items = []
                for _n in _names:
                    _row = _ne.resolve(_pid, _n) or {}
                    _lang = _row.get("language") or "python"
                    _tag = "r" if _lang == "r" else "py"
                    if _n == _active.get(_lang):     # * marks the active env
                        _tag += "*"
                    _pk = list(_row.get("packages") or [])
                    _shown = "+".join(_pk[:2]) + (f"+{len(_pk) - 2}" if len(_pk) > 2 else "")
                    _items.append(f"{_n} ({_tag}, {_shown})" if _shown else f"{_n} ({_tag})")
                line += ("; named envs: " + ", ".join(_items) + " — inspect_env() for detail")
        except Exception:  # noqa: BLE001 — the clause must never break a turn
            pass
        # The line is assembled by appending clauses, most of which open with ". ".
        # Clauses that also CLOSED with "." rendered ".." at four of the six live
        # env shapes. Normalising once here is the property fix: a clause added
        # later cannot reintroduce it, whichever punctuation its author picks.
        line = re.sub(r"\.\.+(?=\s|$)", ".", line).rstrip()
        return line if line.endswith((".", "!", "?")) else line + "."
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


def _wait_label(available: bool, load: dict) -> str:
    """Coarse per-partition wait signal from the weft site's live load — the
    weft-sourced analog of the retired slurm_live.wait_label. The agent reads
    this against the speedup it expects."""
    if not available:
        return "unavailable"
    if (load.get("cpus_idle") or 0) > 0:
        return "likely quick (idle nodes free)"
    pend = load.get("pending_jobs") or 0
    if pend == 0:
        return "moderate (no idle nodes, empty queue)"
    return f"queued (~{pend} jobs pending)"


def _cluster_landscape(site: str) -> "tuple[list, list]":
    """The live submission landscape for a slurm-kind weft `site`: the partition
    list (name / cpus / mem / gpu bool / wait label) + the user's access rows.
    Sourced from the weft SitePort — sites_describe (partition capabilities),
    site_load (live idle CPUs + queue depth, TTL-cached weft-side) and
    site_associations (accounts/QOS the user can reach). Best-effort: any
    substrate/scheduler hiccup degrades to ([], []) so a turn never breaks."""
    try:
        from core.compute import get_compute
        ad = get_compute()
        desc = ad.sync_call("sites_describe", site)
    except Exception:  # noqa: BLE001
        return [], []
    caps = ((desc or {}).get("capabilities") or {}).get("scheduler") or {}
    load_parts: dict = {}
    try:
        load_parts = (ad.sync_call("site_load", site) or {}).get("partitions") or {}
    except Exception:  # noqa: BLE001
        load_parts = {}
    parts: list = []
    for cp in caps.get("partitions") or []:
        name = cp.get("name")
        if not name:
            continue
        lp = load_parts.get(name) or {}
        cpn = cp.get("cpus_per_node")
        idle_cpus = lp.get("cpus_idle") or 0
        parts.append({
            "partition": name,
            "cpus_per_node": cpn,
            "mem_gb_per_node": cp.get("mem_gb_per_node"),
            "max_walltime": cp.get("max_walltime"),
            # weft models GPUs as structured gres:[{type:gpu,count}]; the agent
            # cue + routing only need the bool the old sinfo parser produced.
            "gpu": any((g or {}).get("type") == "gpu" for g in (cp.get("gres") or [])),
            "nodes_idle": (idle_cpus // cpn) if cpn else 0,
            "wait": _wait_label(cp.get("available", True), lp),
        })
    access: list = []
    try:
        assoc = ad.sync_call("site_associations", site)
        for a in (assoc or {}).get("associations") or []:
            access.append({"account": a.get("account"),
                           "partition": a.get("partition"),
                           "qos": a.get("allowed_qos") or []})
    except Exception:  # noqa: BLE001
        access = []
    return parts, access


def gpu_readiness() -> tuple[bool, str]:
    """(usable, reason) for the `gpu_usable` cue — the ONE owner of "will a GPU
    step actually accelerate here?", called only when a GPU is in the picture.

    Two regimes, split by whether the site declares a GPU env pack
    (jobs.gpu_env_pack → ABA_JOBS_GPU_ENV_PACK):

    * **Declared** (a batch-only GPU site): GPU work rides the declared CUDA
      pack in the job lane, so probing torch in THIS process would probe the
      wrong interpreter entirely — on a weft deployment the controller carries
      no torch at all, and the probe would warn "set ABA_ACCELERATOR=cuda"
      forever on a correctly configured site. Usable = the declared pack
      exists in the bundle (structural; no solve in this per-turn hot path).
      A declared-but-missing pack reads NOT usable — the same misconfiguration
      the submit path refuses (base_env.gpu_pack_env_id), said earlier.
    * **Not declared** (the unchanged default): usable = the base torch is a
      CUDA build (`torch_cuda_build`, node-independent). macOS never reaches
      here at all — `node_gpus` counts NVIDIA devices only, and the default
      osx-arm64 base accelerates via Metal/MPS with nothing to declare."""
    from core import config as _cfg
    pack = _cfg.settings.gpu_env_pack.get()
    if pack:
        try:
            from core.compute import env_packs
            ok = env_packs.pack_spec(pack) is not None
        except Exception:  # noqa: BLE001 — bundle unreadable ≠ GPU-ready
            ok = False
        # Outcome first, mechanism second: the first wording led the live agent
        # to pass the pack name as env= (now a valid handle, but unnecessary) —
        # what it must actually do is just submit with its GPU estimate.
        return ok, (
            f"GPU steps run as background jobs — submit with the gpu estimate "
            f"and the site's {pack!r} env is applied automatically, no env= "
            f"needed (this node itself is CPU — no interactive GPU sessions)"
            if ok else
            f"site declares GPU env pack {pack!r} but the bundle has no such "
            f"pack — GPU jobs will refuse until it is published")
    from core.exec.verify import torch_cuda_build
    cuda = torch_cuda_build()
    return cuda is not None, (
        f"CUDA torch ({cuda})" if cuda else
        "base torch is CPU-only — a GPU step would fall back to CPU (admin: set "
        "ABA_ACCELERATOR=cuda in config.env + rebuild the env)")


def _build_compute_env() -> dict:
    from core.exec.cpu import effective_cpu_count
    from core.exec.hpc_session import session_allocation
    from core.jobs.submitter import submitter_name
    from core.jobs.weft_submitter import weft_slurm_site

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
    # Surface the cluster landscape whenever a slurm-kind weft site is declared
    # (so the agent can see what it could submit to), regardless of the configured
    # dispatch mode — data-driven off the weft site model, not a local `sinfo`.
    site = weft_slurm_site()
    if site:
        parts, access = _cluster_landscape(site)
        if parts:
            env["partitions"] = parts
            env["partitions_source"] = "live"
        else:
            try:
                from core.jobs.hpc_config import hpc_config
                env["partitions"] = hpc_config().get("partitions") or []
                env["partitions_source"] = "config"
            except Exception:  # noqa: BLE001
                env["partitions"], env["partitions_source"] = [], "none"
        env["user_access"] = access
    # Accelerator readiness: is a GPU both PRESENT and USABLE by our stack? A GPU node
    # is useless if the base torch is CPU-only (the scVI-on-CPU incident: right node,
    # idle GPU). Present = a local GPU or a gpu partition; usable = torch is a CUDA
    # build (node-independent — see torch_cuda_build). The agent weighs gpu_usable, not
    # just "a GPU exists," when placing a GPU step.
    gpu_present = bool(env["node_gpus"]) or any(p.get("gpu") for p in env.get("partitions") or [])
    if gpu_present:
        env["gpu_usable"], env["gpu_usable_reason"] = gpu_readiness()
    # Declared remote machines usable via run_python/run_r site= (the detached
    # lane) — surfaced so the agent knows its placement options WITHOUT having
    # to call describe_compute first. Named only (kind/capacity is the tool's job).
    try:
        from core.jobs.weft_submitter import declared_compute_sites
        env["remote_sites"] = [s["name"] for s in declared_compute_sites()
                               if s["name"] != "local"]
    except Exception:  # noqa: BLE001
        env["remote_sites"] = []
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
                    or config.settings.nextflow_bin.get()
                    or config.settings.nextflow_module.get())
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
