"""Module manager — read-only view over the registry + live readiness (misc/modules.md).

`enabled` = desired intent (state.json) or the registry default. `actual` is PROBED
live (env markers / on-disk artifacts), overlaid with the reconciler's transient
status (queued/installing/failed) so the UI shows real progress without trusting a
stale file. No side effects here — enabling/installing lives in reconciler.py.
"""
from __future__ import annotations

import os
from pathlib import Path

from core import config
from core.modules import registry, state
from core.modules.registry import ModuleSpec


def _aba_home() -> Path:
    return config.aba_home()


def _runtime_dir() -> Path:
    return Path(config.RUNTIME_DIR)


def _tools_env() -> Path:
    # Honor ABA_TOOLS_DIR — same override contract as core.exec.materialize._resolve_tools_env
    # (the R/CLI base is identical for every group, so a fat SIF / OOD deploy points this at a
    # pre-baked, image-resident copy at /opt/aba-envs/tools). Without this the r-bio readiness
    # PROBE looks under $ABA_RUNTIME_DIR/envs/tools, misses the baked env, and first-use then
    # RE-INSTALLS the whole R stack into the writable runtime dir at runtime — a slow,
    # network-dependent rebuild that defeats the frozen image. Unset (dev/normal install) →
    # identical to before.
    override = config.settings.tools_dir.get()
    if override and override.strip():
        return Path(override).resolve()
    return _runtime_dir() / "envs" / "tools"


def _base_env() -> Path:
    try:
        from core.exec.env_integrity import _base_prefix
        return _base_prefix()
    except Exception:  # noqa: BLE001
        return Path(os.environ.get("ENV_DIR", str(_aba_home() / "env")))


def path_vars() -> dict[str, str]:
    """Variables usable in a manifest's `ready`/`remove` paths ($ABA_HOME, $TOOLS_ENV,
    …). One place so probes and removes agree."""
    home = _aba_home()
    # PAGODA3_DIST honors $ABA_PAGODA3_DIST — same contract as the viewer launcher's
    # pagoda3_dist_path() (backend/content/bio/viewers/launchers/pagoda3.py). A fat SIF bakes
    # the dist at /opt/aba/vendor/pagoda3/dist and exports the var; without this the
    # viewer-pagoda3 readiness PROBE looks under $ABA_HOME/vendor/... , misses it, and
    # first-use re-fetches the dist from GitHub at runtime. Unset → identical to before.
    pagoda3_dist = config.settings.pagoda3_dist.get() or str(home / "vendor" / "pagoda3" / "dist")
    return {
        "ABA_HOME": str(home),
        "ABA_RUNTIME_DIR": str(_runtime_dir()),
        "ENV_DIR": str(_base_env()),
        "TOOLS_ENV": str(_tools_env()),
        "PAGODA3_DIST": pagoda3_dist,
    }


def expand_path(p: str) -> Path:
    for k, v in path_vars().items():
        p = p.replace("$" + k, v)
    return Path(p)


def pack_for(spec: ModuleSpec):
    """The declared base env pack ABSORBING this module (weft rewrite W3.4):
    a pack whose name equals the module id (python-bio, r-bio). Pack-backed
    modules keep the 3-state toggle but install through the compute substrate
    (ensure+realize) instead of a shell script. None on pack-less deployments
    (or when the substrate is offline — the shell path still works there)."""
    try:
        from core.compute import env_packs, status
        if not status().get("ok"):
            return None
        for row in env_packs.list_packs():
            if row.get("name") == spec.id and row.get("role") == "base":
                return row["name"]
    except Exception:  # noqa: BLE001
        pass
    return None


def pack_env_id(spec: ModuleSpec) -> tuple[str, str] | None:
    """For a pack-backed module (weft W3.4), the realized LOCAL weft EnvID + site to evict when
    reclaiming disk — `(env_id, "local")`, or None if the module isn't pack-backed, the substrate
    is offline, or the pack has no local realization to reclaim. This is what makes "Reclaim disk
    space" actually free bytes: a pack-backed module's env lives in the weft store keyed by an
    EnvID, NOT at the manifest's pre-weft `$TOOLS_ENV` path (which no longer exists), so reclaim
    must `env_evict` this id rather than rmtree a dead path. The env rebuilds from its lock on
    next use (see core.compute.named_envs)."""
    pack = pack_for(spec)
    if pack is None:
        return None
    try:
        from core.compute import get_compute
        w = get_compute()
        envs = w.sync_call("list_envs")
        rows = envs.get("envs") if isinstance(envs, dict) else envs
        for e in (rows or []):
            if (e.get("name") or "") == pack:
                eid = e.get("env_id") or e.get("id")
                if eid:
                    return (eid, "local")
    except Exception:  # noqa: BLE001 — substrate offline / unrealized → nothing to evict here
        pass
    return None


def _truthy(v) -> bool:
    """Read-only/ready flags cross the substrate boundary serialized as either
    ints (1/0) or STRINGS ("1"/"0"). Plain Python truthiness treats the string
    "0" as True — so a not-yet-ready realization stamped read_only="0" would
    read as READY. Normalize: only genuine truthy tokens count."""
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)


def _pack_ready(pack: str) -> bool:
    """Cheap store-read probe (no solve): the pack's env is adopted OR realized
    READY on the local site.

    Resolve the pack NAME → EnvID via the catalog adoption path FIRST — weft env
    rows are keyed by EnvID and an ADOPTED base leaves NO local spec row, so its
    `name` is empty and matching `list_envs` by name never hits. That was the reason
    mount-adopted base packs (python-bio/r-bio) showed 'pending' forever on the
    SIF/pack deployment despite being live (2026-07-21). Fall back to the name match
    for a locally-SOLVED base (writable deploy: the spec carries a name). An adopted
    RO mount is recorded state=ready read_only=1; accept either signal."""
    try:
        from core.compute import get_compute, seeding
        w = get_compute()
        eid = seeding.adopt_env_id(pack)                 # adopted (catalog) → EnvID
        if not eid:                                      # locally-solved base: match by spec name
            envs = w.sync_call("list_envs")
            rows = envs.get("envs") if isinstance(envs, dict) else envs
            eid = next((e.get("env_id") or e.get("id")
                        for e in (rows or []) if (e.get("name") or "") == pack), None)
        if not eid:
            return False
        st = w.sync_call("env_status", eid)
        return any(r.get("site") == "local"
                   and (r.get("state") == "ready" or _truthy(r.get("read_only")))
                   for r in st.get("realizations", []))
    except Exception:  # noqa: BLE001
        return False


def probe_ready(spec: ModuleSpec) -> bool:
    """Is the module's capability present right now? Interprets the manifest's declarative
    `ready` probe (misc/modules.md) — cheap filesystem/marker checks only, never a solve
    or network call. Unknown/empty probe → False (not ready). Pack-backed
    modules (W3.4) probe the substrate's store instead of the manifest."""
    pack = pack_for(spec)
    if pack is not None:
        return _pack_ready(pack)
    r = spec.ready or {}
    try:
        if "base_stage" in r:
            from core.exec.env_integrity import base_stage
            return base_stage() == r["base_stage"]
        if "path_exists" in r:
            return expand_path(str(r["path_exists"])).exists()
        if "r_package" in r:
            rp = r["r_package"] or {}
            env = _tools_env() if rp.get("env", "tools") == "tools" else _base_env()
            pkg = str(rp.get("package") or "")
            return (env / "bin" / "Rscript").exists() and (env / "lib" / "R" / "library" / pkg).is_dir()
        if "python_import" in r:
            name = str(r["python_import"])
            return bool(list((_base_env() / "lib").glob(f"python*/site-packages/{name}")))
        if "script" in r:
            import subprocess
            return subprocess.run(["bash", str(expand_path(str(r["script"])))],
                                  capture_output=True).returncode == 0
    except Exception:  # noqa: BLE001 — a probe must never raise into a request
        return False
    return False


def _eager_override(module_id: str) -> str | None:
    """`ABA_MODULES_EAGER` seeds heavy modules to `on` for an EAGER deploy — a fat SIF
    bakes r-bio/viewer-pagoda3 (whose registry default is `first_use`) into the frozen
    image, so they should read as `on` (permanently present, shown enabled, not a
    deferred first-use install). Value: space/comma-separated module ids, or `all`/`*`.
    Write-free and lowest-precedence: an explicit user choice in modules.json still wins,
    so a user can still turn a baked module off. Unset → no effect (normal installs)."""
    raw = (config.settings.modules_eager.get() or "").strip()
    if not raw:
        return None
    ids = {t for t in raw.replace(",", " ").split()}
    return "on" if (module_id in ids or "all" in ids or "*" in ids) else None


def mode(spec: ModuleSpec) -> str:
    """Effective state: explicit desired (modules.json) wins, else the eager-deploy
    override (ABA_MODULES_EAGER), else the registry default. One of on | first_use | off."""
    return state.get_desired(spec.id) or _eager_override(spec.id) or spec.default_state


def is_enabled(spec: ModuleSpec) -> bool:
    """True when the module should be installed PROACTIVELY (at boot) — i.e. mode==on.
    (Kept as the reconciler's boot predicate.)"""
    return mode(spec) == "on"


def allows_auto_install(spec: ModuleSpec) -> bool:
    """True when a first-use request may auto-install this module — mode on or
    first_use. False for off (a request is refused with a nudge to enable)."""
    return mode(spec) in ("on", "first_use")


def actual_state(spec: ModuleSpec) -> str:
    """ready | installing | queued | failed | not_installed. A live-ready probe wins
    over any stale transient status (an install that finished out-of-band still reads
    ready); otherwise the reconciler's recorded status applies."""
    if probe_ready(spec):
        return "ready"
    st = state.get_status(spec.id)["status"]
    if st in ("installing", "queued", "failed"):
        return st
    return "not_installed"


def deployment_immutable() -> bool:
    """A frozen/baked deployment (a fat SIF): the base env is a READ-ONLY mount, so modules
    cannot be installed, removed, or toggled at runtime — the image must be rebuilt to change
    them. When True the UI locks the module controls and the reconciler refuses mutations.
    False for a normal (writable) install → modules stay toggleable, unchanged behavior."""
    try:
        be = _base_env()
        return be.exists() and not os.access(be, os.W_OK)
    except Exception:  # noqa: BLE001
        return False


def module_view(spec: ModuleSpec) -> dict:
    st = state.get_status(spec.id)
    actual = actual_state(spec)
    ready = actual == "ready"
    return {
        "id": spec.id,
        "title": spec.title,
        "description": spec.description,
        "size": spec.size,
        "est_time": spec.est_time,
        "default_state": spec.default_state,
        "mode": mode(spec),                     # on | first_use | off (the 3-state control)
        "removable": spec.removable,
        "first_use": list(spec.first_use),
        "enabled": is_enabled(spec),            # mode==on (back-compat)
        "actual": actual,
        "on_disk": ready,                       # ready ⟹ artifacts present (drives reclaim-space link)
        "locked": deployment_immutable(),       # baked read-only image → UI disables toggle/reclaim
        "progress": st["progress"],
        "error": st["error"],
        "version": st["version"],
    }


def list_modules() -> list[dict]:
    return [module_view(m) for m in registry.all_modules()]


def get_view(module_id: str) -> dict | None:
    spec = registry.get(module_id)
    return module_view(spec) if spec else None
