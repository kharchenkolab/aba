"""Plan lifecycle transitions (A4 + #160).

`metadata.plan_lifecycle` flows: draft → validated → executing →
completed | failed | aborted. Today's transitions covered:

  - validated: set at create (in guide.py present_plan handler)
  - executing: when the user resumes a plan-halted turn (Go)
  - completed: when the resumed turn finishes successfully (DONE)
  - failed:    when the resumed turn errors out (FAILED)

`aborted` is reserved for an explicit reject flow; not wired yet.
`draft` is unused at the moment (validate_plan runs before create).
"""
from __future__ import annotations
from typing import Optional

from core.graph.entities import get_entity, update_entity


VALID_STATES = ("draft", "validated", "executing", "completed", "failed", "aborted")


def set_plan_lifecycle(plan_entity_id: str, new_state: str) -> Optional[dict]:
    """Read-modify-write the plan entity's metadata.plan_lifecycle.
    Returns the updated entity, or None if it doesn't exist or the
    new_state isn't recognized. Safe to call repeatedly with the same
    state — idempotent (no-op when state hasn't changed)."""
    if new_state not in VALID_STATES:
        return None
    ent = get_entity(plan_entity_id)
    if ent is None or ent.get("type") != "plan":
        return None
    meta = dict(ent.get("metadata") or {})
    if meta.get("plan_lifecycle") == new_state:
        return ent
    meta["plan_lifecycle"] = new_state
    return update_entity(plan_entity_id, metadata=meta)
