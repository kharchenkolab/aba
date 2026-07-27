"""A transport blip must not cost the user their kernel session.

Live (2026-07-27), three threads driving one ssh site at once: the contended
transport returned `site.unreachable` ("ssh to <site> timed out after 30s"), all
three sessions were declared dead, and every lane lost its in-memory state —
variables, loaded libraries, everything. The site was fine seconds later.

Two separate mistakes shared one line:

  * the failure happened at block SUBMIT (`wait=False`), so nothing had run yet
    and re-sending could not double-execute — but there was no retry;
  * ANY ComputeError marked the session dead, including one that never reached
    the node. A transport error says nothing about the kernel's health, and
    discarding a live session on that evidence is the expensive direction: one
    wasted round trip if the site really is gone, versus the user's whole
    workspace if it is not.

Retry is scoped to submit ON PURPOSE. A timeout MID-execution is the opposite
case — the block may well be running — so that path still surfaces rather than
retries. The ceiling tests below pin that boundary.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.compute.errors import ComputeError            # noqa: E402
from core.exec.kernels import weft as W                 # noqa: E402


# ── the transport/kernel distinction ─────────────────────────────────────────

def test_site_errors_are_transport():
    assert W._is_transport_error(ComputeError("site.unreachable",
                                              "ssh to X timed out after 30s"))


def test_retryable_flag_counts_as_transport():
    """weft marks what it considers safe to re-attempt; honour that even for a
    code we do not have in the prefix list."""
    e = ComputeError("task.capacity", "busy", retryable=True)
    assert W._is_transport_error(e)


def test_kernel_side_errors_are_NOT_transport():
    """CEILING: a genuinely dead kernel must still be reaped, or a broken session
    is retried forever and the pool never restarts it."""
    for code in ("kernel.died", "kernel.unknown", "env.unknown", "task.invalid"):
        assert not W._is_transport_error(ComputeError(code, "x")), code


def test_a_bare_exception_is_not_transport():
    """WIDE — the degenerate shape: an untyped failure carries no code and no
    retryable flag. Guessing "transport" there would keep dead sessions alive."""
    assert not W._is_transport_error(RuntimeError("boom"))
    assert not W._is_transport_error(ComputeError("", ""))


# ── submit retries exactly once, and only for transport ──────────────────────

class _Sess:
    """The two attributes _submit_block touches, plus a scripted _call."""

    def __init__(self, errors):
        self.kernel_id = "krn_x"
        self.site = "siteA"
        self.calls: list = []
        self._errors = list(errors)

    def _call(self, name, *a, **kw):
        self.calls.append(name)
        if self._errors:
            err = self._errors.pop(0)
            if err is not None:
                raise err
        return {"block": len(self.calls)}


def _submit(errors):
    s = _Sess(errors)
    return s, W.WeftKernelSession._submit_block(s, "print(1)")


def test_a_transport_failure_at_submit_is_retried_once(monkeypatch):
    monkeypatch.setattr(W.time, "sleep", lambda *_: None)
    s, out = _submit([ComputeError("site.unreachable", "ssh timed out")])
    assert len(s.calls) == 2, "submit was not retried after a transport failure"
    assert out["block"] == 2


def test_the_retry_is_ONCE_not_a_loop(monkeypatch):
    """A site that is genuinely down must surface, not spin. Bounded retries are
    the difference between resilience and a hang."""
    monkeypatch.setattr(W.time, "sleep", lambda *_: None)
    with pytest.raises(ComputeError):
        _submit([ComputeError("site.unreachable", "down"),
                 ComputeError("site.unreachable", "still down")])


def test_a_kernel_side_failure_at_submit_is_NOT_retried(monkeypatch):
    """CEILING on the forbidden ACTION: re-sending to a dead kernel is pointless,
    and blurring the two classes is how a retry becomes a double-execute
    somewhere else."""
    monkeypatch.setattr(W.time, "sleep", lambda *_: None)
    s = _Sess([ComputeError("kernel.died", "gone")])
    with pytest.raises(ComputeError):
        W.WeftKernelSession._submit_block(s, "print(1)")
    assert len(s.calls) == 1, "a kernel-side failure was retried"


def test_a_clean_submit_makes_exactly_one_call():
    """CEILING: the ordinary path must not pay an extra round trip."""
    s, out = _submit([])
    assert len(s.calls) == 1 and out["block"] == 1


# ── the session survives a transport failure ─────────────────────────────────

def test_the_source_keeps_the_session_alive_on_transport_only():
    """The consequence half of the fix, at the one line that shipped the bug:
    `alive` must be derived from WHICH class of error occurred, never set False
    unconditionally.

    Asserted on the source because constructing a real WeftKernelSession needs a
    live substrate — and a fake permissive enough to build cheaply would be
    exactly the kind that blesses the bug."""
    src = (ROOT / "backend/core/exec/kernels/weft.py").read_text()
    i = src.index("sub = self._submit_block(code)")
    body = src[i:i + 1200]
    assert "self.alive = not _is_transport_error(e)" in body, \
        "the submit failure path no longer distinguishes transport from kernel death"
    assert "self.alive = False\n" not in body, \
        "an unconditional kill came back into the submit path"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
