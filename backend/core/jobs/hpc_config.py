"""HPC resource config + estimate→request resolution (ondemand.md P6, Q1).

The agent provides an ESTIMATE (runtime_min + optional cores/mem/gpu hints); the
DEPLOYMENT describes available partitions/QoS/limits/defaults; this maps the two
into a concrete Slurm request. A configured catalog (``$ABA_HPC_CONFIG`` or the
bundle ``hpc:`` settings) wins when present; when it doesn't pin them, ABA
auto-detects **partitions** from live ``sinfo`` (default partition first) AND the
user's **QOS + account** from live ``sacctmgr`` (slurm_live.qos_account_live,
ranked most-permissive first + the primary QOS's MaxWall as a walltime cap). So an
unconfigured cluster routes GPU/large jobs to real partitions and submits the right
``--qos``/``--account`` with no ``hpc.yaml`` at all — the file is a pure optional
override (pin a partition list, reorder QOS, force an account).

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
from pathlib import Path
from typing import Optional

from core import config

_DEFAULTS = {"cores": 1, "mem_gb": 4, "walltime_h": 4}
_BIG = 1 << 30


def _live_partitions() -> list:
    """Adapt live ``sinfo`` (slurm_live) into the partition-catalog shape, the
    cluster's default partition FIRST — so an unconfigured cluster still routes
    GPU / large jobs to real partitions (general jobs land on the default via the
    'first that fits' pick). Empty when Slurm isn't reachable."""
    try:
        import re
        from core.jobs.slurm_live import default_partition, partitions_live
        dp = default_partition()
        out = []
        for p in partitions_live():
            m = re.match(r"(?:(\d+)-)?(\d+):", p.get("max_walltime") or "")
            wh = (int(m.group(1) or 0) * 24 + int(m.group(2))) if m else _BIG
            out.append({
                "name": p["partition"],
                "max_cores": int(p.get("cpus_per_node") or 0) or _BIG,
                "max_mem_gb": int(p.get("mem_gb_per_node") or 0) or _BIG,
                "max_walltime_h": wh,
                "gpu": bool(p.get("gpu")),
            })
        out.sort(key=lambda q: q["name"] != dp)          # default partition first
        return out
    except Exception:  # noqa: BLE001
        return []


def hpc_config() -> dict:
    """Deployment HPC config. Precedence: ``$ABA_HPC_CONFIG`` YAML (operator /
    tests) → ``EffectiveBundle.settings['hpc']`` (composes system→inst→lab→user)
    → minimal defaults."""
    cfg: dict = {}
    # Explicit env path wins; otherwise an optional `$ABA_HOME/hpc.yaml` is picked up
    # automatically (so `aba hpc-config` "just works" without setting ABA_HPC_CONFIG).
    path = config.settings.hpc_config.get()
    if not path:
        home = config.aba_home()
        if home and (Path(home) / "hpc.yaml").exists():
            path = str(Path(home) / "hpc.yaml")
    if path and Path(path).exists():
        try:
            import yaml
            cfg = yaml.safe_load(Path(path).read_text()) or {}
            # The file is written wrapped under a top-level `hpc:` key (installer
            # output + the documented format + the bundle's settings['hpc'] shape).
            # Unwrap it — without this the whole catalog (partitions/qos/account)
            # was read as empty and silently ignored, so jobs submitted with no
            # --qos/--account and fell back to the cluster default QOS.
            if isinstance(cfg.get("hpc"), dict):
                cfg = cfg["hpc"]
        except Exception:  # noqa: BLE001
            cfg = {}
    if not cfg:
        try:
            from core.bundle.active import get_bundle
            cfg = dict(get_bundle().settings.get("hpc") or {})
        except Exception:  # noqa: BLE001
            cfg = {}
    cfg.setdefault("partitions", [])
    if not cfg["partitions"]:
        cfg["partitions"] = _live_partitions()       # auto-detect when nothing is configured
    # QOS + account: discover live from sacctmgr when not configured — symmetric
    # with live partitions, so no hpc.yaml is needed to carry them (a configured
    # `qos`/`account` still wins). Also remember the primary QOS's MaxWall so
    # resolve_resources never requests more walltime than the QOS allows.
    if not cfg.get("qos"):
        try:
            from core.jobs.slurm_live import qos_account_live
            ranked, walls, account = qos_account_live()
        except Exception:  # noqa: BLE001 — best-effort; absence is normal
            ranked, walls, account = (), {}, None
        if ranked:
            cfg["qos"] = list(ranked)
            primary_w = walls.get(ranked[0])
            if primary_w:
                cfg["qos_max_walltime_h"] = primary_w
        if account and not cfg.get("account"):
            cfg["account"] = account
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
    # Cap to the chosen QOS's MaxWall too (discovered live or configured) so the
    # request is never rejected for exceeding the QOS limit, not just the partition's.
    qmw = cfg.get("qos_max_walltime_h")
    if qmw:
        walltime_h = min(walltime_h, int(qmw))
    qos_list = cfg.get("qos") or []
    return {
        "partition": partition,
        "qos": qos_list[0] if qos_list else None,
        "cores": cores, "mem_gb": mem_gb, "walltime_h": walltime_h,
        "gpu": gpu, "account": cfg.get("account"),
    }
