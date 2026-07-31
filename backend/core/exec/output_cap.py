"""Middle-snip output cap for tool stdout/stderr.

Applied at INPUT TIME (when a kernel result becomes a tool_result block),
not at history-pruning time — so the capped text is what enters the
conversation, what the prompt cache extends over, and what the Layer B
LLM-summary threshold measures against.

Why middle-snip instead of tail-truncation (CC's choice):
  Scientific output is usually informative at both ends. `print(df.head())`
  prints the head; the final line is often the summary or assertion; the
  middle is repetition (rows N..M, progress logs, iteration N completed).
  Keeping head + tail loses less signal than dropping the tail.

The marker tells the agent (a) it happened, (b) how much was cut, (c) how
to recover the missing region if needed. Small models that ignore prose
behavior rules still notice an in-line marker (validated 2026-05-29 — same
"guardrails IN tool results" pattern as the read_memory anti-fabrication
caveat).
"""
from __future__ import annotations

import re

from core.config import TOOL_OUTPUT_CAP_CHARS


_MARKER_BUDGET = 240   # max chars reserved for the snip marker line


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")


def collapse_progress(text: str) -> str:
    """Collapse terminal REDRAW output to what a reader would actually see.

    A progress bar paints one line thousands of times, each repaint separated by
    a carriage return and wrapped in ANSI cursor/colour codes. Character-count
    capping cannot help: the bytes are legitimate, just meaningless — a live run
    put ~4 KB of `\\r`-separated bar frames straight into a tool result, which is
    pure noise to a reader and pure cost to the context window.

    Two rules, both what a terminal does: drop ANSI escapes, and for a line
    containing carriage returns keep only its LAST frame (the final state the
    user would have seen). Real newlines are untouched, so ordinary multi-line
    output is unchanged."""
    if not text:
        return text
    if "\x1b" in text:
        text = _ANSI_RE.sub("", text)
    if "\r" not in text:
        return text
    out = []
    for line in text.split("\n"):
        if "\r" in line:
            # keep the last non-empty frame; a trailing \r means the final frame
            # is the one before it
            frames = [f for f in line.split("\r") if f != ""]
            line = frames[-1] if frames else ""
        out.append(line)
    return "\n".join(out)


def snip_middle(text: str, cap: int | None = None) -> str:
    """Cap `text` to ≤ `cap` chars by keeping the first and last halves and
    replacing the middle with a single human+agent-readable marker line.

    Terminal redraw noise is collapsed FIRST (`collapse_progress`), so the cap
    measures information rather than repaint frames — otherwise a progress bar
    can consume the whole budget and push the real output into the snipped
    middle.

    - `text` empty or short ⇒ returned unchanged.
    - `cap` defaults to `core.config.TOOL_OUTPUT_CAP_CHARS`.
    - `cap <= 0` disables capping (passthrough).
    """
    if not text:
        return text
    text = collapse_progress(text)
    if cap is None:
        cap = TOOL_OUTPUT_CAP_CHARS
    if cap <= 0 or len(text) <= cap:
        return text
    half = max(1, (cap - _MARKER_BUDGET) // 2)
    removed = len(text) - 2 * half
    if removed <= 0:
        return text
    head = text[:half]
    tail = text[-half:]
    marker = (
        f"\n\n[--- ABA snipped {removed:,} chars from middle: output exceeded "
        f"{cap:,}-char cap. Showing first {half:,} + last {half:,}. To inspect "
        f"the missing region, re-run with subset/.head()/.tail() or write to file. ---]\n\n"
    )
    return head + marker + tail
