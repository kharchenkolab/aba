"""The scratch tier (data.md §4.1): per-project, per-run working directories.

A run executes inside ``WORK_DIR/<project_id>/<run_id>/`` with that dir as its
cwd. The sandbox reads/writes intermediates there freely, by plain path — the
store does not mediate it. Scratch persists across the turns of a run (so the
agent can revisit multi-file output) and is reclaimed on a TTL; nothing here is
a tracked entity until something is explicitly registered (kept outputs go to
the content-addressed artifact store, not here).
"""
from __future__ import annotations
import shutil
import time
from pathlib import Path

from core.config import WORK_DIR

# How long an idle scratch run dir survives before GC. Generous so the agent
# can come back to its working files across a session; small enough that a VM
# doesn't fill with abandoned intermediates.
SCRATCH_TTL_HOURS = 48


def _project_root(project_id: str) -> Path:
    return WORK_DIR / (project_id or "default")


def scratch_dir(project_id: str, run_id: str) -> Path:
    """Return (creating) the scratch working dir for a run. Opportunistically
    GCs stale sibling run dirs for this project so cleanup needs no separate
    scheduler in P0."""
    root = _project_root(project_id)
    root.mkdir(parents=True, exist_ok=True)
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
    call on startup / project switch; idempotent."""
    removed = 0
    if not WORK_DIR.exists():
        return 0
    cutoff = time.time() - ttl_hours * 3600
    for proj in WORK_DIR.iterdir():
        if not proj.is_dir():
            continue
        for run in proj.iterdir():
            if run.is_dir():
                try:
                    if run.stat().st_mtime < cutoff:
                        shutil.rmtree(run, ignore_errors=True)
                        removed += 1
                except OSError:
                    pass
    return removed
