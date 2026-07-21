"""Typed compute-substrate failure — weft's structured error surfaced to aba.

weft's API returns error payloads (never raises across its boundary):
``{"error": <code>, "stage": ..., "detail": ..., "retryable": ..., "hints": ...,
"meaning": ...}``. The adapter converts those into this exception so aba code
gets normal control flow; agent-facing tools catch it and surface the
structured cause (the doctrine: degradation transparent — never fatal, never
silent — the agent decides what to do with the hints).
"""
from __future__ import annotations

from typing import Any


class ComputeError(RuntimeError):
    def __init__(self, code: str, detail: str, *, stage: str = "aba",
                 hints: dict[str, Any] | None = None, retryable: bool = False,
                 meaning: str = ""):
        super().__init__(f"[{code}@{stage}] {detail}")
        self.code = code
        self.stage = stage
        self.detail = detail
        self.hints = hints or {}
        self.retryable = retryable
        self.meaning = meaning

    @classmethod
    def from_payload(cls, payload: dict) -> "ComputeError":
        return cls(
            str(payload.get("error") or "unknown"),
            str(payload.get("detail") or ""),
            stage=str(payload.get("stage") or "weft"),
            hints=payload.get("hints") or {},
            retryable=bool(payload.get("retryable")),
            meaning=str(payload.get("meaning") or ""),
        )

    def to_payload(self) -> dict:
        """The agent-facing shape (mirrors weft's error dict)."""
        return {"error": self.code, "stage": self.stage, "detail": self.detail,
                "retryable": self.retryable, "hints": self.hints,
                "meaning": self.meaning}


def describe(exc: BaseException, *, limit: int = 700) -> str:
    """Agent-facing rendering of a substrate failure INCLUDING its hints.

    `str(ComputeError)` is only `[code@stage] detail` — a summary. weft attaches
    the actual diagnosis in `hints` (for a failed R/py install: `rc`,
    `out_tail`, `err_tail`, `script_tail`), and every caller that formatted the
    failure as f"…{e}" silently dropped it. Live 2026-07-21 that cost four
    wasted turns: the agent was told "session installer failed" while the hints
    it never saw said

        Error: Failed to install 'unknown package' from GitHub:
          cannot open URL '…/contents/DESCRIPTION?ref=main'

    i.e. the package sits in a subdirectory — not, as the agent concluded, that
    the repository does not exist. Use this at any surface the agent reads.

    Ordered so the most diagnostic hint leads, and each value bounded — an
    unbounded tail would push the rest of the tool result out of view.
    """
    base = str(exc)
    hints = dict(getattr(exc, "hints", None) or {})
    if not hints:
        return base
    parts: list[str] = []
    for key in ("out_tail", "err_tail", "rc", "script_tail"):
        if key in hints:
            val = str(hints.pop(key)).strip()
            if val:
                parts.append(f"{key}: {val[:limit]}")
    for key in sorted(hints):                    # anything else weft sent
        val = str(hints[key]).strip()
        if val:
            parts.append(f"{key}: {val[:200]}")
    meaning = (getattr(exc, "meaning", "") or "").strip()
    if meaning:
        parts.append(f"meaning: {meaning}")
    return base + " — " + " | ".join(parts) if parts else base


def is_error_payload(obj: Any) -> bool:
    """weft methods return either a result dict or an error payload — this is
    the discriminator the adapter applies to every return value."""
    return isinstance(obj, dict) and "error" in obj and "stage" in obj
