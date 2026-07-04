"""Job rows — background long-running tools. Domain-neutral.

Jobs are stored in per-project DBs (so a project's deletion cleans up its
jobs naturally). The async worker, by contrast, is global to the backend
process — it dequeues jobs across all projects. To bridge that, get_job /
update_job accept an optional `project_id` that opens THAT project's DB
directly (via _project_conn) without mutating the request-scoped DB_PATH.
Legacy callers that pass project_id=None keep operating on the current
project's DB unchanged.
"""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn, _project_conn, _utcnow, _column_exists


def _row_to_job(r) -> dict:
    return {
        "id": r["id"],
        "kind": r["kind"],
        "title": r["title"],
        "status": r["status"],
        "focus_entity_id": r["focus_entity_id"],
        "params": json.loads(r["params"]) if r["params"] else None,
        "log_tail": r["log_tail"],
        "error": r["error"],
        "created_at": r["created_at"],
        "started_at": r["started_at"],
        "finished_at": r["finished_at"],
    }


def create_job(job_id: str, kind: str, title: str, focus_entity_id: Optional[str],
               params: dict, project_id: Optional[str] = None) -> dict:
    now = _utcnow()
    # Pin-on-launch (slim SIF): record the release this job was submitted under so it — and any
    # Nextflow auto-resume of it — reuse THAT release even if `current` is later repointed
    # (misc/slim_sif_deploy.md §3). No-op without $ABA_SHARE/ABA_RELEASE_ID → personal/fat installs
    # persist no release_id and are unaffected.
    try:
        from core.release import stamp_release
        params = stamp_release(params)
    except Exception:  # noqa: BLE001 — release layer is optional; never block job creation
        pass
    with _project_conn(project_id) as c:
        c.execute(
            "INSERT INTO jobs (id, kind, title, status, focus_entity_id, params, created_at) "
            "VALUES (?, ?, ?, 'queued', ?, ?, ?)",
            (job_id, kind, title, focus_entity_id, json.dumps(params), now),
        )
        c.commit()
    return get_job(job_id, project_id=project_id)  # type: ignore[return-value]


def get_job(job_id: str, project_id: Optional[str] = None) -> Optional[dict]:
    with _project_conn(project_id) as c:
        r = c.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(r) if r else None


def list_jobs(limit: int = 50, project_id: Optional[str] = None,
              statuses: Optional[list[str]] = None,
              include_archived: bool = False) -> list[dict]:
    wheres: list[str] = []
    args: list = []
    with _project_conn(project_id) as c:
        # Defensive: per-project DBs are opened without running init_db's migrations
        # (_project_conn just connects), so an older DB may lack archived_at. Only filter
        # on it when present — never let the Jobs list 500 on an un-migrated project DB.
        if not include_archived and _column_exists(c, "jobs", "archived_at"):
            wheres.append("archived_at IS NULL")
        if statuses:
            wheres.append("status IN (" + ",".join("?" * len(statuses)) + ")")
            args.extend(statuses)
        q = "SELECT * FROM jobs"
        if wheres:
            q += " WHERE " + " AND ".join(wheres)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(q, args).fetchall()
    return [_row_to_job(r) for r in rows]


# Terminal states a job can be dismissed / auto-retained from.
_TERMINAL = ("done", "failed", "cancelled")


def _ensure_archived_col(c) -> bool:
    """Self-heal: add jobs.archived_at on THIS connection's DB if missing (per-project DBs
    aren't migrated by _project_conn). Callers that write archived_at use this first."""
    if _column_exists(c, "jobs", "archived_at"):
        return True
    try:
        c.execute("ALTER TABLE jobs ADD COLUMN archived_at TEXT")
        c.commit()
        return True
    except Exception:  # noqa: BLE001
        return False


def archive_job(job_id: str, project_id: Optional[str] = None) -> bool:
    """Soft-hide a job from list_jobs (provenance preserved; get_job still returns it).
    Only terminal jobs are archivable — refuse to hide an active (queued/running) one."""
    with _project_conn(project_id) as c:
        if not _ensure_archived_col(c):
            return False
        r = c.execute("SELECT status, archived_at FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if r is None or r["status"] not in _TERMINAL or r["archived_at"] is not None:
            return False
        c.execute("UPDATE jobs SET archived_at = ? WHERE id = ?", (_utcnow(), job_id))
        c.commit()
    return True


def prune_terminal_jobs(project_id: Optional[str] = None, keep: int = 30) -> int:
    """Auto-retention: archive terminal jobs beyond the `keep` most-recent (per project),
    so the Jobs list can't grow unbounded. Active jobs and the newest `keep` terminal ones
    are untouched. Returns the number archived."""
    with _project_conn(project_id) as c:
        if not _ensure_archived_col(c):
            return 0
        rows = c.execute(
            "SELECT id FROM jobs WHERE status IN ('done','failed','cancelled') "
            "AND archived_at IS NULL ORDER BY created_at DESC LIMIT -1 OFFSET ?",
            (keep,),
        ).fetchall()
        ids = [r["id"] for r in rows]
        if ids:
            now = _utcnow()
            c.executemany("UPDATE jobs SET archived_at = ? WHERE id = ?",
                          [(now, i) for i in ids])
            c.commit()
    return len(ids)


def update_job(job_id: str, project_id: Optional[str] = None, **fields) -> None:
    allowed = {"status", "log_tail", "error", "started_at", "finished_at", "params"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            if k == "params" and not isinstance(v, str):
                v = json.dumps(v)        # the params column is JSON (slurm_id etc.)
            sets.append(f"{k} = ?"); args.append(v)
    if not sets:
        return
    args.append(job_id)
    with _project_conn(project_id) as c:
        c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
        c.commit()
