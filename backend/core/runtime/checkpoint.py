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
    """At process startup and on project switch: mark any Turn left in
    GENERATING/EXECUTING_TOOLS/SUMMARIZING state as FAILED (reason:
    'process restarted'). Those turns have no recovery path — the new
    process can't continue them because the LLM stream / tool dispatch
    is in-memory. Returns the number of rows reaped.

    AWAITING_USER turns are left alone — the user's next message resumes
    them via the normal chat flow.

    Side effect: also runs repair_orphaned_tool_use_in_messages() so the
    *message log* is API-clean for the next request, without needing a
    request-time history scan. This is the replacement for the old
    `_repair_tool_pairs` in-flight repair (A1).
    """
    import json, datetime
    stale_states = ("generating", "executing_tools", "summarizing")
    reason = json.dumps({"type": "ProcessRestart",
                         "message": "process restarted before the turn completed"})
    placeholders = ", ".join("?" for _ in stale_states)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            f"UPDATE runs SET state='failed', error_blob=?, updated_at=? "
            f"WHERE state IN ({placeholders})",
            (reason, now, *stale_states),
        )
        c.commit()
        n = cur.rowcount or 0
    # Independent of Turn rows, scan the message log for orphaned
    # tool_use blocks and write synthetic tool_results so the next
    # Anthropic call doesn't 400. Handles legacy DBs from before Turn
    # tracking existed.
    repair_orphaned_tool_use_in_messages()
    return n


def repair_orphaned_tool_use_in_messages() -> int:
    """Scan the project's message log for assistant tool_use blocks
    that aren't followed by a user tool_result with the matching id.
    For each gap, append a synthetic user message with the missing
    tool_results.

    Idempotent — running it twice is a no-op the second time.

    Returns the number of synthetic user messages inserted.
    """
    import json
    with _conn() as c:
        rows = c.execute(
            "SELECT id, entity_id, thread_id, role, content "
            "FROM messages ORDER BY id"
        ).fetchall()

    # Group by (entity_id, thread_id) — message history flows per-thread
    # within a project DB.
    by_thread: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["entity_id"], r["thread_id"])
        by_thread.setdefault(key, []).append({
            "id": r["id"], "role": r["role"], "content": r["content"],
        })

    inserted = 0
    for (entity_id, thread_id), msgs in by_thread.items():
        i = 0
        while i < len(msgs):
            m = msgs[i]
            if m["role"] == "assistant":
                try:
                    blocks = json.loads(m["content"])
                except (json.JSONDecodeError, TypeError):
                    blocks = []
                tool_ids = [
                    b.get("id") for b in blocks
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
                ]
                if tool_ids:
                    # Look at the next message: it should be a user message
                    # carrying tool_result blocks for each id.
                    nxt = msgs[i + 1] if i + 1 < len(msgs) else None
                    present: set = set()
                    if nxt and nxt["role"] == "user":
                        try:
                            nxt_blocks = json.loads(nxt["content"])
                        except (json.JSONDecodeError, TypeError):
                            nxt_blocks = []
                        present = {
                            b.get("tool_use_id") for b in nxt_blocks
                            if isinstance(b, dict) and b.get("type") == "tool_result"
                        }
                    missing = [tid for tid in tool_ids if tid not in present]
                    if missing:
                        synth = [{
                            "type": "tool_result",
                            "tool_use_id": tid,
                            "content": "[tool result unavailable — the run was interrupted; system synthesized this so the message log stays well-formed]",
                        } for tid in missing]
                        if nxt and nxt["role"] == "user":
                            # Prepend synthetic results to the existing user
                            # message so positional ordering is preserved.
                            with _conn() as c:
                                c.execute(
                                    "UPDATE messages SET content=? WHERE id=?",
                                    (json.dumps(synth + nxt_blocks), nxt["id"]),
                                )
                                c.commit()
                        else:
                            # No following user message — append one. Safe
                            # at startup / project-switch when nothing else
                            # writes concurrently.
                            with _conn() as c:
                                c.execute(
                                    "INSERT INTO messages "
                                    "(entity_id, focus_entity_id, thread_id, role, content, ts) "
                                    "VALUES (?, ?, ?, 'user', ?, ?)",
                                    (entity_id, None, thread_id, json.dumps(synth), _now()),
                                )
                                c.commit()
                        inserted += 1
            i += 1
    return inserted


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


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
