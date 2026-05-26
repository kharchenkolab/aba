"""Proposals table CRUD: signature dedup, accept/dismiss/undo plumbing.
The framework only — what gets proposed lives in content."""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn, _utcnow
from core.graph.audit import log_event


def _row_to_proposal(r) -> dict:
    return {
        "id": r["id"],
        "thread_id": r["thread_id"],
        "kind": r["kind"],
        "advisor": r["advisor"],
        "headline": r["headline"],
        "body": r["body"],
        "payload": json.loads(r["payload"]) if r["payload"] else None,
        "signature": r["signature"],
        "status": r["status"],
        "result_id": r["result_id"],
        "undo_data": json.loads(r["undo_data"]) if r["undo_data"] else None,
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def proposal_signature_exists(signature: str) -> bool:
    """Dedup gate: has this exact proposal ever been raised (any status)? We
    suppress re-raising across pending/accepted/dismissed so a dismissed idea
    doesn't re-nag until the world changes (which yields a new signature)."""
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM proposals WHERE signature = ? LIMIT 1", (signature,)
        ).fetchone()
    return row is not None


def add_proposal(*, thread_id: Optional[str], kind: str, headline: str,
                 signature: str, advisor: str = "guide", body: str = "",
                 payload: Optional[dict] = None) -> Optional[int]:
    """Insert a pending proposal. Returns None (no-op) if an identical-signature
    proposal already exists."""
    if proposal_signature_exists(signature):
        return None
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO proposals (thread_id, kind, advisor, headline, body, "
            "payload, signature, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
            (thread_id, kind, advisor, headline, body,
             json.dumps(payload) if payload else None, signature, now, now),
        )
        c.commit()
        pid = cur.lastrowid
    log_event("proposal", entity_id=thread_id, title=headline,
              detail={"kind": kind, "advisor": advisor})
    return pid


def list_proposals(thread_id: Optional[str] = None,
                   status: Optional[str] = "pending") -> list[dict]:
    q = "SELECT * FROM proposals"
    conds, args = [], []
    if thread_id is not None:
        conds.append("thread_id = ?"); args.append(thread_id)
    if status is not None:
        conds.append("status = ?"); args.append(status)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    q += " ORDER BY id DESC"
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [_row_to_proposal(r) for r in rows]


def get_proposal(pid: int) -> Optional[dict]:
    with _conn() as c:
        row = c.execute("SELECT * FROM proposals WHERE id = ?", (pid,)).fetchone()
    return _row_to_proposal(row) if row else None


def update_proposal(pid: int, *, status: Optional[str] = None,
                    result_id: Optional[str] = None,
                    undo_data: Optional[dict] = None) -> bool:
    sets, args = [], []
    if status is not None:
        sets.append("status = ?"); args.append(status)
    if result_id is not None:
        sets.append("result_id = ?"); args.append(result_id)
    if undo_data is not None:
        sets.append("undo_data = ?"); args.append(json.dumps(undo_data))
    if not sets:
        return False
    sets.append("updated_at = ?"); args.append(_utcnow())
    args.append(pid)
    with _conn() as c:
        cur = c.execute(f"UPDATE proposals SET {', '.join(sets)} WHERE id = ?", args)
        c.commit()
        return cur.rowcount > 0
