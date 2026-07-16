"""Machine-type + configuration proposal (misc/compute_settings.md §5.4).

One PURE function over a weft capability record (`capabilities:v2`) — no
weft import, no I/O — so the /api/compute router, the Settings tab, and the
Guide's compute tools share the exact same judgment. This is aba domain
policy ("reveal, don't require"): weft measures; aba proposes; the user
confirms or tweaks.

Inputs beyond the record are facts only the caller can know:
  dest           the ssh target the user typed (name inference)
  shared_paths   deployment data paths VERIFIED present on the machine
                 (the shared-fs canary — never inferred from mount names)
  accounts       scheduler billing accounts visible to the user
  known_names    already-registered site names (collision avoidance)
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

# hostname labels that name a machine's ROLE, not the machine (login3,
# submit-1, head, gw2, bastion...) — skipped when guessing a site name
_GENERIC_LABEL = re.compile(
    r"^(login|submit|head|gw|gateway|bastion|access|portal|ssh)[-_]?\d*$", re.I)
_NAME_OK = re.compile(r"[^a-z0-9-]+")

# volatile / node-local mounts: never propose them as the weft root
_VOLATILE = ("/tmp", "/dev/shm", "/var/tmp")
_HOMEISH = re.compile(r"^/(home|users?|Users)(/|$)")


def suggest_name(dest: str, known_names: Iterable[str] = ()) -> str:
    """'me@login2.vbc.ac.at' → 'vbc' (first hostname label that names the
    machine rather than its role; sanitized; de-collided with a -2 suffix)."""
    host = dest.rsplit("@", 1)[-1].strip().lower()
    labels = [l for l in host.split(".") if l]
    picked = next((l for l in labels if not _GENERIC_LABEL.match(l)),
                  labels[0] if labels else "machine")
    name = _NAME_OK.sub("-", picked).strip("-") or "machine"
    taken = set(known_names)
    if name in taken:
        i = 2
        while f"{name}-{i}" in taken:
            i += 1
        name = f"{name}-{i}"
    return name


def _partition_gpus(p: dict) -> int:
    """Per-node GPU count for a partition node-class row (weft gres shape:
    [{type, model, count}])."""
    return sum(g.get("count", 0) for g in (p.get("gres") or [])
               if g.get("type") == "gpu")


# The consequence that matters: results you KEEP are retained IN PLACE under
# this root (weft never relocates them) — so the root's durability is the
# keeps' durability. Environments/caches rebuild; kept results don't.
_KIND_NOTE = {
    "scratch": "fast and roomy, but may be purged — environments rebuild; "
               "results kept here would be lost with it",
    "home": "usually backed up — results you keep here stay safe; counts "
            "against your home quota",
    "other": "writable space — check whether it is backed up if you plan "
             "to keep results here",
}


def _candidate_kind(path: str) -> str:
    if "scratch" in path or "work" in path or "/tmp" in path:
        return "scratch"
    if _HOMEISH.match(path) or path == "~":
        return "home"
    return "other"


def working_root_options(storage: dict) -> list[dict]:
    """Every plausible weft-root choice from the probe, honestly labeled —
    the UI shows these as a picker, never a silent guess. Volatile mounts
    (/tmp, /dev/shm) are excluded outright; everything else is the user's
    call with the trade-off stated."""
    opts = []
    for c in (storage or {}).get("candidates") or []:
        path = c.get("path") or ""
        if not c.get("writable") or not path:
            continue
        if any(path == v or path.startswith(v + "/") for v in _VOLATILE):
            continue
        kind = _candidate_kind(path)
        opts.append({"root": path.rstrip("/") + "/.weft",
                     "free_gb": c.get("free_gb"), "kind": kind,
                     "note": _KIND_NOTE[kind]})
    if not any(o["kind"] == "home" for o in opts):
        opts.append({"root": "~/.weft",
                     "free_gb": (storage or {}).get("free_gb"),
                     "kind": "home", "note": _KIND_NOTE["home"]})
    # stable order: scratch-likes by free space, then home, then other
    opts.sort(key=lambda o: ({"scratch": 0, "home": 1, "other": 2}[o["kind"]],
                             -(o["free_gb"] or 0), -o["root"].count("/")))
    return opts


def pick_working_root(storage: dict, *, scheduler: bool = False) -> dict:
    """The DEFAULT weft-root pick (the user can choose any option):
    - scheduler sites (clusters): prefer scratch/work — cluster homes are
      classically small-quota, scratch is the norm, and everything under a
      weft root rebuilds by design;
    - plain servers/workstations: prefer HOME — it's typically the backed-up,
      durable place, and there is no purge policy to dodge — unless home is
      tight and a much roomier writable mount exists."""
    opts = working_root_options(storage)
    scratchy = [o for o in opts if o["kind"] == "scratch"]
    homey = [o for o in opts if o["kind"] == "home"]
    if scheduler and scratchy:
        best = scratchy[0]
    elif not scheduler and homey:
        best = homey[0]
        big = scratchy[0] if scratchy else None
        home_free = best.get("free_gb") or 0
        if big and home_free < 25 and (big.get("free_gb") or 0) > 4 * home_free:
            best = big
    else:
        best = (scratchy + homey + opts)[0] if opts else \
            {"root": "~/.weft", "free_gb": None, "kind": "home",
             "note": _KIND_NOTE["home"]}
    return {"root": best["root"], "free_gb": best.get("free_gb"),
            "reason": best["note"], "kind": best["kind"], "options": opts}


def propose(caps: dict, *, dest: str = "",
            shared_paths: Iterable[str] = (),
            accounts: Iterable[str] = (),
            known_names: Iterable[str] = ()) -> dict:
    """The §5.4 inference table as one record. Every field is a *default* the
    user may edit; nothing here asks a question the probe already answered."""
    sched = (caps.get("scheduler") or {})
    sched_type = sched.get("type", "none")
    partitions = list(sched.get("partitions") or [])
    gpus = list(caps.get("gpus") or [])
    if not gpus and caps.get("compute"):
        gpus = list((caps["compute"] or {}).get("gpus") or [])
    part_gpu_total = sum(_partition_gpus(p) * (p.get("nodes") or 0)
                         for p in partitions)
    has_gpu = bool(gpus) or part_gpu_total > 0

    if sched_type == "slurm":
        kind, machine_type = "slurm", "Slurm cluster"
    elif has_gpu:
        kind, machine_type = "ssh", "GPU workstation"
    else:
        kind, machine_type = "ssh", "remote server"

    # cluster totals — the caps line rule: never the login node's own cpus
    totals = None
    if partitions:
        totals = {
            "nodes": sum(p.get("nodes") or 0 for p in partitions),
            "cores": sum((p.get("nodes") or 0) * (p.get("cpus_per_node") or 0)
                         for p in partitions),
            "gpus": part_gpu_total,
            "partitions": len({p.get("name") for p in partitions}),
        }

    ver = sched.get("version") or ""
    if sched_type == "slurm":
        headline = (f"This is a Slurm cluster{f' (v{ver})' if ver else ''} — "
                    f"{totals['nodes']} nodes in {totals['partitions']} "
                    f"partition{'s' if totals['partitions'] != 1 else ''}")
    elif has_gpu:
        gpu_bits = ", ".join(f"{g.get('count', 1)}× {g.get('model', 'GPU')}"
                             for g in gpus) or "GPUs"
        headline = (f"This is a GPU workstation — {caps.get('cpus', '?')} "
                    f"cores, {gpu_bits}")
    else:
        headline = (f"This is a remote server — {caps.get('cpus', '?')} cores, "
                    f"{caps.get('mem_gb', '?')} GB")

    use_for = ["interactive", "background"] + (["gpu"] if has_gpu else [])

    shared = [p for p in shared_paths if p]
    contract = "shared-fs" if shared else "detached"

    part_rows = [{
        "name": p.get("name"),
        "selected": bool(p.get("available", True)),
        "gpus_per_node": _partition_gpus(p),
        "nodes": p.get("nodes"),
        "cpus_per_node": p.get("cpus_per_node"),
        "mem_gb_per_node": p.get("mem_gb_per_node"),
        "max_walltime": p.get("max_walltime"),
    } for p in partitions]

    accounts = [a for a in accounts if a]

    return {
        "kind": kind,
        "machine_type": machine_type,
        "headline": headline,
        "name": suggest_name(dest, known_names),
        "use_for": use_for,
        "notes": [],
        "working": pick_working_root(caps.get("storage") or {},
                                     scheduler=sched_type != "none"),
        "long_term": [{"path": p, "stable": True} for p in shared],
        "contract": contract,
        "contract_evidence": shared,
        "partitions": part_rows,
        "account": accounts[0] if len(accounts) == 1 else None,
        "accounts": accounts,
        "gpus": gpus,
        "totals": totals,
        "facts": {k: caps.get(k) for k in
                  ("cpus", "mem_gb", "arch", "glibc", "internet",
                   "module_system")} | {"scheduler": sched_type},
    }


def build_site_config(proposal: dict, *, dest: str = "",
                      port: Optional[int] = None,
                      ssh_opts: Optional[list[str]] = None,
                      pixi_source: Optional[str] = None) -> dict:
    """A confirmed proposal → the weft `register_site` config dict. Kept next
    to propose() so the two halves of the contract stay in one file."""
    cfg: dict = {"root": proposal["working"]["root"]}
    if proposal["kind"] in ("ssh", "slurm"):
        user, _, host = dest.rpartition("@")
        cfg["host"] = host or dest
        if user:
            cfg["user"] = user
        if port:
            cfg["port"] = port
        if ssh_opts:
            cfg["ssh_opts"] = list(ssh_opts)
    policy: dict = {}
    selected = [p["name"] for p in proposal.get("partitions", [])
                if p.get("selected")]
    if selected and len(selected) < len(proposal.get("partitions", [])):
        policy["partitions_allowed"] = selected
    storage_roles: dict = {}
    stable = [e["path"] for e in proposal.get("long_term", []) if e.get("path")]
    if stable:
        storage_roles["large"] = stable[0]   # weft's role is single-valued
    working_parent = proposal["working"]["root"].rsplit("/.weft", 1)[0]
    if "scratch" in working_parent:
        storage_roles["scratch"] = working_parent
    if storage_roles:
        policy["storage"] = storage_roles
    notes = [n.strip() for n in (proposal.get("notes") or []) if n.strip()]
    if notes:
        policy["notes"] = notes   # free-text guidance weft surfaces per plan
    if policy:
        cfg["policy"] = policy
    if proposal["kind"] == "slurm" and proposal.get("account"):
        cfg["scheduler"] = {"account": proposal["account"]}
    if pixi_source:
        cfg["pixi_source"] = pixi_source
    return cfg
