"""Module manager — read-only view over the registry + live readiness (misc/modules.md).

`enabled` = desired intent (state.json) or the registry default. `actual` is PROBED
live (env markers / on-disk artifacts), overlaid with the reconciler's transient
status (queued/installing/failed) so the UI shows real progress without trusting a
stale file. No side effects here — enabling/installing lives in reconciler.py.
"""
from __future__ import annotations

import os
from pathlib import Path

from core.modules import registry, state
from core.modules.registry import ModuleSpec


def _aba_home() -> Path:
    return Path(os.environ.get("ABA_HOME", str(Path.home() / ".aba")))


def _runtime_dir() -> Path:
    return Path(os.environ.get("ABA_RUNTIME_DIR", str(_aba_home() / "runtime")))


def _tools_env() -> Path:
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
    return {
        "ABA_HOME": str(home),
        "ABA_RUNTIME_DIR": str(_runtime_dir()),
        "ENV_DIR": str(_base_env()),
        "TOOLS_ENV": str(_tools_env()),
        "PAGODA3_DIST": str(home / "vendor" / "pagoda3" / "dist"),
    }


def expand_path(p: str) -> Path:
    for k, v in path_vars().items():
        p = p.replace("$" + k, v)
    return Path(p)


def probe_ready(spec: ModuleSpec) -> bool:
    """Is the module's capability present right now? Interprets the manifest's declarative
    `ready` probe (misc/modules.md) — cheap filesystem/marker checks only, never a solve
    or network call. Unknown/empty probe → False (not ready)."""
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


def mode(spec: ModuleSpec) -> str:
    """Effective state: explicit desired (modules.json) wins, else the registry
    default. One of on | first_use | off."""
    return state.get_desired(spec.id) or spec.default_state


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
        "progress": st["progress"],
        "error": st["error"],
        "version": st["version"],
    }


def list_modules() -> list[dict]:
    return [module_view(m) for m in registry.all_modules()]


def get_view(module_id: str) -> dict | None:
    spec = registry.get(module_id)
    return module_view(spec) if spec else None
