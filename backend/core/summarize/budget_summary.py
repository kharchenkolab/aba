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

from core import config
from core.graph._schema import _conn, _utcnow


# Budget threshold (message-side chars). Default and env-var override live
# in core.config.HISTORY_SUMMARY_THRESHOLD_CHARS (consolidated 2026-06-03).
# Default 400K chars (~100K tokens) matches CC's autoCompactWindow default —
# fires rarely, lets the prompt cache extend across long sessions instead of
# flushing the message-tail prefix every few turns.
def _threshold(budget_chars: Optional[int] = None) -> int:
    """Caller-supplied `budget_chars` (e.g. from the active AgentSpec)
    overrides the global default. Used by the lean spec to demand
    earlier Tier-2 summarization in a small-context backend window."""
    if budget_chars is not None and budget_chars > 0:
        return int(budget_chars)
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


def _summary_model() -> str:
    """Pick the model used by Tier-2 history synthesis.

    Tier-2 is a Haiku-class job (structured rewriting of a bounded
    transcript, not creative reasoning). It's intentionally
    DECOUPLED from `ABA_MODEL` / `ABA_PRIMARY_MODEL` (which steer the
    chat agent) because (a) the primary chat model and the synth
    model have different optimal choices; (b) using the primary chat
    model means Tier-2 inherits its rate-limit budget, which 429'd
    under live load and silently dropped summarization (observed in
    prj_30d7535f 2026-06-19).

    Override knob: `ABA_SUMMARY_MODEL`. Forward-looking: once the
    local-LLM runtime lands, point this at the local endpoint —
    cheaper than Anthropic Haiku and the summarization workload
    doesn't need frontier quality.
    """
    return config.settings.summary_model.get().strip()


# Observable failure modes for Tier-2. Counters so the next "Tier-2
# isn't firing" mystery is one `print(_TIER2_DIAG)` away from the
# answer. Last-error string is the one the most recent _synthesize
# raised (or empty if it succeeded). Stays in-process; not persisted.
_TIER2_DIAG: dict = {
    "calls":            0,    # _synthesize entered
    "ok":               0,    # synth returned non-empty wrapped text
    "skipped_no_prompt": 0,   # `thread_summary` registration absent
    "raised":           0,    # any Exception path
    "reused":           0,    # stored summary served verbatim — NO LLM call
    "reused_on_fail":   0,    # synth failed; stored summary served (not the cliff)
    "saturated":        0,    # budget unsatisfiable at ANY boundary; served stored
                              # verbatim. A nonzero rate here is the live tell that
                              # something oversized (e.g. a vision block) is being
                              # retained verbatim in history — the upstream cap's
                              # problem, not this module's.
    "last_error":       "",
}


def tier2_diag() -> dict:
    """Snapshot of the failure-mode counters above. Tests + admin
    debug pages read this to confirm Tier-2 is doing what it claims."""
    return dict(_TIER2_DIAG)


def _synthesize(thread_id: str, old_messages: list[dict],
                prior_summary: Optional[str]) -> str:
    """Call the synth LLM. Returns the SYSTEM SUMMARY block text. Empty
    string on any failure — caller falls back gracefully (returns the
    pruned-but-unsummarized list, which is bigger but correct).

    Every entry/exit lane bumps a counter in `_TIER2_DIAG`. Tests
    assert on those counters instead of patching this function to a
    stub — the stub-test pattern hid three real bugs (no prompt
    registration, MODEL-as-Opus, 429 on shared rate budget) in the
    prj_30d7535f 2026-06-19 session. Once burned, twice shy."""
    _TIER2_DIAG["calls"] += 1
    try:
        from core.llm import sync_anthropic_client
        from core.prompts import get as get_prompt
        client = sync_anthropic_client()
        system = get_prompt("thread_summary") or ""
        if not system:
            _TIER2_DIAG["skipped_no_prompt"] += 1
            _TIER2_DIAG["last_error"] = (
                "thread_summary prompt is not registered — bio import "
                "side-effects must have failed before _synthesize ran")
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
            model=_summary_model(),
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
        _TIER2_DIAG["ok"] += 1
        _TIER2_DIAG["last_error"] = ""
        return out
    except Exception as e:                                       # noqa: BLE001
        _TIER2_DIAG["raised"] += 1
        _TIER2_DIAG["last_error"] = f"{type(e).__name__}: {e}"
        return ""


def maybe_summarize(thread_id: Optional[str], messages: list[dict],
                    budget_chars: Optional[int] = None,
                    tail_keep:    Optional[int] = None) -> list[dict]:
    """If `messages` exceeds the size budget, replace the OLDEST
    contiguous block with a single SYSTEM SUMMARY message. Otherwise
    return `messages` unchanged.

    Tail (most recent TAIL_KEEP messages) is always preserved verbatim.
    Per-thread summary is cached + incrementally regenerated.

    `budget_chars` overrides the global threshold for THIS call.
    `tail_keep` overrides the global TAIL_KEEP guard (default 20 was
    calibrated for the 400k-char budget; lean's 25k budget needs ~6).
    Both None ⇒ preserves today's behavior bit-for-bit.
    """
    if not thread_id:
        # No thread context — skip summarization (no place to cache).
        return messages

    if _message_chars(messages) <= _threshold(budget_chars):
        return messages

    eff_tail = tail_keep if tail_keep is not None and tail_keep > 0 else TAIL_KEEP
    if len(messages) <= eff_tail + 2:
        # Not enough room to collapse meaningfully — bail.
        return messages

    # The boundary derives from the STORE, not from the current length.
    # Prompt caching is prefix-matched: if each call re-picked the boundary
    # from `len(messages) - eff_tail`, the head would slide +2 every
    # generation and the summary (message 0) would be re-synthesized over a
    # different block each time — nothing before the tail would ever match,
    # so a long turn re-billed its entire retained history per generation
    # (measured live: 411k cache_write tokens in one turn) and paid an extra
    # synchronous LLM call per generation. Instead: REUSE the stored
    # (covered_until, summary) verbatim while the uncovered remainder still
    # fits the budget — output stays a prefix-extension of the previous
    # generation's — and ADVANCE the boundary (monotonically, re-synthesizing
    # with the prior as seed) only when the remainder alone re-exceeds it.
    existing = _load(thread_id)
    if existing:
        cov_n, prior = existing
        if 0 < cov_n <= len(messages) - 1:
            remainder = messages[cov_n:]
            if _message_chars(remainder) + len(prior) <= _threshold(budget_chars):
                _TIER2_DIAG["reused"] += 1
                return [_summary_message(prior), *remainder]
        else:
            cov_n, prior = 0, None      # store stale/misaligned → full re-derive
    else:
        cov_n, prior = 0, None

    # SATURATION rule: if even MAXIMUM coverage cannot fit — the tail alone
    # (plus the summary) exceeds the budget, which one oversized message inside
    # tail_keep guarantees — then re-synthesizing achieves nothing but a broken
    # prefix and a synchronous LLM roundtrip, every generation (the regime the
    # live incident was in: a ~MB vision block riding the tail for ~tail_keep/2
    # generations, 30/30 divergences measured). Serve the stored summary
    # verbatim instead: over-budget but byte-STABLE, so the request is a
    # prefix-extension and bills only its delta. Advance anyway once the
    # uncovered gap exceeds a quantum (bounds coverage staleness at one
    # sanctioned rewrite per epoch, same idiom as Tier-1's batches).
    desired = len(messages) - eff_tail
    if cov_n and prior:
        tail_chars = _message_chars(messages[desired:])
        if tail_chars + len(prior) > _threshold(budget_chars) \
                and (desired - cov_n) < max(16, 4 * eff_tail):
            _TIER2_DIAG["saturated"] += 1
            return [_summary_message(prior), *messages[cov_n:]]

    to_cover_n = max(desired, cov_n)                    # never move backwards
    old_block = messages[cov_n:to_cover_n] if cov_n else messages[:to_cover_n]
    tail = messages[to_cover_n:]

    summary_text = _synthesize(thread_id, old_block, prior_summary=prior)
    if not summary_text:
        # Degrade to the STORED summary when one exists: stale-but-stable beats
        # the bail-out cliff (dumping the full un-summarized history was the
        # single largest cache_write of the measured run).
        if prior and cov_n:
            _TIER2_DIAG["reused_on_fail"] += 1
            return [_summary_message(prior), *messages[cov_n:]]
        return messages   # no store yet; keep full pruned list

    _save(thread_id, to_cover_n, summary_text)

    return [_summary_message(summary_text)] + tail


def _summary_message(summary_text: str) -> dict:
    """Single user-role message carrying the summary, prefixed with the same
    handoff framing Claude Code uses on its own continuing-session injection
    ("This session is being continued from a previous conversation…") — the
    model reads it as meta-context, not user request. Byte-DETERMINISTIC for a
    given summary text: this message is message 0 of every request while the
    summary is live, so any variation here re-bills the whole history."""
    handoff = (
        "This session is being continued from a previous conversation that "
        "ran out of context. The summary below covers the earlier portion of "
        "the conversation.\n\n"
    )
    return {"role": "user",
            "content": [{"type": "text", "text": handoff + summary_text}]}
