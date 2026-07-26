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


# ── env-resolution policy (ONE definition, two consumers) ───────────────────
# A step that asked for the project's environment and could not GET it must
# fail loudly. It must never be relocated to whatever interpreter happens to
# sit on the node's PATH: that silently swaps the whole scientific stack for an
# arbitrary one (live incident 2026-07-26 — an un-snapshottable session sent
# every remote python step to a node's system python 3.8 with a broken
# user-site package, for hours, reported as success). The node interpreter is
# reachable ONLY through the explicit `env='system'` lever.
#
# Both consumers — core/jobs/weft_submitter._detached_env (the one-shot/detached
# choke point) and content/bio/tools/run_exec._run_remote_kernel (the
# interactive lane) — ask this ONE question, so the policy cannot drift.
_ENV_FAILURE_CODES = frozenset({"no_base_pack"})
_ENV_FAILURE_PREFIXES = ("env.", "session.")


def is_env_resolution_failure(exc: BaseException) -> bool:
    """Did this failure mean "the declared environment could not be resolved"
    (as opposed to a transport hiccup, a capacity limit, or a missing kernel)?

    True for the substrate's env/session families (`env.solve_conflict`,
    `env.solve_failed`, `env.platform_mismatch`, `session.cold_base`, …) and
    aba's own `no_base_pack`. Non-ComputeError exceptions count as env failures
    when they arise on the env-resolution path — the caller decides scope by
    where it applies this; an unexpected exception there is exactly the shape
    that used to degrade silently."""
    code = getattr(exc, "code", None)
    if not isinstance(code, str):
        return True          # untyped failure on the env path: do NOT degrade
    return code in _ENV_FAILURE_CODES or code.startswith(_ENV_FAILURE_PREFIXES)


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
    raw = getattr(exc, "hints", None)
    # A malformed payload (non-dict hints) must never make the RENDERER raise —
    # describe() runs inside except handlers, where a raise escapes the
    # structured-error contract entirely.
    hints = dict(raw) if isinstance(raw, dict) else {}
    if not hints:
        return base
    parts: list[str] = []
    # The priority set is weft's own first-read diagnosis per failure class:
    # out/err tails (installs), log_tail (realize failures — the build log),
    # solver_message (solve conflicts — names the unsatisfiable pins), the
    # verifier's MISSING line, and WHOSE rc failed (install vs verify are
    # discriminated; a bare rc:0 in a failure means the OTHER stage died).
    for key in ("out_tail", "err_tail", "log_tail", "solver_message",
                "missing", "rc", "install_rc", "verify_rc", "script_tail"):
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
    if not parts:
        return base
    out = base + " — " + " | ".join(parts)
    # Bounded in TOTAL, not only per key — a payload with many keys is as able
    # to push the rest of a tool result out of view as one long tail.
    cap = 4 * limit
    return out if len(out) <= cap else out[:cap] + " …[truncated]"


def is_error_payload(obj: Any) -> bool:
    """weft methods return either a result dict or an error payload — this is
    the discriminator the adapter applies to every return value."""
    return isinstance(obj, dict) and "error" in obj and "stage" in obj
