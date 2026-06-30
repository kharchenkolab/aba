"""Live Slurm scheduler queries for compute planning (ondemand: rely on live).

`describe_compute` and the ExecutionRouter use these to show the agent the real
submission landscape — what partitions/QOS the user can reach, how big the nodes
are, and how busy the queue is right now — so it can weigh Slurm-vs-local with
queue waits. Every function is best-effort and returns an empty/None result when
Slurm isn't installed or reachable, so callers degrade to the configured catalog
(core.jobs.hpc_config) without error.
"""
from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess


def slurm_available() -> bool:
    """True if the Slurm client tools are on PATH (the deployment can query)."""
    return shutil.which("sinfo") is not None


def _run(cmd: list[str], timeout: int = 8) -> str | None:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.stdout if p.returncode == 0 else None
    except Exception:  # noqa: BLE001 — best-effort; absence is normal
        return None


def parse_partitions(sinfo_out: str) -> list[dict]:
    """Aggregate `sinfo -h -o '%R|%a|%l|%c|%m|%G|%D|%t'` rows into one dict per
    partition: name, avail, max_walltime, cpus_per_node (largest node), mem_gb_per_node,
    gpu, nodes_total, nodes_idle. Pure (testable on captured output)."""
    parts: dict[str, dict] = {}
    for line in (sinfo_out or "").splitlines():
        if not line.strip():
            continue
        f = (line.split("|") + [""] * 8)[:8]
        name, avail, tlimit, cpn, mpn, gres, nds, state = (x.strip() for x in f)
        if not name:
            continue
        p = parts.setdefault(name, {
            "partition": name, "avail": avail, "max_walltime": tlimit,
            "cpus_per_node": 0, "mem_gb_per_node": 0.0, "gpu": False,
            "nodes_total": 0, "nodes_idle": 0,
        })
        if cpn.isdigit():
            p["cpus_per_node"] = max(p["cpus_per_node"], int(cpn))
        m = re.match(r"(\d+)", mpn)                       # %m can be "64000" or "64000+"
        if m:
            p["mem_gb_per_node"] = max(p["mem_gb_per_node"], round(int(m.group(1)) / 1024, 1))
        if gres and gres.lower() not in ("(null)", "") and "gpu" in gres.lower():
            p["gpu"] = True
        n = int(nds) if nds.isdigit() else 0
        p["nodes_total"] += n
        if state.startswith(("idle", "mix")):            # mix = partially free → can still start
            p["nodes_idle"] += n
    return list(parts.values())


def parse_queue(squeue_out: str) -> dict:
    """Count pending/running jobs per partition from `squeue -h -o '%P|%t'`."""
    depth: dict[str, dict] = {}
    for line in (squeue_out or "").splitlines():
        if not line.strip():
            continue
        f = (line.split("|") + ["", ""])[:2]
        part, st = f[0].strip().rstrip("*"), f[1].strip()
        d = depth.setdefault(part, {"pending": 0, "running": 0})
        if st == "PD":
            d["pending"] += 1
        elif st == "R":
            d["running"] += 1
    return depth


def parse_assoc(sacctmgr_out: str) -> list[dict]:
    """User's submittable (account, partition, QOS) from
    `sacctmgr -nP show assoc ... format=Account,Partition,QOS`. Empty when the
    cluster runs without accounting (sacctmgr returns nothing) — caller then
    falls back to the configured catalog."""
    rows: list[dict] = []
    for line in (sacctmgr_out or "").splitlines():
        if not line.strip():
            continue
        f = (line.split("|") + ["", "", ""])[:3]
        rows.append({"account": f[0].strip(), "partition": f[1].strip(),
                     "qos": [q for q in f[2].strip().split(",") if q]})
    return rows


def partitions_live() -> list[dict]:
    out = _run(["sinfo", "-h", "-o", "%R|%a|%l|%c|%m|%G|%D|%t"])
    return parse_partitions(out) if out is not None else []


def default_partition() -> Optional[str]:
    """The cluster's DEFAULT partition (the one `sinfo` marks with `*` in %P),
    or None if it can't be determined."""
    out = _run(["sinfo", "-h", "-o", "%P"])
    if not out:
        return None
    for tok in out.split():
        if tok.endswith("*"):
            return tok.rstrip("*")
    return None


def queue_depth() -> dict:
    out = _run(["squeue", "-h", "-o", "%P|%t"])
    return parse_queue(out) if out is not None else {}


def user_access() -> list[dict]:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or ""
    if not user:
        return []
    out = _run(["sacctmgr", "-nP", "show", "assoc", f"user={user}",
                "format=Account,Partition,QOS"])
    return parse_assoc(out) if out is not None else []


def _wall_to_h(val) -> "int | None":
    """sacctmgr MaxWall ('D-HH:MM:SS' / 'HH:MM:SS') → whole hours. Blank/unlimited → None."""
    val = (val or "").strip()
    if not val or val.lower() in ("unlimited", "none", "-1"):
        return None
    days = 0
    if "-" in val:
        d, val = val.split("-", 1)
        days = int(d) if d.isdigit() else 0
    hh = val.split(":")[0]
    return days * 24 + (int(hh) if hh.isdigit() else 0)


def qos_walls() -> dict:
    """{qos_name: max_walltime_h or None} from `sacctmgr show qos format=name,maxwall`."""
    out = _run(["sacctmgr", "-nP", "show", "qos", "format=name,maxwall"], timeout=20)
    walls: dict = {}
    for line in (out or "").splitlines():
        f = (line.split("|") + ["", ""])[:2]
        if f[0].strip():
            walls[f[0].strip()] = _wall_to_h(f[1])
    return walls


@functools.lru_cache(maxsize=1)
def qos_account_live() -> tuple:
    """The user's valid QOS (ranked) + each QOS's MaxWall + their account, from
    `sacctmgr` — the runtime analog of the installer's discovery, so QOS/account
    are resolved ON THE FLY (symmetric with live partitions) and no hpc.yaml is
    needed to carry them. Without this the runtime submits no ``--qos`` and jobs
    land on the cluster-default QOS (often an 8h cap → QOSMaxWallDurationPerJobLimit).

    Returns ``(qos_ranked, {qos: max_h}, account)`` — QOS ranked most-permissive
    first (largest MaxWall; ties toward generic/short names so a cross-partition
    ``long`` beats a partition-scoped ``c_long``). ``([], {}, None)`` when sacctmgr
    is absent/quiet. Cached: a user's QOS/account is stable for the process; a
    restart re-discovers (call ``qos_account_live.cache_clear()`` to force)."""
    qos: set = set()
    account = None
    for row in user_access():                 # parse_assoc: [{account, partition, qos:[...]}]
        if row.get("account") and account is None:
            account = row["account"]
        qos.update(row.get("qos") or [])
    if not qos:
        return (), {}, account
    walls = qos_walls()
    _INF = 1 << 30
    ranked = sorted(qos, key=lambda q: (-(_INF if walls.get(q) is None else walls[q]), len(q), q))
    return tuple(ranked), {q: walls.get(q) for q in ranked}, account


def wait_label(part: dict, queue: dict) -> str:
    """Coarse wait signal for a partition: idle nodes → likely quick; else gauge
    by pending depth. The agent reads this against the speedup it expects."""
    if part.get("avail") and part["avail"] != "up":
        return "unavailable"
    if part.get("nodes_idle", 0) > 0:
        return "likely quick (idle nodes free)"
    pend = (queue.get(part.get("partition", ""), {}) or {}).get("pending", 0)
    if pend == 0:
        return "moderate (no idle nodes, empty queue)"
    return f"queued (~{pend} jobs pending)"
