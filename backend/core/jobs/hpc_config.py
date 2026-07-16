"""HPC resource config + estimate→request resolution (ondemand.md P6, Q1).

The agent provides an ESTIMATE (runtime_min + optional cores/mem/gpu hints); the
DEPLOYMENT describes available partitions/QoS/limits/defaults; this maps the two
into a concrete Slurm request. A configured catalog (``$ABA_HPC_CONFIG`` or the
bundle ``hpc:`` settings) wins when present; when it doesn't pin them, ABA
auto-detects **partitions** AND the user's **QOS + account** live from the
deployment's slurm-kind weft site (the SitePort — ``sites_describe`` for
partitions, ``site_associations`` for QOS/account, ranked most-permissive first +
the primary QOS's MaxWall as a walltime cap). So an unconfigured cluster routes
GPU/large jobs to real partitions and submits the right ``--qos``/``--account``
with no ``hpc.yaml`` at all — the file is a pure optional override (pin a partition
list, reorder QOS, force an account).

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
    """Adapt the slurm-kind weft site's partition capabilities (``sites_describe``)
    into the partition-catalog shape — so an unconfigured cluster still routes
    GPU / large jobs to real partitions (general jobs land via the 'first that
    fits' pick). Empty when no cluster site is declared or the substrate is
    offline."""
    try:
        from core.jobs.weft_submitter import weft_slurm_site
        site = weft_slurm_site()
        if not site:
            return []
        from core.compute import get_compute
        desc = get_compute().sync_call("sites_describe", site)
        caps = ((desc or {}).get("capabilities") or {}).get("scheduler") or {}
        out = []
        for p in caps.get("partitions") or []:
            name = p.get("name")
            if not name:
                continue
            secs = p.get("max_walltime_s")            # weft gives unambiguous seconds
            wh = int(secs // 3600) if secs else _BIG
            out.append({
                "name": name,
                "max_cores": int(p.get("cpus_per_node") or 0) or _BIG,
                "max_mem_gb": int(p.get("mem_gb_per_node") or 0) or _BIG,
                "max_walltime_h": wh,
                # weft models GPUs as structured gres:[{type:gpu,count}] → bool.
                "gpu": any((g or {}).get("type") == "gpu" for g in (p.get("gres") or [])),
            })
        return out
    except Exception:  # noqa: BLE001
        return []


def _wall_to_h(val) -> "int | None":
    """A sacctmgr MaxWall string ('D-HH:MM:SS' / 'HH:MM:SS') → whole hours.
    Blank / unlimited → None (unknown ≠ unlimited)."""
    val = (val or "").strip()
    if not val or val.lower() in ("unlimited", "none", "-1"):
        return None
    days = 0
    if "-" in val:
        d, val = val.split("-", 1)
        days = int(d) if d.isdigit() else 0
    hh = val.split(":")[0]
    return days * 24 + (int(hh) if hh.isdigit() else 0)


def _live_qos_account() -> tuple:
    """The user's valid QOS (ranked most-permissive first) + each QOS's MaxWall
    (hours) + their account, from the slurm-kind weft site's ``site_associations``
    — symmetric with :func:`_live_partitions`, so no ``hpc.yaml`` is needed to
    carry them. Ranking mirrors the retired ``slurm_live.qos_account_live``:
    largest MaxWall first, ties toward generic/short names (a cross-partition
    ``long`` beats a partition-scoped ``c_long``). ``((), {}, None)`` when no
    cluster site / no accounting is visible."""
    try:
        from core.jobs.weft_submitter import weft_slurm_site
        site = weft_slurm_site()
        if not site:
            return (), {}, None
        from core.compute import get_compute
        assoc = get_compute().sync_call("site_associations", site)
    except Exception:  # noqa: BLE001 — best-effort; absence is normal
        return (), {}, None
    qos_names: set = set()
    account = None
    for a in (assoc or {}).get("associations") or []:
        if a.get("account") and account is None:
            account = a["account"]
        qos_names.update(a.get("allowed_qos") or [])
    if not qos_names:
        return (), {}, account
    walls = {q.get("name"): _wall_to_h(q.get("max_wall"))
             for q in ((assoc or {}).get("qos") or [])}
    _INF = 1 << 30
    ranked = sorted(qos_names,
                    key=lambda q: (-(_INF if walls.get(q) is None else walls[q]), len(q), q))
    return tuple(ranked), {q: walls.get(q) for q in ranked}, account


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
    # QOS + account: discover live from the weft site's associations when not
    # configured — symmetric with live partitions, so no hpc.yaml is needed to
    # carry them (a configured `qos`/`account` still wins). Also remember the
    # primary QOS's MaxWall so resolve_resources never requests more walltime
    # than the QOS allows.
    if not cfg.get("qos"):
        ranked, walls, account = _live_qos_account()
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
