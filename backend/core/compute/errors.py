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


def is_error_payload(obj: Any) -> bool:
    """weft methods return either a result dict or an error payload — this is
    the discriminator the adapter applies to every return value."""
    return isinstance(obj, dict) and "error" in obj and "stage" in obj
