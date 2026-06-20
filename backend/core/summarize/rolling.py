"""Conversation history compaction — entry point.

History compaction redesign (misc/history_compaction_redesign.md):

  Phase 2: deterministic transcript pruning — replace verbose
  tool_result CONTENTS in older turns with one-line stubs; drop
  pure-text inter-step assistant prose older than K_TEXT_KEEP. Cheap,
  no LLM. (core.summarize.pruning)

  Phase 3: neutral-voice budget escape hatch — when pruning leaves the
  messages still above a configurable char budget, synthesize a
  per-THREAD SYSTEM SUMMARY (third-person, structured) that replaces
  the oldest portion. Cached per thread, regenerated incrementally.
  Most threads never hit this branch. (core.summarize.budget_summary)

The prior workspace-keyed agent-voice summarizer is gone (deleted; it
caused the voice-mimicry loop seen in thr_67f0b8ba on 2026-05-31).
"""
from __future__ import annotations
from typing import Optional

from core.config import FAKE_SESSION
from core.summarize.pruning import prune_transcript
from core.summarize.budget_summary import maybe_summarize


def effective_history(thread_id: Optional[str], messages: list[dict],
                      budget_chars: Optional[int] = None,
                      tail_keep:    Optional[int] = None) -> list[dict]:
    """Return the message list to send to the LLM:
       1. prune (Phase 2 — no LLM, fast)
       2. if still over budget AND thread_id is known, fold the
          oldest portion into a per-thread SYSTEM SUMMARY (Phase 3).

    `thread_id` keys the per-thread summary cache. Pass `None` to skip
    Phase 3 (pruning still runs).

    `budget_chars` overrides the global HISTORY_SUMMARY_THRESHOLD_CHARS
    for THIS call only — used by the lean primary spec to demand much
    earlier Tier-2 summarization (≤25k chars instead of 400k), so the
    synthesized neutral-voice summary fires inside the 40k vLLM
    window. None preserves today's behavior bit-for-bit."""
    if FAKE_SESSION:
        return messages
    pruned = prune_transcript(messages)
    return maybe_summarize(thread_id, pruned,
                           budget_chars=budget_chars,
                           tail_keep=tail_keep)
