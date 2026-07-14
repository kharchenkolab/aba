"""Turn persistence — upsert a Turn into the runs table on every transition.

Cheap (~10ms with SQLite WAL); call on every state change. Loading is a
single row read; load_turn() yields a fully-rehydrated Turn or None.
"""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn
from core.runtime.turn import Turn, TurnState


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
    """For /api/turns (diagnostic): the recent run roster. Includes
    parent_run_id (B4) extracted from the pending blob so the UI can
    indent sub-agent runs under their parent without a second round-trip."""
    import json
    with _conn() as c:
        rows = c.execute(
            "SELECT run_id, session_id, turn_index, agent_spec_name, state, "
            "       focus_entity_id, thread_id, pending_blob, started_at, updated_at "
            "FROM runs ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            pend = json.loads(d.pop("pending_blob") or "{}")
        except (json.JSONDecodeError, TypeError):
            pend = {}
        d["parent_run_id"] = pend.get("parent_run_id")
        out.append(d)
    return out


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
    # #14 — a turn whose background task is still running in THIS process is
    # NOT stale; it's alive. Never fail it. (Staleness means "owning process is
    # gone" — true for every in-flight turn at startup, false for a live one.)
    # Without this, a project switch that reaches reap while a turn is mid-tool
    # would mark it FAILED and synthesize a bogus 'interrupted' result.
    try:
        from core.runtime import turn_sink as _ts
        live = _ts.live_run_ids()
    except Exception:  # noqa: BLE001
        live = set()
    placeholders = ", ".join("?" for _ in stale_states)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with _conn() as c:
        if live:
            live_ph = ", ".join("?" for _ in live)
            cur = c.execute(
                f"UPDATE runs SET state='failed', error_blob=?, updated_at=? "
                f"WHERE state IN ({placeholders}) AND run_id NOT IN ({live_ph})",
                (reason, now, *stale_states, *live),
            )
        else:
            cur = c.execute(
                f"UPDATE runs SET state='failed', error_blob=?, updated_at=? "
                f"WHERE state IN ({placeholders})",
                (reason, now, *stale_states),
            )
        c.commit()
        n = cur.rowcount or 0
    # Sweep up any duplicate orphan-fill rows that accumulated under
    # the prior (buggy) reaper which appended a fill at the end of the
    # messages table for every middle orphan on every run. Idempotent
    # on a clean DB.
    purge_orphan_fill_messages()
    # Independent of Turn rows, scan the message log for trailing
    # orphans and append a synthetic user message so the next Anthropic
    # call doesn't 400. Middle orphans are skipped here — the
    # request-time shim handles them in-memory at the correct position.
    repair_orphaned_tool_use_in_messages()
    return n


ORPHAN_FILL_MARKER = "[tool result unavailable — the run was interrupted"


def _synthetic_tool_result(tool_use_id: str) -> dict:
    """The shape of a fill block. JSON-content so the frontend can
    structurally detect+hide it (legacy plain-string fills are still
    matched via ORPHAN_FILL_MARKER for back-compat)."""
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": json.dumps({
            "status": "interrupted",
            "note": "The previous tool call did not complete (run was interrupted).",
        }),
    }


def _is_orphan_fill_block(b) -> bool:
    """Recognize fill blocks in any historical format: new JSON shape
    (status=='interrupted') or legacy string (starts with the marker)."""
    if not isinstance(b, dict) or b.get("type") != "tool_result":
        return False
    c = b.get("content")
    if isinstance(c, str):
        if c.startswith(ORPHAN_FILL_MARKER):
            return True
        # New format: JSON-encoded
        try:
            j = json.loads(c)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(j, dict) and j.get("status") == "interrupted"
    return False


def _threads_with_pending_approval() -> set[str | None]:
    """Thread ids whose latest Turn row is paused (either AWAITING_USER on
    approval OR AWAITING_TOOL_RESULT on a deferred tool). Used by the
    orphan-fill reaper to skip patching held tool_uses — the
    resume/webhook endpoint writes the real result on user action /
    completion."""
    out: set[str | None] = set()
    with _conn() as c:
        rows = c.execute(
            "SELECT thread_id, state, pending_blob FROM runs "
            "WHERE state IN ('awaiting_user', 'awaiting_tool_result') "
            "  AND pending_blob IS NOT NULL"
        ).fetchall()
    for r in rows:
        if r["state"] == "awaiting_tool_result":
            out.add(r["thread_id"])
            continue
        try:
            pend = json.loads(r["pending_blob"])
        except (json.JSONDecodeError, TypeError):
            continue
        if pend.get("pending_user_signal") == "approval":
            out.add(r["thread_id"])
    return out


def repair_orphaned_tool_use_in_messages() -> int:
    """Scan the project's message log for *trailing* orphans — assistant
    messages with tool_use blocks where no user message follows at all
    in the thread — and append a synthetic user message with fill blocks
    for each missing id. The synthesized content is JSON-encoded so the
    frontend can hide it.

    **Middle orphans** (assistant_with_tool_use → assistant_without_fill
    inside the thread) are intentionally NOT patched here. Appending a
    fill at the end of the table doesn't actually satisfy the Anthropic
    API contract (which parses messages in order), so the appended row
    is useless from the API's perspective and just clutters the UI.
    The request-time shim `_ensure_tool_pair_completeness` in guide.py
    handles middle orphans in memory at the correct position before
    sending to the model.

    Idempotent on trailing-orphan case: once the fill row exists, the
    next pass sees no missing ids.

    Returns the number of synthetic user messages appended.
    """
    import json as _json
    with _conn() as c:
        rows = c.execute(
            "SELECT id, entity_id, thread_id, role, content "
            "FROM messages ORDER BY id"
        ).fetchall()

    by_thread: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["entity_id"], r["thread_id"])
        by_thread.setdefault(key, []).append({
            "id": r["id"], "role": r["role"], "content": r["content"],
        })

    # P1 #3 — turns that are legitimately paused on user approval also
    # have an unresolved trailing assistant tool_use (the held tool). We
    # must NOT patch those — the resume endpoint will write the real
    # tool_result when the user decides. Skip threads whose latest Turn
    # row is AWAITING_USER with pending_user_signal='approval'.
    held_thread_ids = _threads_with_pending_approval()
    # #14 (the deferred "Phase 2") — also skip threads with a turn whose task
    # is RUNNING right now in this process. Its trailing assistant tool_use is
    # the tool still in flight, NOT an orphan; synthesizing an 'interrupted'
    # fill here is the cross-project corruption (the real result lands moments
    # later, leaving two results for one tool_use).
    try:
        from core.runtime import turn_sink as _ts
        held_thread_ids = held_thread_ids | _ts.live_thread_ids()
    except Exception:  # noqa: BLE001
        pass

    inserted = 0
    for (entity_id, thread_id), msgs in by_thread.items():
        if thread_id in held_thread_ids:
            continue
        # Only repair if the LAST message in the thread is an assistant
        # with unresolved tool_use blocks. Middle orphans are skipped —
        # the request-time shim handles them at the correct position.
        if not msgs:
            continue
        last = msgs[-1]
        if last["role"] != "assistant":
            continue
        try:
            blocks = _json.loads(last["content"])
        except (_json.JSONDecodeError, TypeError):
            continue
        tool_ids = [
            b.get("id") for b in blocks
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
        ]
        if not tool_ids:
            continue
        synth = [_synthetic_tool_result(tid) for tid in tool_ids]
        with _conn() as c:
            c.execute(
                "INSERT INTO messages "
                "(entity_id, focus_entity_id, thread_id, role, content, ts) "
                "VALUES (?, ?, ?, 'user', ?, ?)",
                (entity_id, None, thread_id, _json.dumps(synth), _now()),
            )
            c.commit()
        inserted += 1
    return inserted


def purge_orphan_fill_messages() -> int:
    """Cleanup: remove orphan-fill clutter from the message log.

    Two passes:
      1. Pure-fill user messages → delete the row entirely.
      2. Mixed user messages (fill + other blocks) → rewrite content
         with the fills stripped. The request-time shim regenerates the
         in-memory fill before any API call, so dropping the persisted
         fill is safe (the model never sees an unresolved tool_use).

    Returns the count of rows touched (deleted + rewritten).
    """
    with _conn() as c:
        rows = c.execute(
            "SELECT id, content FROM messages WHERE role='user'"
        ).fetchall()

    delete_ids: list[int] = []
    rewrites: list[tuple[str, int]] = []
    for r in rows:
        try:
            blocks = json.loads(r["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(blocks, list) or not blocks:
            continue
        has_fill = any(_is_orphan_fill_block(b) for b in blocks)
        if not has_fill:
            continue
        kept = [b for b in blocks if not _is_orphan_fill_block(b)]
        if not kept:
            delete_ids.append(r["id"])
        elif len(kept) != len(blocks):
            rewrites.append((json.dumps(kept), r["id"]))

    if not delete_ids and not rewrites:
        return 0
    with _conn() as c:
        if delete_ids:
            c.executemany("DELETE FROM messages WHERE id=?", [(i,) for i in delete_ids])
        if rewrites:
            c.executemany("UPDATE messages SET content=? WHERE id=?", rewrites)
        c.commit()
    return len(delete_ids) + len(rewrites)


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def cancel_turn(run_id: str, *, reason: str = "user cancelled") -> bool:
    """Mark a Turn FAILED — used by POST /api/turns/{id}/cancel and as
    the explicit cleanup primitive when the UI knows an in-flight turn
    isn't coming back.

    Also repairs any orphan tool_use blocks in the message log so a
    reloaded chat doesn't render a permanent spinner for a tool whose
    result will never arrive. Without this, the only path to clear the
    spinner was a backend restart (which runs the same repair via
    `reap_stale_turns`)."""
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
    # Append synthetic [cancelled] tool_results for any tool_use blocks
    # that never got a partner — clears the frontend spinner.
    try:
        repair_orphaned_tool_use_in_messages()
    except Exception:  # noqa: BLE001
        pass
    return True


# ── deferred background-job resolution ────────────────────────────────────────
# A background tool (run_python/run_r/run_nextflow with background=true) parks its
# turn in AWAITING_TOOL_RESULT with pending_deferred={tool_use_id, deferred_id=job_id}
# and writes the assistant tool_use to history WITHOUT a tool_result. When the job
# reaches a terminal state (done/failed/cancelled/dropped), settle_deferred_job resolves
# that held tool_use — the single point that does so for EVERY background-job kind and
# EVERY terminal transition, so the chat tool line resolves (no forever-spinner) and the
# message history is well-formed. Driven entirely by the durable runs/messages tables, so
# it also works after a restart (reap_stale_turns leaves AWAITING_TOOL_RESULT intact).

def _find_awaiting_turn_for_job(job_id: str) -> Optional[str]:
    """run_id of the AWAITING_TOOL_RESULT turn whose deferred_id == job_id, else None."""
    with _conn() as c:
        rows = c.execute(
            "SELECT run_id, pending_blob FROM runs "
            "WHERE state='awaiting_tool_result' AND pending_blob IS NOT NULL"
        ).fetchall()
    for r in rows:
        try:
            pend = json.loads(r["pending_blob"])
        except (json.JSONDecodeError, TypeError):
            continue
        if (pend.get("pending_deferred") or {}).get("deferred_id") == job_id:
            return r["run_id"]
    return None


def deferred_settle_content(job: dict) -> dict:
    """The structured tool_result content that resolves a deferred tool_use, keyed on the
    job's terminal status. Kept brief in P1 — the continuation still carries the rich
    'continue the plan' message; this just makes the tool_use a well-formed pair."""
    status = job.get("status") or "done"
    jid = job.get("id") or "?"
    title = job.get("title") or "background job"
    # status values chosen to match the frontend tool-line icon logic:
    #   'ok' → ✓ ; 'cancelled' → ✓ (non-error, a user action) ; 'error' → ✗.
    if status == "cancelled":
        return {"status": "cancelled",
                "note": f"The background job `{jid}` ({title}) was cancelled before it finished."}
    if status == "done":
        return {"status": "ok", "deferred_id": jid,
                "note": f"The background job `{jid}` ({title}) finished; outputs are registered to this Run."}
    if status == "failed":
        err = (job.get("error") or "").strip().splitlines()
        return {"status": "error", "note": f"The background job `{jid}` ({title}) failed.",
                "error": (err[0][:300] if err else "")}
    # dropped / anything else — did not complete (defensive)
    return {"status": "error",
            "note": f"The background job `{jid}` ({title}) did not complete (interrupted)."}


def settle_deferred_job(job: dict) -> bool:
    """Resolve the parked deferred turn for a TERMINATED background job: write a terminal
    tool_result for its held tool_use and transition the turn out of AWAITING_TOOL_RESULT.
    Idempotent — no-op if there is no still-awaiting parked turn for this job (already
    settled, reaped, or the job never deferred). Returns True iff it settled one."""
    job_id = job.get("id")
    if not job_id:
        return False
    run_id = _find_awaiting_turn_for_job(job_id)
    if not run_id:
        return False
    t = load_turn(run_id)
    if t is None or t.state != TurnState.AWAITING_TOOL_RESULT or not t.pending_deferred:
        return False
    tool_use_id = t.pending_deferred.get("tool_use_id")
    if not tool_use_id:
        return False
    from core.graph.messages import append_message
    try:
        append_message(
            "user",
            [{"type": "tool_result", "tool_use_id": tool_use_id,
              "content": json.dumps(deferred_settle_content(job))}],
            entity_id=t.entity_id or "workspace",
            focus_entity_id=t.focus_entity_id,
            thread_id=t.thread_id,
        )
    except Exception:  # noqa: BLE001 — never let a message-write break job finalization
        return False
    # Transition out of AWAITING_TOOL_RESULT so the turn is no longer 'held' (the reaper /
    # request-time shim leave awaiting turns alone) and pending_deferred is cleared.
    t.pending_deferred = None
    t.transition(TurnState.FAILED if job.get("status") in ("failed", "cancelled") else TurnState.DONE)
    checkpoint(t)
    return True


# --- waist lifecycle subscription -------------------------------------------------
# Reap stale turns on a project's FIRST open in this process (A1/#14). The waist
# (core/projects) fires `on_project_first_open` and must not import upward
# (plane dependency lint, W0.2) — the Reasoning plane subscribes here instead.
# Registered at import: the app imports guide (→ this module) before any
# project is opened; embedders that never import the runtime simply have no
# turns to reap.
from core.hooks.dispatcher import register as _register_hook  # noqa: E402


def _reap_on_first_open(_ctx: dict) -> None:
    reap_stale_turns()


_register_hook("on_project_first_open", _reap_on_first_open)
