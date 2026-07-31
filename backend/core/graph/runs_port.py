"""Runs read-port — typed reads over the `runs` table.

The store ratchet (tests/check_store_port.py) confines raw SQL to
core/graph/; consumers that need run rows (the Record's World assembler,
sitting derivation) read through here instead of reaching for `_conn`.
Read-only: no writer belongs in this module (turn checkpointing owns the
writes — core/runtime/checkpoint.py, a grandfathered raw-SQL site).
"""
from __future__ import annotations

from typing import Optional

from core.graph._schema import _conn

_COLS = ("run_id", "session_id", "turn_index", "agent_spec_name", "state",
         "focus_entity_id", "thread_id", "started_at", "updated_at")


def list_runs(thread_id: Optional[str] = None, *,
              limit: Optional[int] = None) -> list[dict]:
    """Run rows (no blobs), oldest first — the order sitting derivation
    consumes. `thread_id` filters when given; `limit` keeps the newest N
    (still returned oldest-first)."""
    q = f"SELECT {', '.join(_COLS)} FROM runs"
    args: list = []
    if thread_id is not None:
        q += " WHERE thread_id = ?"
        args.append(thread_id)
    q += " ORDER BY COALESCE(started_at, updated_at) DESC, run_id DESC"
    if limit is not None:
        q += " LIMIT ?"
        args.append(int(limit))
    with _conn() as c:
        rows = [dict(r) for r in c.execute(q, args).fetchall()]
    rows.reverse()
    return rows
