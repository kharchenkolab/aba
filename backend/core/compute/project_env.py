"""Per-project DEFAULT environments as weft SESSIONS — weft rewrite W3.4
(personal installs; misc/weft_rewrite.md §4b, weft doctrine "sessions are for
iteration; snapshot before recording results").

On a pack-declaring deployment, each project's default env (per language) is a
**session over the base pack**. What the session RUNS FROM is the substrate's
fact, consumed as weft's runtime block ({source: session|base, env_id, prefix,
activation, ns_wrap, direct_exec}): the clone may be LAZY — a zero-delta
session runs from the base realization in place until the first install
materializes its own clone (the flip moment). Kernels attach by session_id
(weft activates the right thing); one-shot lanes compose commands with
`argv_for_runtime` (direct prefix exec only when the runtime permits,
activation-wrapped otherwise — squashfs bases are mount-scoped and have no
path outside their activation). `ensure_capability` installs land LIVE in the
session (`session_install`) — the running kernel imports the new package
without a restart, matching the old overlay UX. Frozen identity is minted
exactly when it matters: **background jobs and exports run a
`session_snapshot` EnvID** (dirty-cached — one snapshot per change-set, not
per job; a zero-delta session's snapshot IS the base EnvID), which is what
puts a true EnvID into those exec records.

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

import shlex
import time
from pathlib import Path
from typing import Optional, Sequence

from core.compute import adapter as _adapter
from core.compute import base_env, named_envs
from core.compute.errors import ComputeError

_ECOS = ("conda", "pypi", "cran")


def _exe(language: str) -> str:
    return "Rscript" if language.lower() == "r" else "python"


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
    # Serialized + atomic (named_envs._update) — the default-session write shares
    # weft_envs.json with named-env writes; a plain save would clobber a
    # concurrent named-env create (observed live).
    named_envs._update(str(pid),
                       lambda data: data.setdefault("default", {}).__setitem__(language, row))


# ── session lifecycle ────────────────────────────────────────────────────────
#
# What a session RUNS FROM is the substrate's fact, not ours. Weft's `runtime`
# block ({source: session|base, env_id, prefix, activation, ns_wrap,
# direct_exec}) is the authoritative answer — an unmaterialized (lazy) session
# runs from its base realization in place, a materialized one from its own
# clone, and on squashfs/userns topologies the prefix is MOUNT-SCOPED (exists
# only inside the activation's namespace — a bare `prefix/bin/*` exec is
# wrong there even against an EAGER weft). We therefore never probe the
# filesystem to decide liveness or resolve interpreters; `_shim_runtime` is
# the one compatibility exception, for weft versions that predate the runtime
# contract (those clone eagerly, so a live session always owns a plain
# on-disk prefix).


def _shim_runtime(session_id: str) -> Optional[dict]:
    """Runtime block synthesized for a PRE-RUNTIME weft (eager clones only).
    None ⇒ the prefix is gone ⇒ the session was pruned (session_stop rm -rf's
    the dir) — the caller's cue to rebuild. Activation-shaped like weft's own
    block (the same pixi shell-hook session_exec uses) so callers stay
    topology-blind; deleted with the shim once every deployment's weft
    exposes session_runtime."""
    root = _adapter.weft_workspace() / "site-local" / "sessions" / session_id
    p = root / ".pixi" / "envs" / "default"
    if not p.exists():
        return None
    pixi = _adapter.resolve_pixi() or "pixi"
    return {
        "source": "session", "env_id": None, "prefix": str(p),
        "activation": (f"eval \"$({shlex.quote(pixi)} shell-hook "
                       f"--manifest-path {shlex.quote(str(root / 'pixi.toml'))})\""),
        "ns_wrap": False, "direct_exec": True,
    }


def _current_runtime(session_id: str) -> Optional[dict]:
    """The session's current runtime block, substrate-first: weft's
    session_runtime (observation-only — deliberately does NOT touch the
    session's last_used, so polling can't mask idleness) when this weft
    exposes it, else the eager-clone shim. Returns None when the session is
    GONE (pruned/stopped) — and never for a merely-unmaterialized one; that
    distinction is exactly what the old prefix-existence probe conflated."""
    ad = _adapter.get_compute()
    try:
        rt_call = ad.session_runtime      # AttributeError: pre-runtime weft
    except AttributeError:
        return _shim_runtime(session_id)
    try:
        return named_envs._sync(rt_call(session_id))
    except ComputeError:
        return None                        # task.invalid: no active session


def _ensure_out(session_id: str, base_eid: str, rt: dict) -> dict:
    p = rt.get("prefix")
    # "materialized" = the session owns an on-disk layer of its own: a full
    # clone (source=session) OR an additive overlay riding the base — pylib
    # (cold-base pypi, weft 6070bfc) / rlib (cran layer on ANY base, 80e609d).
    return {"session_id": session_id, "base_env_id": base_eid, "runtime": rt,
            "prefix": Path(p) if p else None,
            "materialized": (rt.get("source") == "session"
                             or bool(rt.get("pylib")) or bool(rt.get("rlib")))}


def ensure(pid: str, language: str) -> dict:
    """The project's default session for `language` (create on first use —
    the lazy-install moment for `first_use` packs). Returns {session_id,
    prefix, base_env_id, runtime, materialized}; `prefix` may be None
    (activation-only topologies) and `materialized=False` means the session
    legitimately runs from its base realization — NOT an error. Liveness is
    the substrate's answer (session_runtime), never prefix existence: a lazy
    live session must not trigger a rebuild (that was the duplicate-session
    leak), and only a truly pruned/lost session is rebuilt from the base pack
    with the recorded additions REPLAYED. Raises ComputeError/RuntimeError
    (module OFF) — surfaced to the agent, never silent."""
    pid = str(pid)
    _gate_module_policy(language)
    base_eid = base_env.env_id(language)
    if base_eid is None:
        raise ComputeError("no_base_pack",
                           f"no {language} base pack declared", stage="aba")
    row = get(pid, language)
    if row and row.get("base_env_id") == base_eid:
        rt = _current_runtime(row["session_id"])
        if rt is not None:
            return _ensure_out(row["session_id"], base_eid, rt)
    # (Re)create — new project, base pack CHANGED, or session pruned. Tell the
    # three apart: a changed base under an existing project is a drift the agent
    # must SEE (old snapshots/EnvIDs no longer match; additions are replayed onto
    # a different base and may behave differently), not a silent swap. The prior
    # code rebuilt against the current base with no signal (I4).
    old_base = (row or {}).get("base_env_id")
    base_changed = bool(row) and old_base is not None and old_base != base_eid
    ad = _adapter.get_compute()
    res = named_envs._sync(ad.session_start(base_eid, "local"))
    sid = res.get("session_id") or res.get("id")
    additions = list((row or {}).get("additions") or [])
    if base_changed:
        print(f"[project_env] base pack for {language!r} changed under project "
              f"{pid} ({str(old_base)[:24]}… → {str(base_eid)[:24]}…); rebuilding "
              f"the default session on the NEW base and replaying "
              f"{len(additions)} recorded addition(s). Snapshots/EnvIDs recorded "
              f"under the old base no longer match — re-run to record results "
              f"under the new env.")
    rt = res.get("runtime")
    for add in additions:                      # replay the recorded deltas
        if add.get("eco") == "installer":      # captured arbitrary installer
            _ikw = {"writes_to": add["writes_to"]} if add.get("writes_to") else {}
            try:
                ires = named_envs._sync(ad.session_run_installer(
                    sid, add.get("cmd") or "", note=add.get("note", ""), **_ikw))
            except TypeError:                  # substrate predates writes_to
                ires = named_envs._sync(ad.session_run_installer(
                    sid, add.get("cmd") or "", note=add.get("note", "")))
        else:
            ires = named_envs._sync(ad.session_install(
                sid, **{add["eco"]: add["specs"]}, **(add.get("opts") or {})))
        # installs are the FLIP moment (base → own clone): the install result
        # carries the fresh runtime; the start-time block is stale after one
        rt = (ires or {}).get("runtime") or rt
    if rt is None:
        rt = _shim_runtime(sid)
        if rt is None:
            # only reachable on a pre-runtime (eager) weft, where a missing
            # prefix after session_start IS a realization failure
            raise ComputeError("env.realize_failed",
                               f"session {sid} has no local prefix", stage="realize")
    new_row = {"session_id": sid, "base_env_id": base_eid,
               "additions": additions, "rev": (row or {}).get("rev", 0),
               "snapshot": None,               # stale after a rebuild
               "created_at": time.time()}
    _save_row(pid, language, new_row)
    out = _ensure_out(sid, base_eid, rt)
    if base_changed:
        out["base_changed"] = {"from": old_base, "to": base_eid,
                               "additions_replayed": len(additions)}
    return out


def runtime(pid: str, language: str) -> dict:
    """The default session's runtime contract: {source: "session"|"base",
    env_id, prefix, activation, ns_wrap, direct_exec}. `activation` is always
    correct; `prefix` only when `direct_exec` (see argv_for_runtime)."""
    return ensure(pid, language)["runtime"]


def argv_for_runtime(rt: dict, language: str, args: Sequence[str], *,
                     pre: Sequence[str] = ()) -> list[str]:
    """argv that runs the default env's interpreter with `args`, topology-blind
    — the ONE builder every one-shot lane (harness, probes, launchers) shares.
    A plain-prefix runtime execs `prefix/bin/<exe>` directly; anything else
    goes through the runtime's activation line, inside a user+mount namespace
    when the substrate says the activation's mounts live only there (ns_wrap —
    squashfs bases). `pre` prepends a wrapper (e.g. stdbuf -oL) in either
    shape. bash mirrors weft's own wrapper choice: conda activate.d hooks
    contain bashisms."""
    tail = [str(a) for a in args]
    head = [str(x) for x in pre]
    exe = _exe(language)
    p = rt.get("prefix")
    if rt.get("direct_exec") and p:
        return [*head, str(Path(p) / "bin" / exe), *tail]
    script = f"{rt['activation']} && exec {shlex.join([*head, exe, *tail])}"
    if rt.get("ns_wrap"):
        script = f"unshare -rm bash -c {shlex.quote(script)}"
    return ["bash", "-c", script]


def exec_argv(pid: str, language: str, args: Sequence[str], *,
              pre: Sequence[str] = ()) -> list[str]:
    """`argv_for_runtime` over the project's current default-session runtime."""
    return argv_for_runtime(runtime(pid, language), language, args, pre=pre)


def prefix(pid: str, language: str) -> Path:
    """The default env's on-disk prefix — only where the runtime permits
    direct filesystem access; raises a typed refusal otherwise (mount-scoped /
    packed bases have no caller-usable path — the old code handed out a
    dangling one). Code that RUNS things wants exec_argv; presentation wants
    runtime()."""
    out = ensure(pid, language)
    rt = out["runtime"]
    if rt.get("direct_exec") and out["prefix"] is not None:
        return out["prefix"]
    raise ComputeError(
        "session.no_direct_exec",
        f"the default {language} env has no directly-accessible prefix on "
        f"this topology (runs from {rt.get('source')!r} via activation); use "
        f"project_env.exec_argv()/runtime()", stage="aba")


def interpreter(pid: str, language: str) -> Path:
    """Direct path to the default env's interpreter — same contract (and same
    honest refusal) as prefix()."""
    return prefix(pid, language) / "bin" / _exe(language)


def install(pid: str, language: str, specs: list[str], *,
            eco: str = "pypi", **opts) -> dict:
    """LIVE install into the project's default session (the running kernel
    sees it after an importlib cache invalidation — no restart). Recorded in
    the registry so a rebuilt session replays it, and the snapshot goes dirty
    (the next background job/export mints a fresh EnvID).

    `specs` speak the SUBSTRATE's spec vocabulary, not a reduced one: for
    eco='cran' that is plain names, `name ==X.Y.Z`, and `owner/repo@ref` git
    sources (weft d51f9fc). Extra `opts` (e.g. `cran_repos=[url]` for a
    secondary repository) ride through to the substrate verb and are recorded
    with the addition, so a rebuilt session replays the same request — send
    them here rather than pre-resolving to a bespoke installer, which is a
    different lane with different (full-realize, refuses-on-cold-base)
    semantics."""
    if eco not in _ECOS:
        raise ValueError(f"eco must be one of {_ECOS} (R goes conda-first on "
                         f"warm bases; eco='cran' layers a session rlib on ANY "
                         f"base — delta-only, no clone; bespoke installers via "
                         f"run_installer)")
    pid = str(pid)
    s = ensure(pid, language)
    ad = _adapter.get_compute()
    out = dict(named_envs._sync(
        ad.session_install(s["session_id"], **{eco: list(specs)}, **opts)) or {})
    row = get(pid, language)
    row["additions"].append({"eco": eco, "specs": list(specs),
                             **({"opts": dict(opts)} if opts else {}),
                             "at": time.time()})
    row["rev"] = int(row.get("rev") or 0) + 1
    _save_row(pid, language, row)
    # an install is the FLIP moment (lazy session materializes its own clone):
    # surface the post-install runtime, never the stale pre-install block
    rt = out.get("runtime") or _current_runtime(s["session_id"]) or s["runtime"]
    out["runtime"] = rt
    p = rt.get("prefix")
    return {"session_id": s["session_id"],
            "prefix": str(p) if p else None, **out}


def run_installer(pid: str, language: str, cmd: str, *, note: str = "",
                  writes_to: Optional[str] = None) -> dict:
    """Escape hatch (captured + portable): arbitrary installer inside the
    session — e.g. source-CRAN `Rscript -e 'install.packages(…)'`. Rides
    snapshots as a labeled post_install step.

    `writes_to='rlib'|'pylib'` DECLARES the write target as the session layer
    (weft d51f9fc): the substrate provisions that layer, points R_LIBS/PIP_TARGET
    at it, and runs the command over the read-only base — so it works on an
    adopted base, where an UNdeclared installer is refused (it could write
    anywhere in the prefix). Declare it whenever the command only adds to the
    session layer. Cost, per the substrate's own result: a post_install spec
    realizes FULL rather than overlay, so prefer `install()` when the addition
    fits the spec vocabulary. Passed through only when set, so an older
    substrate that lacks the parameter is unaffected."""
    pid = str(pid)
    s = ensure(pid, language)
    ad = _adapter.get_compute()
    _kw = {"writes_to": writes_to} if writes_to else {}
    try:
        out = dict(named_envs._sync(
            ad.session_run_installer(s["session_id"], cmd, note=note, **_kw)) or {})
    except TypeError:
        if not _kw:
            raise
        # substrate predates writes_to — retry undeclared (refuses on a cold
        # base, but works wherever it worked before)
        out = dict(named_envs._sync(
            ad.session_run_installer(s["session_id"], cmd, note=note)) or {})
    row = get(pid, language)
    row["additions"].append({"eco": "installer", "cmd": cmd, "note": note,
                             **({"writes_to": writes_to} if writes_to else {}),
                             "at": time.time()})
    row["rev"] = int(row.get("rev") or 0) + 1
    _save_row(pid, language, row)
    # same FLIP handling as install(): callers get the post-install runtime
    out["runtime"] = (out.get("runtime")
                      or _current_runtime(s["session_id"]) or s["runtime"])
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


def stop_all_sessions(pid: str) -> dict:
    """Stop every default session this project owns in the weft store — called
    on project DELETE so the store doesn't leak the project's live prefixes
    (sessions are project-private and can be GBs each). Best-effort: never
    raises, tolerates an offline substrate.

    Deliberately leaves weft_envs.json and the per-project dir IN PLACE (the
    recovery archive reads them; `ensure` rebuilds a stopped session from the
    base + replayed additions). Named/isolated env EnvIDs are content-addressed
    and may be shared across projects — they are weft-GC's to reclaim by LRU,
    never force-dropped here."""
    pid = str(pid)
    try:
        defaults = (named_envs._load(pid).get("default") or {})
    except Exception:  # noqa: BLE001 — unreadable registry ≠ a delete blocker
        return {"stopped": [], "errors": ["registry unreadable"]}
    if not defaults:
        return {"stopped": [], "errors": []}
    try:
        ad = _adapter.get_compute()
    except Exception as e:  # noqa: BLE001 — substrate offline: nothing to stop
        return {"stopped": [], "errors": [f"substrate unavailable: {e}"]}
    stopped, errors = [], []
    for lang, row in defaults.items():
        sid = (row or {}).get("session_id")
        if not sid:
            continue
        try:
            named_envs._sync(ad.session_stop(sid))
            stopped.append(sid)
        except Exception as e:  # noqa: BLE001 — a dead session is already freed
            errors.append(f"{lang}/{sid}: {e}")
    return {"stopped": stopped, "errors": errors}


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
    named_envs._update(pid,
                       lambda data: data.setdefault("default", {}).pop(language, None))
