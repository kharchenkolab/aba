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

from core.graph._schema import _conn, _project_conn, _utcnow


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
              statuses: Optional[list[str]] = None) -> list[dict]:
    q = "SELECT * FROM jobs"
    args: list = []
    if statuses:
        q += " WHERE status IN (" + ",".join("?" * len(statuses)) + ")"
        args.extend(statuses)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    with _project_conn(project_id) as c:
        rows = c.execute(q, args).fetchall()
    return [_row_to_job(r) for r in rows]


def update_job(job_id: str, project_id: Optional[str] = None, **fields) -> None:
    allowed = {"status", "log_tail", "error", "started_at", "finished_at"}
    sets, args = [], []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?"); args.append(v)
    if not sets:
        return
    args.append(job_id)
    with _project_conn(project_id) as c:
        c.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", args)
        c.commit()
