"""Per-tool approval — session cache + decision recording.

Tools whose schema declares `approval_policy != 'never'` halt the Guide
loop the first time they're invoked in a session; the user approves
once (or always-for-session) and subsequent calls in the same session
run silently.

The bar for `approval_policy != 'never'` should be HIGH — only flag
tools where running unauthorized would cost real money or have
hard-to-reverse side effects. Plan-level approval covers most things;
per-tool approval is for the rare standalone heavy hitter.

Cache is per-(session_id, tool_name) — restricted to the current run
session. No persistence across sessions in v1 (per user preference).
"""
from __future__ import annotations
from typing import Optional


# Policies a tool can declare on its schema.
POLICY_NEVER   = 'never'      # No approval needed. Default.
POLICY_SESSION = 'session'    # Ask once per thread; cache the approval.
POLICY_ALWAYS  = 'always'     # Ask every time, no caching.

VALID_POLICIES = (POLICY_NEVER, POLICY_SESSION, POLICY_ALWAYS)

# Decisions the resume endpoint accepts.
ACTION_APPROVE         = 'approve'           # Run this once.
ACTION_APPROVE_SESSION = 'approve_session'   # Run + remember for this thread.
ACTION_REJECT          = 'reject'            # Don't run; return rejection to the model.

VALID_ACTIONS = (ACTION_APPROVE, ACTION_APPROVE_SESSION, ACTION_REJECT)


# In-memory cache: {(thread_id, tool_name) → True}. The "session" scope
# is the user's conversation (i.e. a thread) — not the per-turn
# session_id. Crossing into a different thread re-prompts.
_GRANTS: dict[tuple[str, str], bool] = {}


def needs_approval(policy: Optional[str], scope_id: str, tool_name: str) -> bool:
    """True iff the tool should halt for approval before executing.
    `scope_id` is the thread_id — approvals are per-conversation."""
    if not policy or policy == POLICY_NEVER:
        return False
    if policy == POLICY_SESSION and _GRANTS.get((scope_id, tool_name)):
        return False
    return True


def grant_for_session(scope_id: str, tool_name: str) -> None:
    _GRANTS[(scope_id, tool_name)] = True


def clear_session(scope_id: str) -> None:
    """Drop all approvals for a scope (e.g. on Cancel All for a thread)."""
    for k in list(_GRANTS.keys()):
        if k[0] == scope_id:
            _GRANTS.pop(k, None)


def _reset_for_testing() -> None:
    _GRANTS.clear()
