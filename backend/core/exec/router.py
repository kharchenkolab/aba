"""ExecutionRouter — decides *where* a step runs, from the live compute env.

The placement decision is ABA's, never the individual tool/MCP server's:
declare → **decide** → place → run.

Rules (replacing the old single "estimated_runtime_min ≥ 4 → background" heuristic,
which silently relocated state-dependent cells into a fresh process):

- **Local mode** — interactive by default; a long cell just runs in the persistent
  kernel (raise `timeout_s`). Background ONLY on an explicit request (the agent
  parallelizing independent jobs, or the user asking). Never auto-backgrounded.
- **Slurm mode** — dispatch to the background queue (→ Slurm via the submitter)
  when the step needs more than this node has (cores/mem/GPU) or might exceed the
  allocation's remaining walltime; otherwise interactive. The agent drives the
  speed-vs-queue-wait judgment by requesting resources (est_cores/est_gpu) after
  reading `describe_compute`. Code here is the safety net (won't fit / would be
  killed) plus honoring the explicit flag.

`location` stays "local" | "background"; the active submitter
(`ABA_BATCH_SUBMITTER`) turns a "background" choice into a local async job or an
`sbatch` job. Thresholds are tunable via ABA_SLURM_MEM_FRAC / ABA_SLURM_WALLTIME_FRAC.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class ExecutorChoice:
    location: str = "local"          # "local" (interactive) | "background" (async; Slurm when mode=slurm)
    rationale: str = ""
    requires_approval: bool = False


def _frac(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except (TypeError, ValueError):
        return default


def decide(*, env: Optional[dict] = None,
           estimate: Optional[dict] = None,
           override: Optional[str] = None) -> ExecutorChoice:
    """Choose local (interactive) vs background, given the compute env, the
    agent's estimate ({runtime_min, cores, mem_gb, gpu}), and an explicit override."""
    env = env or {}
    estimate = estimate or {}
    mode = env.get("mode", "local")

    if override == "background":
        return ExecutorChoice("background", "explicit background request")
    if override and override not in ("local", "background"):
        return ExecutorChoice(override, f"forced → {override}", requires_approval=True)

    if mode != "slurm":
        # LOCAL: never auto-background. Long cells run interactively (raise timeout_s).
        return ExecutorChoice("local", "local mode — interactive (background only on explicit request)")

    # SLURM mode — the safety net + the "Slurm has more" cases.
    ec = float(estimate.get("cores") or 0)
    em = float(estimate.get("mem_gb") or 0)
    eg = bool(estimate.get("gpu"))
    ert = float(estimate.get("runtime_min") or 0)
    nc = float(env.get("node_cores") or 1)
    nm = float(env.get("node_mem_gb") or 0)
    ng = int(env.get("node_gpus") or 0)
    wt = env.get("walltime_remaining_min")              # None = unbounded
    parts = env.get("partitions") or []
    best_cores = max((int(p.get("cpus_per_node") or 0) for p in parts), default=0)
    gpu_part = any(p.get("gpu") for p in parts)
    mem_frac = _frac("ABA_SLURM_MEM_FRAC", 0.85)
    wt_frac = _frac("ABA_SLURM_WALLTIME_FRAC", 0.8)

    reasons: list[str] = []
    if eg and ng <= 0 and gpu_part:
        reasons.append("needs a GPU this node lacks")
    if em and nm and em > mem_frac * nm:
        reasons.append(f"~{em:g} GB ≳ node's {nm:g} GB")
    if wt is not None and ert and ert > wt_frac * wt:
        reasons.append(f"~{ert:g} min may exceed walltime left ({round(wt)} min)")
    if ec and ec > nc and best_cores > nc:               # more cores AND Slurm has bigger nodes
        reasons.append(f"wants {ec:g} cores; Slurm offers up to {best_cores}/node (> {nc:g} here)")

    if reasons:
        return ExecutorChoice("background", "Slurm: " + "; ".join(reasons))
    return ExecutorChoice("local", "fits this node — interactive")
