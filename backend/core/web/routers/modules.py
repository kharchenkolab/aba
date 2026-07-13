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


@router.post("/api/modules/{module_id}/mode")
def set_module_mode(module_id: str, mode: str, remove: bool = False) -> dict:
    """Set a module's state (the 3-state control): mode=on|first_use|off.
      • on → install now; first_use → install on first use; off → don't auto-install.
    ?remove=true with mode=off also reclaims disk (removable modules only)."""
    from core.modules import reconciler
    try:
        view = reconciler.set_mode(module_id, mode, remove=remove)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if view is None:
        raise HTTPException(404, f"unknown module: {module_id!r}")
    return view


@router.post("/api/modules/{module_id}/enable")
def enable_module(module_id: str) -> dict:
    """Convenience: set the module to `on` (install now). Used by one-click 'Enable'
    affordances (e.g. an off-refusal in the viewer/agent path)."""
    return set_module_mode(module_id, "on")


@router.post("/api/modules/{module_id}/disable")
def disable_module(module_id: str, remove: bool = False) -> dict:
    """Convenience: set the module to `off` (optionally reclaiming disk)."""
    return set_module_mode(module_id, "off", remove=remove)


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
