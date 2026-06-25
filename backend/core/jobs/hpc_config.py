"""HPC resource config + estimate→request resolution (ondemand.md P6, Q1).

The agent provides an ESTIMATE (runtime_min + optional cores/mem/gpu hints); the
DEPLOYMENT describes available partitions/QoS/limits/defaults; this maps the two
into a concrete Slurm request. We do NOT query live ``sinfo`` — a VBC cluster
(ABA on a real node) could look it up directly, but the general case can't, so
the bundle/site config is the source of truth.

Config shape::

    hpc:
      partitions:
        - {name: short, max_cores: 16, max_mem_gb: 64, max_walltime_h: 4, gpu: false}
        - {name: gpu,   max_cores: 16, max_mem_gb: 128, max_walltime_h: 24, gpu: true}
      qos: [normal]
      account: my_lab
      defaults: {partition: short, cores: 1, mem_gb: 4, walltime_h: 4}
"""
from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Optional

_DEFAULTS = {"cores": 1, "mem_gb": 4, "walltime_h": 4}
_BIG = 1 << 30


def hpc_config() -> dict:
    """Deployment HPC config. Precedence: ``$ABA_HPC_CONFIG`` YAML (operator /
    tests) → ``EffectiveBundle.settings['hpc']`` (composes system→inst→lab→user)
    → minimal defaults."""
    cfg: dict = {}
    path = os.environ.get("ABA_HPC_CONFIG")
    if path and Path(path).exists():
        try:
            import yaml
            cfg = yaml.safe_load(Path(path).read_text()) or {}
        except Exception:  # noqa: BLE001
            cfg = {}
    if not cfg:
        try:
            from core.bundle.active import get_bundle
            cfg = dict(get_bundle().settings.get("hpc") or {})
        except Exception:  # noqa: BLE001
            cfg = {}
    cfg.setdefault("partitions", [])
    d = dict(_DEFAULTS)
    d.update(cfg.get("defaults") or {})
    cfg["defaults"] = d
    return cfg


def resolve_resources(estimate: Optional[dict] = None, cfg: Optional[dict] = None) -> dict:
    """Map an agent estimate + config → a concrete request.

    estimate (all optional): ``runtime_min``, ``cores``, ``mem_gb``, ``gpu``.
    Returns ``{partition, qos, cores, mem_gb, walltime_h, gpu, account}``.
    Picks the FIRST partition that fits (gpu match + within ceilings), clamps the
    request to that partition's ceilings, and falls back to the default partition
    when none fits (operator's responsibility)."""
    estimate = estimate or {}
    cfg = cfg if cfg is not None else hpc_config()
    d = cfg.get("defaults") or _DEFAULTS
    gpu = bool(estimate.get("gpu"))
    cores = max(1, int(estimate.get("cores") or d.get("cores", 1)))
    mem_gb = max(0, int(estimate.get("mem_gb") or d.get("mem_gb", 4)))   # 0 → omit --mem
    rt_min = float(estimate.get("runtime_min") or 0)
    walltime_h = max(1, math.ceil(rt_min / 60.0)) if rt_min > 0 else int(d.get("walltime_h", 4))

    parts = [p for p in (cfg.get("partitions") or []) if bool(p.get("gpu")) == gpu]

    def _fits(p: dict) -> bool:
        return (cores <= int(p.get("max_cores", _BIG))
                and mem_gb <= int(p.get("max_mem_gb", _BIG))
                and walltime_h <= int(p.get("max_walltime_h", _BIG)))

    chosen = next((p for p in parts if _fits(p)), None)
    if chosen is None and parts:
        # The request exceeds every partition → use the LARGEST (by cores) and
        # clamp to it (a slightly-smaller job beats a rejected sbatch).
        chosen = max(parts, key=lambda p: int(p.get("max_cores", 0)))
    partition = (chosen or {}).get("name") or d.get("partition")
    if chosen:
        cores = min(cores, int(chosen.get("max_cores", cores)))
        mem_gb = min(mem_gb, int(chosen.get("max_mem_gb", mem_gb)))
        walltime_h = min(walltime_h, int(chosen.get("max_walltime_h", walltime_h)))
    qos_list = cfg.get("qos") or []
    return {
        "partition": partition,
        "qos": qos_list[0] if qos_list else None,
        "cores": cores, "mem_gb": mem_gb, "walltime_h": walltime_h,
        "gpu": gpu, "account": cfg.get("account"),
    }
