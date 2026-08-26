"""Project deletion & disk reclaim — the ONE owner of "what does deleting a
project free, and what does it never touch".

Three classes (docs/arch/envs.md § Project deletion & reclaim):

- **rebuildable** — the project's default session prefixes and its named /
  isolated env realizations. weft keeps the EnvID and the solved lock, so
  dropping the prefix is RECLAIM, not loss: the env rebuilds cache-warm on
  next use. These are freed.
- **shared** — an env_id some surviving project's registry also names, and any
  realization weft reports `read_only` (adopted from an institutional pack
  root). Never touched here, whatever the bytes. weft refuses the read-only
  case on its own; we classify it first so a preview never PROMISES bytes it
  cannot deliver.
- **valued** — retained run outputs, dataset homes, and the project directory
  with its recovery archive and weft_envs.json. A reclaim never deletes these;
  it reports them so the delete card can say what survives.

Order matters: sessions stop BEFORE envs are evicted. A live session holds its
base env against `env_evict` (weft raises `env.evict_blocked`), so evicting
first would refuse on exactly the envs worth reclaiming.

Nothing here is implicit. `plan()` is a dry run; only `reclaim(confirm=True)`
acts, and it is best-effort per item — a substrate that cannot be reached, or
one env that refuses, never blocks the delete or the other evictions.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

# A realization's `bytes` is apparent size (du): a prefix hardlinks most of its
# blocks from the shared package cache, so the filesystem gives back less than
# the sum. weft measures the real delta when the sweep runs (live-agent eval put
# the overstatement at ~2.4x) — never present the plan's number as freed disk.
BYTES_NOTE = ("sizes are apparent (du) and share hardlinked blocks with the "
              "package cache — the filesystem gives back less; the sweep "
              "reports what weft actually reclaimed")


def _project_ids(exclude: Optional[str] = None) -> Optional[list[str]]:
    """Every project the registry still lists, minus `exclude`. During a delete
    the row is already gone, so this is naturally the SURVIVORS.

    Returns None when the registry cannot be read. That is NOT an empty list:
    empty means "no other project shares anything, evict freely", which is the
    dangerous direction to guess. Unknown survivors ⇒ nothing is provably
    private ⇒ nothing is evicted."""
    try:
        from core import projects  # noqa: PLC0415
        ids = [str(p["id"]) for p in projects.list_projects()]
    except Exception:  # noqa: BLE001
        return None
    return [i for i in ids if i != str(exclude)]


def _registered_envs(pid: str) -> list[dict]:
    """This project's named + isolated envs. Reads the per-project registry
    file directly (via named_envs), so it still answers after the project's
    registry row and DB are gone."""
    from core.compute import named_envs  # noqa: PLC0415
    out = []
    for name in named_envs.list_names(pid):
        row = named_envs.resolve(pid, name) or {}
        eid = row.get("env_id")
        if eid:
            out.append({"name": name, "env_id": eid,
                        "language": row.get("language") or "python"})
    return out


def _referrers(env_id: str, others: list[str]) -> list[str]:
    """Which OTHER projects name this same env_id. EnvIDs are content-addressed:
    two projects that asked for the same packages get the same id, and evicting
    it for one steals the prefix from the other. Unreadable registry ⇒ counted
    as a referrer (a project we cannot read is not a project we may ignore)."""
    from core.compute import named_envs  # noqa: PLC0415
    hits = []
    for other in others:
        try:
            names = named_envs.list_names(other)
        except Exception:  # noqa: BLE001
            hits.append(other)
            continue
        for name in names:
            row = named_envs.resolve(other, name) or {}
            if row.get("env_id") == env_id:
                hits.append(other)
                break
    return hits


def _status(env_id: str) -> Optional[dict]:
    """weft's record for one env, or None when the substrate can't answer."""
    from core.compute import adapter as _adapter  # noqa: PLC0415
    from core.compute import named_envs  # noqa: PLC0415
    try:
        return named_envs._sync(_adapter.get_compute().env_status(env_id))
    except Exception:  # noqa: BLE001 — offline substrate ≠ a delete blocker
        return None


def _valued(pid: str) -> dict:
    """What a delete must NOT reclaim, for the preview card. Never raises: a
    rollup that cannot be computed says so rather than reading as 'nothing
    valuable here' (the emptiest-looking answer is the most dangerous one)."""
    from core.config import project_root  # noqa: PLC0415
    out: dict = {"project_dir": str(project_root(pid)),
                 "note": "kept results, dataset homes and the project "
                         "directory (recovery archive + weft_envs.json) "
                         "survive a delete — reclaim never touches them"}
    # data_ledger's project_id is DECORATIVE — "the graph is already scoped to
    # the active project's DB" (core/data/ledger.py:189). Asking it about any
    # other project silently returns the CURRENT project's numbers, which is
    # worse than no number: the delete card would confidently describe the
    # wrong project's valued items. Only ask when the scope actually matches.
    try:
        from core import projects  # noqa: PLC0415
        current = str(projects.current())
    except Exception:  # noqa: BLE001
        current = None
    if current != pid:
        out["unknown"] = True
        out["detail"] = ("the data ledger reads the ACTIVE project's graph; "
                         "open this project to see its kept results and "
                         "dataset homes")
        return out
    try:
        from core.data.ledger import data_ledger  # noqa: PLC0415
        led = data_ledger(pid)
        totals = led.get("totals") or {}
        out["valued_items"] = totals.get("items")
        out["at_risk"] = totals.get("at_risk")
        out["degraded"] = bool(led.get("degraded"))
        out["ledger"] = led
    except Exception as e:  # noqa: BLE001
        out["unknown"] = True
        out["detail"] = f"data ledger unavailable: {e}"
    return out


def plan(pid: str, *, valued: bool = True) -> dict:
    """What deleting this project would free — dry, free, honest."""
    pid = str(pid)
    others = _project_ids(exclude=pid)
    rebuildable, shared, not_realized, unknown = [], [], [], []
    for e in _registered_envs(pid):
        if others is None:
            unknown.append({**e, "reason": "project registry unreadable — "
                                           "sharing cannot be ruled out"})
            continue
        refs = _referrers(e["env_id"], others)
        if refs:
            shared.append({**e, "also_in": refs,
                           "reason": "another project names this env"})
            continue
        st = _status(e["env_id"])
        if st is None:
            unknown.append({**e, "reason": "substrate unreachable — "
                                           "not assessed, not touched"})
            continue
        ready = [r for r in st.get("realizations", [])
                 if r.get("state") == "ready"]
        ours = [r for r in ready if not r.get("read_only")]
        adopted = [r for r in ready if r.get("read_only")]
        if ours:
            rebuildable.append({
                **e, "sites": [r.get("site") for r in ours],
                "bytes": sum(int(r.get("bytes") or 0) for r in ours)})
        elif adopted:
            shared.append({**e, "sites": [r.get("site") for r in adopted],
                           "reason": "adopted from a read-only root — its "
                                     "owner manages that lifecycle"})
        else:
            not_realized.append({**e, "reason": "nothing realized — no disk "
                                                "to reclaim"})
    out = {
        "project": pid,
        "rebuildable": rebuildable,
        "shared": shared,
        "not_realized": not_realized,
        "unknown": unknown,
        "reclaimable_bytes": sum(e["bytes"] for e in rebuildable),
        "bytes_note": BYTES_NOTE,
        "note": "dry run; reclaim(pid, confirm=True) executes — "
                "nothing is implicit",
    }
    if valued:
        out["valued"] = _valued(pid)
    return out


def reclaim(pid: str, *, confirm: bool = False) -> dict:
    """Free this project's rebuildable substrate. `confirm=False` returns the
    plan and touches nothing. Best-effort per item; never raises."""
    pid = str(pid)
    p = plan(pid, valued=not confirm)
    if not confirm:
        return p
    from core.compute import named_envs  # noqa: PLC0415
    from core.compute import project_env  # noqa: PLC0415
    # sessions FIRST — a live session holds its base env against env_evict
    try:
        sess = project_env.stop_all_sessions(pid)
    except Exception as e:  # noqa: BLE001
        sess = {"stopped": [], "errors": [f"stop_all_sessions: {e}"]}
    evicted, errors = [], list(sess.get("errors") or [])
    freed = 0
    for e in p["rebuildable"]:
        try:
            res = named_envs.evict(pid, e["name"])
            b = int(res.get("freed_bytes") or 0)
            freed += b
            evicted.append({"name": e["name"], "env_id": e["env_id"],
                            "bytes": b})
        except Exception as ex:  # noqa: BLE001 — one refusal ≠ a failed delete
            errors.append(f"{e['name']}: {ex}")
    return {
        "project": pid,
        "stopped_sessions": sess.get("stopped") or [],
        "evicted": evicted,
        "freed_bytes": freed,
        "kept_shared": p["shared"],
        "not_realized": p["not_realized"],
        "unknown": p["unknown"],
        "errors": errors,
        "bytes_note": BYTES_NOTE,
        "valued_note": "retained outputs, dataset homes and the project "
                       "directory are untouched by design",
    }


# ── orphans ──────────────────────────────────────────────────────────────────
#
# A project's directory and its `weft_envs.json` SURVIVE the delete (they are
# the recovery archive), so a project deleted before this lane existed still
# says, on disk, exactly which envs it held. Nothing extra had to be recorded:
# the orphan sweep is the same three-class plan, run over the ids that have a
# directory but no registry row.
#
# Not automatic. Evicting envs for projects someone deleted months ago must not
# happen as a side effect of a server boot — plan, then confirm.

ORPHAN_GRACE_S = 3600.0
_RESERVED = {"single"}


def _live_ids_strict() -> Optional[set]:
    """Ids the registry lists, or None if the registry cannot be TRUSTED.

    `projects._load()` swallows a read error and returns `[]`, which here would
    mean "every project on disk is an orphan, sweep them all". So the registry
    is read directly and any failure refuses the whole sweep. A registry that
    parses to an empty list IS trustworthy — that is the state after deleting
    the last project."""
    try:
        import json  # noqa: PLC0415
        from core import projects  # noqa: PLC0415
        reg = json.loads(Path(str(projects.REGISTRY)).read_text())
    except Exception:  # noqa: BLE001 — missing, truncated, unparseable
        return None
    if not isinstance(reg, list):
        return None
    return {str(p.get("id")) for p in reg if isinstance(p, dict) and p.get("id")}


def orphan_ids(*, grace_s: float = ORPHAN_GRACE_S) -> "list | None":
    """Project ids with a directory and env records but no registry row.
    None ⇒ the registry could not be trusted and no sweep may run.

    A directory with no `weft_envs.json` never held an env, so there is nothing
    to reclaim and it is not listed. A directory touched inside the grace
    window is skipped: `_db_file` creates the project dir before the registry
    row is written, so a project being created looks exactly like an orphan for
    a moment."""
    live = _live_ids_strict()
    if live is None:
        return None
    from core.config import PROJECTS_DIR  # noqa: PLC0415
    try:
        from core import projects  # noqa: PLC0415
        current = str(projects.current())
    except Exception:  # noqa: BLE001
        current = None
    now = time.time()
    out = []
    try:
        entries = sorted(Path(PROJECTS_DIR).iterdir())
    except Exception:  # noqa: BLE001
        return None
    for d in entries:
        pid = d.name
        if not d.is_dir() or pid.startswith("_") or pid in _RESERVED:
            continue
        if pid in live or pid == current:
            continue
        reg_file = d / "weft_envs.json"
        if not reg_file.exists():
            continue                       # never held an env
        try:
            touched = max(d.stat().st_mtime, reg_file.stat().st_mtime)
        except Exception:  # noqa: BLE001
            touched = now
        if now - touched < grace_s:
            continue                       # mid-creation, or still in use
        out.append(pid)
    return out


def orphans(*, confirm: bool = False,
            grace_s: float = ORPHAN_GRACE_S) -> dict:
    """Reclaim the substrate held by DELETED projects. `confirm=False` plans.

    Per-orphan the classification is unchanged, so an env a LIVE project still
    names is never evicted — `_project_ids` lists the survivors, and an orphan
    is simply absent from that list. Two orphans sharing an env is harmless:
    both are gone, and the second evict is weft's own no-op."""
    ids = orphan_ids(grace_s=grace_s)
    if ids is None:
        return {"refused": "the project registry could not be read — refusing "
                           "to treat every project directory as an orphan",
                "orphans": [], "reclaimable_bytes": 0}
    if not ids:
        return {"orphans": [], "reclaimable_bytes": 0,
                "note": "no deleted project is holding reclaimable substrate"}
    rows, total = [], 0
    for pid in ids:
        if confirm:
            r = reclaim(pid, confirm=True)
            total += int(r.get("freed_bytes") or 0)
        else:
            r = plan(pid, valued=False)
            total += int(r.get("reclaimable_bytes") or 0)
        rows.append(r)
    return {"orphans": rows,
            ("freed_bytes" if confirm else "reclaimable_bytes"): total,
            "bytes_note": BYTES_NOTE,
            "note": ("swept" if confirm else
                     "dry run; orphans(confirm=True) executes — the project "
                     "directories and their recovery archives are never "
                     "touched, only the substrate they still hold")}
