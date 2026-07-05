"""Project-lifecycle routes — list/current/create/open/rename/delete + recovery
report/verify, plus the read-only /api/env layers view. Extracted from main.py
(Item 2A.3). Domain-neutral (core.projects / core.config / core.recovery / core.exec).

Pinning: these are project-lifecycle or global reads — create/open/rename/delete/
verify-recovery are exempt in the pin gate (pid is in the path or there's no
project yet); the rest are GETs. Matches prior main.py behavior.
(`/api/projects/{pid}/materialize` stays in main for now — it's bio-coupled → 2A.4.)
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter()


class ProjectRequest(BaseModel):
    name: str = ""


@router.get("/api/projects")
def projects_list():
    from core import projects
    return projects.list_projects()


@router.get("/api/projects/current")
def projects_current():
    from core import projects
    return {"current": projects.current()}


@router.get("/api/env")
def api_env(project_id: str | None = None):
    """The layered Python + R environments with their packages — backs the
    (i)-drawer Env tab. Python via dist-info scan (fast); R via one Rscript
    (~2s). Read-only."""
    from core import projects
    from core.exec.env_integrity import env_layers
    return env_layers(project_id or projects.current())


@router.post("/api/projects")
def projects_create(req: ProjectRequest):
    from core import projects
    return projects.create_project(req.name)


@router.post("/api/projects/{pid}/open")
def projects_open(pid: str):
    from core import projects
    projects.set_current(pid)
    return {"current": projects.current()}


@router.patch("/api/projects/{pid}")
def projects_rename(pid: str, req: ProjectRequest):
    from core import projects
    projects.rename_project(pid, req.name)
    return {"ok": True}


@router.delete("/api/projects/{pid}")
def projects_delete(pid: str):
    from core import projects
    projects.delete_project(pid)
    return {"current": projects.current()}


@router.get("/api/projects/{pid}/recovery-report")
def projects_recovery_report(pid: str):
    """The most recent recovery_report.json for this project, or null if
    the project hasn't been imported via aba-recover (i.e. it was created
    here and has no compatibility issues to surface)."""
    from core.config import project_root
    from pathlib import Path
    rp = project_root(pid) / "recovery_report.json"
    if not rp.exists():
        return None
    try:
        import json as _json
        return _json.loads(rp.read_text())
    except Exception:
        return None


@router.post("/api/projects/{pid}/verify-recovery")
def projects_verify_recovery(pid: str, depth: str = "full"):
    """On-demand drift check from the project ⋯ menu's
    'Verify recovery archive' button (recovery.md § 10.0)."""
    from core.recovery.drift import compute_drift
    from core.config import project_root
    rep = compute_drift(project_root(pid), depth=depth)
    return rep.to_dict()
