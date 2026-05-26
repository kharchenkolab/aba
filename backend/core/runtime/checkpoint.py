"""Turn persistence — upsert a Turn into the runs table on every transition.

Cheap (~10ms with SQLite WAL); call on every state change. Loading is a
single row read; load_turn() yields a fully-rehydrated Turn or None.
"""
from __future__ import annotations
from typing import Optional

from core.graph._schema import _conn
from core.runtime.turn import Turn


def checkpoint(turn: Turn) -> None:
    """Upsert the Turn row. Idempotent on run_id; safe to call as often as
    convenient (the cost is one SQLite write)."""
    row = turn.to_row()
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "run_id")
    with _conn() as c:
        c.execute(
            f"INSERT INTO runs ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(run_id) DO UPDATE SET {updates}",
            [row[c] for c in cols],
        )
        c.commit()


def load_turn(run_id: str) -> Optional[Turn]:
    with _conn() as c:
        r = c.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    return Turn.from_row(r) if r else None


def list_recent_turns(limit: int = 50) -> list[dict]:
    """For /api/turns (diagnostic): the recent run roster."""
    with _conn() as c:
        rows = c.execute(
            "SELECT run_id, session_id, turn_index, agent_spec_name, state, "
            "       focus_entity_id, thread_id, started_at, updated_at "
            "FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
