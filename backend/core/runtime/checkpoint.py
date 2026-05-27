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


def reap_stale_turns() -> int:
    """At process startup: mark any Turn left in GENERATING/EXECUTING_TOOLS/
    SUMMARIZING state as FAILED (reason: 'process restarted'). Those turns
    have no recovery path today — the new process can't continue them
    because the LLM stream/tool dispatch is in-memory. Returns the number
    of rows reaped.

    AWAITING_USER turns are left alone — the user's next message resumes
    them via the normal chat flow.
    """
    import json
    stale_states = ("generating", "executing_tools", "summarizing")
    reason = json.dumps({"type": "ProcessRestart",
                         "message": "process restarted before the turn completed"})
    placeholders = ", ".join("?" for _ in stale_states)
    with _conn() as c:
        cur = c.execute(
            f"UPDATE runs SET state='failed', error_blob=?, updated_at=? "
            f"WHERE state IN ({placeholders})",
            (reason, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), *stale_states),
        )
        c.commit()
        return cur.rowcount or 0


def cancel_turn(run_id: str, *, reason: str = "user cancelled") -> bool:
    """Mark a Turn FAILED — used by POST /api/turns/{id}/cancel and as
    the explicit cleanup primitive when the UI knows an in-flight turn
    isn't coming back."""
    import json
    t = load_turn(run_id)
    if t is None:
        return False
    if t.state.value in ("done", "failed"):
        return False
    err = json.dumps({"type": "Cancelled", "message": reason})
    with _conn() as c:
        c.execute(
            "UPDATE runs SET state='failed', error_blob=?, updated_at=? WHERE run_id=?",
            (err, __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(), run_id),
        )
        c.commit()
    return True
