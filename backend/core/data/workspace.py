"""The scratch tier (data.md §4.1): per-project, per-run working directories.

A run executes inside ``projects/<project_id>/work/<run_id>/`` with that dir as
its cwd (post 2026-05-31 reorg — pre-reorg this was ``WORK_DIR/<pid>/<run>/``).
The sandbox reads/writes intermediates there freely, by plain path — the store
does not mediate it. Scratch persists across the turns of a run (so the agent
can revisit multi-file output) and is reclaimed on a TTL; nothing here is a
tracked entity until something is explicitly registered.
"""
from __future__ import annotations
import shutil
import time
from pathlib import Path

from core.config import PROJECTS_DIR, project_work_dir

# How long an idle scratch run dir survives before GC. Generous so the agent
# can come back to its working files across a session; small enough that a VM
# doesn't fill with abandoned intermediates.
SCRATCH_TTL_HOURS = 48


def _project_work_root(project_id: str) -> Path:
    return project_work_dir(project_id or "_workspace")


def scratch_dir(project_id: str, run_id: str) -> Path:
    """Return (creating) the scratch working dir for a run. Opportunistically
    GCs stale sibling run dirs for this project so cleanup needs no separate
    scheduler in P0."""
    root = _project_work_root(project_id)
    _gc_project(root)
    d = root / (run_id or "run")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gc_project(root: Path, ttl_hours: float = SCRATCH_TTL_HOURS) -> None:
    cutoff = time.time() - ttl_hours * 3600
    try:
        for child in root.iterdir():
            if not child.is_dir():
                continue
            try:
                if child.stat().st_mtime < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
            except OSError:
                pass
    except OSError:
        pass


def gc_scratch(ttl_hours: float = SCRATCH_TTL_HOURS) -> int:
    """Reap stale run dirs across all projects. Returns count removed. Safe to
    call on startup / project switch; idempotent. Walks projects/<pid>/work/."""
    removed = 0
    if not PROJECTS_DIR.exists():
        return 0
    cutoff = time.time() - ttl_hours * 3600
    for proj_root in PROJECTS_DIR.iterdir():
        if not proj_root.is_dir():
            continue
        work = proj_root / "work"
        if not work.is_dir():
            continue
        for run in work.iterdir():
            if run.is_dir():
                try:
                    if run.stat().st_mtime < cutoff:
                        shutil.rmtree(run, ignore_errors=True)
                        removed += 1
                except OSError:
                    pass
    return removed
