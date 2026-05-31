"""In-process tool-lifecycle hook registry.

Adopts the Claude Agent SDK's PreToolUse / PostToolUse / PostToolUseFailure
DECISION CONTRACT — deny with a model-facing reason, rewrite the input before
execution, add context / rewrite the output afterwards — but runs as plain
in-process calls from `_dispatch_tool`, NOT over a subprocess control protocol
(see misc decision: keep ABA's own loop, borrow the SDK's hook *shape*).

It turns the accreting guardrail if-pile in tools.py into an ordered,
matcher-scoped, individually-testable registry. Three events (the only ones
ABA needs):

  PreToolUse          fn(name, input_, ctx) -> Deny | Rewrite | None
  PostToolUse         fn(name, input_, result, ctx) -> None      (mutates result)
  PostToolUseFailure  fn(name, input_, result, ctx) -> None      (mutates result;
                      runs only when the result represents a failure)

Register with the decorators below, passing a tool-name matcher (regex string,
predicate callable, or None = all tools).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Deny:
    """A PreToolUse veto. Rendered to a typed control-state 'blocked' result that the
    model receives (NOT an error to work around). `reason` is MODEL-facing and must be
    actionable; `system_message` is the optional USER-facing warning (two audiences,
    two fields — the SDK's reason vs systemMessage split)."""
    reason_code: str
    block_type: str
    reason: str
    allowed: list = field(default_factory=list)
    forbidden: list = field(default_factory=list)
    system_message: Optional[str] = None


@dataclass
class Rewrite:
    """A PreToolUse input rewrite — modify the call before it executes (safe-defaulting,
    e.g. redirect a path, inject a confirm flag). The SDK's updatedInput."""
    updated_input: dict
    note: str = ""


@dataclass
class _Hook:
    match: Callable[[str], bool]
    fn: Callable
    label: str


_PRE: list[_Hook] = []
_POST: list[_Hook] = []
_FAIL: list[_Hook] = []


def _matcher(spec: Any) -> Callable[[str], bool]:
    if spec is None:
        return lambda _n: True
    if callable(spec):
        return spec
    rx = re.compile(f"^(?:{spec})$")
    return lambda n: bool(rx.match(n))


def pre_tool_use(match: Any = None, *, label: str = "") -> Callable:
    def deco(fn: Callable) -> Callable:
        _PRE.append(_Hook(_matcher(match), fn, label or fn.__name__))
        return fn
    return deco


def post_tool_use(match: Any = None, *, label: str = "") -> Callable:
    def deco(fn: Callable) -> Callable:
        _POST.append(_Hook(_matcher(match), fn, label or fn.__name__))
        return fn
    return deco


def post_tool_use_failure(match: Any = None, *, label: str = "") -> Callable:
    def deco(fn: Callable) -> Callable:
        _FAIL.append(_Hook(_matcher(match), fn, label or fn.__name__))
        return fn
    return deco


def deny_to_result(d: Deny) -> dict:
    """Render a Deny as the typed control-state 'blocked' dict the model receives.
    Shape is byte-identical to the pre-refactor tools._blocked() (behavior-preserving)."""
    out = {
        "status": "blocked", "executed": False, "block_type": d.block_type,
        "reason_code": d.reason_code, "message": d.reason,
        "allowed_next_actions": d.allowed, "forbidden_next_actions": d.forbidden or [],
        "note": "This code was NOT executed. This is a design-level block, not a code "
                "error to work around — re-running the same approach will be blocked again.",
    }
    if d.system_message:
        out["user_message"] = d.system_message   # user-facing; only present when set
    return out


def run_pre(name: str, input_: dict, ctx: Optional[dict]) -> tuple[Optional[Deny], dict]:
    """Run PreToolUse hooks in registration order. First Deny short-circuits (returned
    to the caller, which skips execution). Rewrites chain into `input_`. Returns
    (Deny | None, possibly-rewritten input_)."""
    for h in _PRE:
        if not h.match(name):
            continue
        d = h.fn(name, input_, ctx)
        if isinstance(d, Deny):
            return d, input_
        if isinstance(d, Rewrite):
            input_ = d.updated_input
    return None, input_


def _is_failure(result: Any) -> bool:
    """Does this tool result represent a failure? (drives PostToolUseFailure hooks)."""
    if not isinstance(result, dict):
        return False
    if result.get("status") in ("error", "failed") or result.get("is_error") or result.get("error"):
        return True
    rc = result.get("returncode")
    return isinstance(rc, int) and rc != 0


def run_post(name: str, input_: dict, result: Any, ctx: Optional[dict]) -> None:
    """Run PostToolUse hooks (always), then PostToolUseFailure hooks (only when the
    result is a failure). Hooks mutate `result` in place (add_context / rewrite_output)."""
    for h in _POST:
        if h.match(name):
            h.fn(name, input_, result, ctx)
    if _is_failure(result):
        for h in _FAIL:
            if h.match(name):
                h.fn(name, input_, result, ctx)


def registered() -> dict:
    """Introspection for tests / debugging: the labels registered per event."""
    return {"pre": [h.label for h in _PRE],
            "post": [h.label for h in _POST],
            "post_failure": [h.label for h in _FAIL]}
