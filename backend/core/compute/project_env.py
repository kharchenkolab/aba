"""Per-project DEFAULT environments as weft SESSIONS — weft rewrite W3.4
(personal installs; misc/weft_rewrite.md §4b, weft doctrine "sessions are for
iteration; snapshot before recording results").

On a pack-declaring deployment, each project's default env (per language) is a
**session cloned from the base pack**: kernels and one-shot runs execute the
session's prefix, and `ensure_capability` installs land LIVE in it
(`session_install`) — the running kernel imports the new package without a
restart, matching the old overlay UX. Frozen identity is minted exactly when
it matters: **background jobs and exports run a `session_snapshot` EnvID**
(dirty-cached — one snapshot per change-set, not per job), which is what puts
a true EnvID into those exec records.

Policy: the Settings → Modules toggles govern packs. A pack whose module is
OFF refuses with the enable prompt (never silently solves a toolchain the user
turned off); `first_use` solves on first demand (this module's ensure());
`on` is ensured eagerly at boot by the reconciler.

Registry: the `default` key of `PROJECTS_DIR/<pid>/weft_envs.json` (shared
with named_envs): per language `{session_id, base_env_id, additions[],
snapshot{env_id, at_rev}, rev}`. Sessions persist in weft's store; a pruned
session is rebuilt from the base and its recorded additions REPLAYED — the
registry, not the session dir, is the durable truth.

Sync, worker-thread callable (same rules as named_envs).
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

from core.compute import adapter as _adapter
from core.compute import base_env, named_envs
from core.compute.errors import ComputeError

_ECOS = ("conda", "pypi")


def _gate_module_policy(language: str) -> None:
    """Honor the lazy-install toggles: a base pack whose module id is OFF must
    refuse with the one-click-enable prompt (the same contract the old r-bio
    gate had), never silently solve. Unknown module id → no gate (a pack
    without a Settings row is always-on deployment config)."""
    pack = base_env.pack_name(language)
    if not pack:
        return
    try:
        from core.modules import registry as _mreg, manager as _mmgr
        spec = _mreg.get(pack)
        if spec is not None and _mmgr.mode(spec) == "off":
            raise RuntimeError(
                f"The {language} environment pack ({pack!r}) is turned OFF. Ask "
                f"the user to enable it by calling ask_clarification(question=\"…\", "
                f"enable_module=\"{pack}\") — that shows one-click Enable buttons "
                f"(On / First use). Don't paste Settings instructions.")
    except RuntimeError:
        raise
    except Exception:  # noqa: BLE001 — module wiring must never break the lane
        pass


# ── registry (shares weft_envs.json with named_envs) ─────────────────────────

def _load(pid: str) -> dict:
    data = named_envs._load(pid)
    data.setdefault("default", {})
    return data


def get(pid: str, language: str) -> Optional[dict]:
    return _load(str(pid))["default"].get(language)


def _save_row(pid: str, language: str, row: dict) -> None:
    data = _load(str(pid))
    data["default"][language] = row
    named_envs._save(str(pid), data)


# ── session lifecycle ────────────────────────────────────────────────────────

def _session_prefix(session_id: str) -> Optional[Path]:
    """The session's live prefix on the local site. Session location is
    deterministic (`sessions/<id>` under the site root — weft's session_exec
    activates `<location>/pixi.toml`, interpreter under `.pixi/envs/default`);
    existence doubles as the liveness check (session_stop rm -rf's the dir)."""
    p = (_adapter.weft_workspace() / "site-local" / "sessions" / session_id
         / ".pixi" / "envs" / "default")
    return p if p.exists() else None


def ensure(pid: str, language: str) -> dict:
    """The project's default session for `language` (create on first use —
    the lazy-install moment for `first_use` packs). Returns {session_id,
    prefix, base_env_id}. A pruned/lost session is rebuilt from the base pack
    and the recorded additions are REPLAYED. Raises ComputeError/RuntimeError
    (module OFF) — surfaced to the agent, never silent."""
    pid = str(pid)
    _gate_module_policy(language)
    base_eid = base_env.env_id(language)
    if base_eid is None:
        raise ComputeError("no_base_pack",
                           f"no {language} base pack declared", stage="aba")
    row = get(pid, language)
    if row and row.get("base_env_id") == base_eid:
        prefix = _session_prefix(row["session_id"])
        if prefix is not None:
            return {"session_id": row["session_id"], "prefix": prefix,
                    "base_env_id": base_eid}
    # (Re)create — new project, base pack upgraded, or session pruned.
    ad = _adapter.get_compute()
    res = named_envs._sync(ad.session_start(base_eid, "local"))
    sid = res.get("session_id") or res.get("id")
    additions = list((row or {}).get("additions") or [])
    for add in additions:                      # replay the recorded deltas
        named_envs._sync(ad.session_install(sid, **{add["eco"]: add["specs"]}))
    new_row = {"session_id": sid, "base_env_id": base_eid,
               "additions": additions, "rev": (row or {}).get("rev", 0),
               "snapshot": None,               # stale after a rebuild
               "created_at": time.time()}
    _save_row(pid, language, new_row)
    prefix = _session_prefix(sid)
    if prefix is None:
        raise ComputeError("env.realize_failed",
                           f"session {sid} has no local prefix", stage="realize")
    return {"session_id": sid, "prefix": prefix, "base_env_id": base_eid}


def prefix(pid: str, language: str) -> Path:
    return ensure(pid, language)["prefix"]


def interpreter(pid: str, language: str) -> Path:
    exe = "Rscript" if language.lower() == "r" else "python"
    return prefix(pid, language) / "bin" / exe


def install(pid: str, language: str, specs: list[str], *,
            eco: str = "pypi") -> dict:
    """LIVE install into the project's default session (the running kernel
    sees it after an importlib cache invalidation — no restart). Recorded in
    the registry so a rebuilt session replays it, and the snapshot goes dirty
    (the next background job/export mints a fresh EnvID)."""
    if eco not in _ECOS:
        raise ValueError(f"eco must be one of {_ECOS} (R goes conda-first; "
                         f"source-CRAN via run_installer)")
    pid = str(pid)
    s = ensure(pid, language)
    ad = _adapter.get_compute()
    out = named_envs._sync(ad.session_install(s["session_id"], **{eco: list(specs)}))
    row = get(pid, language)
    row["additions"].append({"eco": eco, "specs": list(specs), "at": time.time()})
    row["rev"] = int(row.get("rev") or 0) + 1
    _save_row(pid, language, row)
    return {"session_id": s["session_id"], "prefix": str(s["prefix"]), **(out or {})}


def run_installer(pid: str, language: str, cmd: str, *, note: str = "") -> dict:
    """Escape hatch (captured + portable): arbitrary installer inside the
    session — e.g. source-CRAN `Rscript -e 'install.packages(…)'`. Rides
    snapshots as a labeled post_install step."""
    pid = str(pid)
    s = ensure(pid, language)
    ad = _adapter.get_compute()
    out = named_envs._sync(ad.session_run_installer(s["session_id"], cmd, note=note))
    row = get(pid, language)
    row["additions"].append({"eco": "installer", "cmd": cmd, "note": note,
                             "at": time.time()})
    row["rev"] = int(row.get("rev") or 0) + 1
    _save_row(pid, language, row)
    return out


def snapshot(pid: str, language: str) -> str:
    """A FROZEN EnvID of the session's current state (for background jobs and
    exports). Dirty-cached: unchanged session → the previous snapshot's id
    (identity is content-addressed; re-snapshotting an unchanged set would
    yield the same env anyway — this just skips the round trip)."""
    pid = str(pid)
    s = ensure(pid, language)
    row = get(pid, language)
    snap = row.get("snapshot") or {}
    if snap.get("env_id") and snap.get("at_rev") == row.get("rev"):
        return snap["env_id"]
    ad = _adapter.get_compute()
    res = named_envs._sync(ad.session_snapshot(
        s["session_id"], name=f"aba-{pid}-default-{language}"))
    eid = res["env_id"]
    row["snapshot"] = {"env_id": eid, "at_rev": row.get("rev"),
                       "at": time.time()}
    _save_row(pid, language, row)
    return eid


def reset(pid: str, language: str) -> None:
    """Drop the project's default session (next use re-clones the base pack;
    recorded additions are NOT replayed — reset means reset)."""
    pid = str(pid)
    row = get(pid, language)
    if not row:
        return
    try:
        named_envs._sync(_adapter.get_compute().session_stop(row["session_id"]))
    except Exception:  # noqa: BLE001 — a dead session is already reset
        pass
    data = _load(pid)
    data["default"].pop(language, None)
    named_envs._save(pid, data)
