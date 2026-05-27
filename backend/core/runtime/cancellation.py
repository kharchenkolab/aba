"""Per-turn cancellation primitive.

A `CancelToken` is the single object every long-running thing in a turn
hangs its cleanup off of. Anything that can be interrupted — a Python
subprocess, an MCP tool call, an LLM stream's underlying HTTP
connection, a background job — registers an *interrupter* (a no-arg
callable) at the start of its work and removes it on completion. When
the turn is cancelled, every registered interrupter fires.

The design is deliberately tool-agnostic. `run_python` registers a
`proc.terminate()` callback; an MCP tool registers
`session.send_notification('cancelled')`; a future GPU job registers
`job.kill()`. The Guide loop just sees `token.cancelled` flip and
breaks out — it doesn't need to know how each thing dies.

Per-turn instances live in a module-level dict keyed by run_id. Acquired
at the top of `stream_response`, released in its finally block. Cancel
endpoint looks up by run_id and fires.
"""
from __future__ import annotations
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


# How long to wait between SIGTERM and SIGKILL for processes whose
# interrupter wants to do graceful shutdown first. Tools that want
# different semantics can wrap their own grace period.
DEFAULT_GRACE_S = 2.0


@dataclass
class CancelToken:
    """One token per Turn. Tools register interrupters; cancel fires them.

    Methods are thread-safe — cancel can be called from any thread
    (typically the HTTP request thread for /api/turns/{id}/cancel),
    while interrupters register/unregister from the executor threads.
    """
    run_id: str
    cancelled: bool = False
    reason:    str  = ""
    cancelled_at: Optional[float] = None
    _interrupters: list[tuple[int, Callable[[], None]]] = field(default_factory=list)
    _next_id: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock)

    def register(self, interrupter: Callable[[], None]) -> Callable[[], None]:
        """Add an interrupter; returns a callable that removes it. If the
        token is ALREADY cancelled, the interrupter fires immediately
        (and the returned remove is a no-op) — this handles the race
        where the user clicks Stop just before the tool starts."""
        with self._lock:
            if self.cancelled:
                _safe_call(interrupter)
                return _noop
            self._next_id += 1
            iid = self._next_id
            self._interrupters.append((iid, interrupter))

        def remove() -> None:
            with self._lock:
                self._interrupters = [(i, fn) for (i, fn) in self._interrupters if i != iid]
        return remove

    def cancel(self, reason: str = "user cancelled") -> bool:
        """Mark cancelled + fire all interrupters. Idempotent: a second
        call returns False without re-firing. Interrupters are invoked
        OUTSIDE the lock, in reverse registration order (LIFO — the
        most-recently-started thing dies first)."""
        with self._lock:
            if self.cancelled:
                return False
            self.cancelled = True
            self.reason = reason
            self.cancelled_at = time.time()
            interrupters = list(self._interrupters)
            self._interrupters.clear()
        for _iid, fn in reversed(interrupters):
            _safe_call(fn)
        return True

    def raise_if_cancelled(self) -> None:
        """Convenience for loops that want to bail explicitly."""
        if self.cancelled:
            raise CancelledError(self.reason)


class CancelledError(Exception):
    """Raised by raise_if_cancelled() for callers that prefer exception
    flow over polling."""


# ---- Module-level per-run registry ----

_TOKENS: dict[str, CancelToken] = {}
_REG_LOCK = threading.Lock()


def acquire(run_id: str) -> CancelToken:
    """Get (or create + register) the token for a run_id. Called at the
    top of stream_response. Calling twice with the same id returns the
    same token (so a resumed turn shares cancellation state if needed)."""
    with _REG_LOCK:
        tok = _TOKENS.get(run_id)
        if tok is None:
            tok = CancelToken(run_id=run_id)
            _TOKENS[run_id] = tok
        return tok


def get(run_id: str) -> Optional[CancelToken]:
    """Look up an existing token without creating one. Used by the
    cancel endpoint — if there's no token, the run isn't in flight on
    this process (already finished, or never started here)."""
    with _REG_LOCK:
        return _TOKENS.get(run_id)


def release(run_id: str) -> None:
    """Remove the token. Called from stream_response's finally block.
    Idempotent. Note: releasing a CANCELLED token still removes it —
    once a turn is gone, there's nothing left to interrupt."""
    with _REG_LOCK:
        _TOKENS.pop(run_id, None)


def active_run_ids() -> list[str]:
    """For diagnostics — which turns currently have a live cancel token."""
    with _REG_LOCK:
        return list(_TOKENS.keys())


# ---- internals ----

def _safe_call(fn: Callable[[], None]) -> None:
    """Run an interrupter; swallow exceptions. We never want a buggy
    interrupter to prevent other interrupters from firing."""
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        # Best-effort log; never raise back to the caller of cancel().
        print(f"[cancel] interrupter failed: {type(e).__name__}: {e}")


def _noop() -> None:
    pass


def _reset_for_testing() -> None:
    with _REG_LOCK:
        _TOKENS.clear()
