"""Modules API — Settings → Modules (misc/modules.md).

Server/user-scoped (no project): list capability packs and their live state, toggle
them (enable → background install; disable[?remove] → optional disk reclaim), retry a
failed install, and tail a module's install log. The heavy lifting is in
core.modules.{manager,reconciler}; these are thin HTTP wrappers.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter()


@router.get("/api/modules")
def list_modules() -> dict:
    """All modules with live state (enabled intent + probed actual + progress/error)."""
    from core.modules import manager
    return {"modules": manager.list_modules()}


@router.post("/api/modules/{module_id}/enable")
def enable_module(module_id: str) -> dict:
    """Enable a module: persist the intent and launch its install in the background
    (no-op if already ready/installing). Returns the updated view."""
    from core.modules import reconciler
    view = reconciler.ensure_module(module_id)
    if view is None:
        raise HTTPException(404, f"unknown module: {module_id!r}")
    return view


@router.post("/api/modules/{module_id}/disable")
def disable_module(module_id: str, remove: bool = False) -> dict:
    """Disable a module. Keeps it on disk by default (a 'reclaim space' affordance
    stays available); pass ?remove=true to delete its artifacts now (removable modules
    only — the base-resident python-bio can't be reclaimed)."""
    from core.modules import reconciler
    try:
        view = reconciler.disable_module(module_id, remove=remove)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if view is None:
        raise HTTPException(404, f"unknown module: {module_id!r}")
    return view


@router.post("/api/modules/{module_id}/retry")
def retry_module(module_id: str) -> dict:
    """Retry a failed (or never-run) install — same path as enable."""
    from core.modules import reconciler
    view = reconciler.ensure_module(module_id)
    if view is None:
        raise HTTPException(404, f"unknown module: {module_id!r}")
    return view


@router.get("/api/modules/{module_id}/log")
def module_log(module_id: str, tail: int = 200) -> dict:
    """Tail the module's install log for the Settings → Modules progress view."""
    from core.modules import registry, reconciler
    if registry.get(module_id) is None:
        raise HTTPException(404, f"unknown module: {module_id!r}")
    p = reconciler._log_path(registry.get(module_id))
    if not p.exists():
        return {"lines": []}
    try:
        lines = p.read_text(errors="replace").splitlines()
    except Exception:  # noqa: BLE001
        lines = []
    return {"lines": lines[-max(1, tail):]}
