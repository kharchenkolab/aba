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


def _pagoda3_dist() -> Path:
    return _aba_home() / "vendor" / "pagoda3" / "dist" / "index.html"


def probe_ready(spec: ModuleSpec) -> bool:
    """Is the module's capability actually present right now? Cheap filesystem/marker
    checks only — never a solve or a network call."""
    try:
        if spec.id == "python-bio":
            from core.exec.env_integrity import base_stage
            return base_stage() == "ready"
        if spec.id == "r-bio":
            t = _tools_env()
            return (t / "bin" / "Rscript").exists() and (t / "lib" / "R" / "library" / "Seurat").is_dir()
        if spec.id == "viewer-pagoda3":
            return _pagoda3_dist().exists()
    except Exception:  # noqa: BLE001 — a probe must never raise into a request
        return False
    return False


def is_enabled(spec: ModuleSpec) -> bool:
    """Desired intent: explicit state wins, else the registry default."""
    desired = state.get_desired(spec.id)
    if desired == "enabled":
        return True
    if desired == "disabled":
        return False
    return spec.default_enabled


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
        "default_enabled": spec.default_enabled,
        "removable": spec.removable,
        "first_use": list(spec.first_use),
        "enabled": is_enabled(spec),
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
