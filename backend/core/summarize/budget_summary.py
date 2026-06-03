"""Phase 3 — neutral-voice thread summary as the token-budget escape
hatch. Per the history-compaction redesign (misc/history_compaction_redesign.md
§4.2).

Triggered ONLY when transcript pruning (Phase 2) leaves the messages
above a configurable size budget. Most threads will never reach this.
When fired, synthesizes a single SYSTEM SUMMARY message (third-person,
structured) that REPLACES the oldest contiguous block of pruned
messages. The recent tail (default 20 msgs) stays verbatim.

Differences from the deprecated `rolling.py` LLM-summary path:
  - Per-THREAD (not workspace) — no cross-thread bleed.
  - Neutral third-person voice (prompt enforced) — no agent-voice
    mimicry loop ("I see from the summary that you've completed...").
  - Structured output (User asks / Agent did / Produced / Kernel state
    / Open work) — not free narration.
  - Wrapped in `[SYSTEM SUMMARY OF EARLIER ACTIVITY]…[/SYSTEM SUMMARY]`
    markers so the model has an explicit anchor: this is meta-context.
  - Sync Anthropic call (off-loop-safe — runs in the same thread as
    `effective_history`, which guide.py invokes via asyncio.to_thread).
"""
from __future__ import annotations
import json
import os
import sqlite3
from typing import Optional

from core.graph._schema import _conn, _utcnow


# Budget threshold (message-side chars). Default and env-var override live
# in core.config.HISTORY_SUMMARY_THRESHOLD_CHARS (consolidated 2026-06-03).
# Default 400K chars (~100K tokens) matches CC's autoCompactWindow default —
# fires rarely, lets the prompt cache extend across long sessions instead of
# flushing the message-tail prefix every few turns.
def _threshold() -> int:
    from core.config import HISTORY_SUMMARY_THRESHOLD_CHARS
    return HISTORY_SUMMARY_THRESHOLD_CHARS


TAIL_KEEP = 20         # how many recent messages to leave verbatim


def _message_chars(messages: list[dict]) -> int:
    """Crude size estimate — JSON-serialized total chars. Close enough
    for trigger decisions; we don't need token accuracy."""
    try:
        return len(json.dumps(messages, default=str))
    except (TypeError, ValueError):
        return sum(len(str(m)) for m in messages)


def _ensure_table() -> None:
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS thread_summaries (
                thread_id     TEXT PRIMARY KEY,
                covered_until INTEGER NOT NULL,
                summary       TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
        """)
        c.commit()


def _load(thread_id: str) -> tuple[int, str] | None:
    _ensure_table()
    with _conn() as c:
        try:
            r = c.execute(
                "SELECT covered_until, summary FROM thread_summaries WHERE thread_id=?",
                (thread_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
    return (r["covered_until"], r["summary"]) if r else None


def _save(thread_id: str, covered_until: int, summary: str) -> None:
    _ensure_table()
    with _conn() as c:
        c.execute(
            "INSERT INTO thread_summaries (thread_id, covered_until, summary, updated_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(thread_id) DO UPDATE SET "
            "covered_until=excluded.covered_until, summary=excluded.summary, updated_at=excluded.updated_at",
            (thread_id, covered_until, summary, _utcnow()),
        )
        c.commit()


def _render_msgs_for_synth(messages: list[dict]) -> str:
    """Turn a slice of messages into a compact transcript for the synth
    LLM. We keep role + text/tool_use snippets + tool_result stubs;
    skip raw image data."""
    lines: list[str] = []
    for m in messages:
        role = m.get("role", "?")
        c = m.get("content")
        if isinstance(c, str):
            lines.append(f"{role}: {c[:500]}")
            continue
        if not isinstance(c, list):
            continue
        for b in c:
            if not isinstance(b, dict):
                continue
            t = b.get("type")
            if t == "text":
                lines.append(f"{role}: {(b.get('text') or '')[:500]}")
            elif t == "tool_use":
                name = b.get("name", "?")
                inp = b.get("input") or {}
                # Just name + first ~80 chars of inputs as a hint
                hint = ""
                if isinstance(inp, dict):
                    for k, v in inp.items():
                        sv = str(v)[:80]
                        hint = f"{k}={sv!r}"
                        break
                lines.append(f"{role} [ran {name}]({hint})")
            elif t == "tool_result":
                content = b.get("content")
                if isinstance(content, str):
                    snippet = content[:300]
                else:
                    snippet = str(content)[:300]
                lines.append(f"{role} [result] {snippet}")
            # skip images
    return "\n".join(lines)[:50000]   # hard cap


def _synthesize(thread_id: str, old_messages: list[dict],
                prior_summary: Optional[str]) -> str:
    """Call the synth LLM. Returns the SYSTEM SUMMARY block text. Empty
    string on any failure — caller falls back gracefully (returns the
    pruned-but-unsummarized list, which is bigger but correct)."""
    try:
        from content.bio.lifecycle.promote import _sync_anthropic_client, _load_annotation_prompt
        from core.config import MODEL
        client = _sync_anthropic_client()
        system = _load_annotation_prompt("thread_summary")
        if not system:
            return ""

        transcript = _render_msgs_for_synth(old_messages)
        user_text = (
            f"Thread id: {thread_id}\n"
            f"Messages to fold into the summary: {len(old_messages)}\n\n"
        )
        if prior_summary:
            user_text += (
                "Previous summary (incremental — incorporate as needed):\n"
                f"{prior_summary}\n\n"
            )
        user_text += f"Transcript to summarize:\n{transcript}"

        r = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        out = " ".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
        # Strip leading/trailing markdown code fences if the LLM wrapped
        # its output (we asked for no fences; some models still do it).
        if out.startswith("```"):
            out = out.split("\n", 1)[1] if "\n" in out else out
        if out.endswith("```"):
            out = out.rsplit("\n", 1)[0]
        out = out.strip()
        # If the LLM didn't honor the wrapping tags, wrap it ourselves. We use
        # <summary>…</summary> (CC's convention) — XML-style tags from the
        # same family as <system-reminder>, which the model is heavily trained
        # to recognize as meta-context rather than user prose.
        if "<summary>" not in out:
            out = ("<summary>\n"
                   f"Scope: {thread_id}\n"
                   f"Covers: {len(old_messages)} messages\n\n"
                   + out
                   + "\n</summary>")
        return out
    except Exception:  # noqa: BLE001 — summary is best-effort
        return ""


def maybe_summarize(thread_id: Optional[str], messages: list[dict]) -> list[dict]:
    """If `messages` exceeds the size budget, replace the OLDEST
    contiguous block with a single SYSTEM SUMMARY message. Otherwise
    return `messages` unchanged.

    Tail (most recent TAIL_KEEP messages) is always preserved verbatim.
    Per-thread summary is cached + incrementally regenerated.
    """
    if not thread_id:
        # No thread context — skip summarization (no place to cache).
        return messages

    if _message_chars(messages) <= _threshold():
        return messages

    if len(messages) <= TAIL_KEEP + 2:
        # Not enough room to collapse meaningfully — bail.
        return messages

    to_cover_n = len(messages) - TAIL_KEEP
    old_block = messages[:to_cover_n]
    tail = messages[to_cover_n:]

    existing = _load(thread_id)
    prior = existing[1] if existing else None

    summary_text = _synthesize(thread_id, old_block, prior_summary=prior)
    if not summary_text:
        return messages   # synth failed; keep full pruned list

    _save(thread_id, to_cover_n, summary_text)

    # Single user-role message carries the summary, prefixed with the same
    # handoff framing Claude Code uses on its own continuing-session injection
    # ("This session is being continued from a previous conversation…").
    # Pattern: the model has been trained on CC's transcripts where this
    # exact framing appears at the start of a continued session — using it
    # primes the model to read what follows as meta-context, not user request.
    handoff = (
        "This session is being continued from a previous conversation that "
        "ran out of context. The summary below covers the earlier portion of "
        "the conversation.\n\n"
    )
    summary_msg = {
        "role": "user",
        "content": [{"type": "text", "text": handoff + summary_text}],
    }
    return [summary_msg] + tail
