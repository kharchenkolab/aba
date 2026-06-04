"""Turn-intent + thread-title helpers (WU-5 extraction).

Two small heuristics extracted from guide.py:

  - `effective_intent` — what user message to rank the in-prompt recipe
    slice against. On a resume/confirmation ("Yes, go ahead") the
    literal current text carries no task signal; we walk back through
    history to find the last substantive message so the slice stays
    relevant.
  - `derive_thread_title` — first-pass thread title from the opening
    message. No LLM (Phase 1 heuristic); LLM-quality naming was
    deferred to a later phase.

Pure string functions, no I/O.
"""
from __future__ import annotations


# Confirmation phrases that, when used as the entire user message, are
# resume/Go signals — not new task statements. Used by effective_intent
# to recognize when to look further back for the real task signal.
_CONFIRM_WORDS = {
    "yes", "y", "go", "ok", "okay", "sure", "proceed", "continue", "do it", "go ahead",
    "yes go ahead", "yes please", "yep", "please do", "run it", "sounds good", "go for it",
    "yes, go ahead", "do that", "please proceed",
}


def effective_intent(user_text: str, history: list) -> str:
    """The intent used to rank the in-prompt recipe slice. On a resume/confirmation
    ('Yes, go ahead') the literal user_text carries no task signal — fall back to
    the last substantive user message so the slice still reflects the real task
    (otherwise the resume turn gets an irrelevant slice). Normal turns return
    user_text unchanged."""
    t = (user_text or "").strip()
    if len(t) > 40 or t.lower().rstrip(".!").strip() not in _CONFIRM_WORDS:
        return user_text
    for m in reversed(history or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        txt = c if isinstance(c, str) else " ".join(
            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
        ) if isinstance(c, list) else ""
        txt = (txt or "").strip()
        if len(txt) > 40 and txt.lower().rstrip(".!").strip() not in _CONFIRM_WORDS:
            return txt
    return user_text


def derive_thread_title(text: str) -> str:
    """Heuristic thread title from the opening message (no LLM — Phase 1).
    LLM-quality naming is deferred to Phase 4."""
    t = " ".join((text or "").strip().split())
    for sep in (". ", "? ", "! ", "\n"):
        if sep in t:
            t = t.split(sep)[0]
            break
    t = t[:48].rstrip()
    return (t[:1].upper() + t[1:]) if t else "Investigation"
