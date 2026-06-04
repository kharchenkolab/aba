"""Status-transition validator — domain-neutral.

Phase 4.4 of misc/phase4_entity_types.md. Read by core/graph/entities's
write paths (update_entity, archive_entity) and any future caller that
wants to know whether a transition is allowed before committing it.

Today: warning-only. Returns a list of human-readable messages —
empty = OK or unknown type / unknown current state. Callers log via
their module logger and proceed; after a week of real-use warnings
the contract may flip to raise.

Unknown type yields no warnings (consistent with create/edge
validators in Phase 4.5). This is critical for legacy data and
synthetic test types.
"""
from __future__ import annotations

from typing import Optional


def validate_transition(
    *,
    entity_type: str,
    from_status: Optional[str],
    to_status: str,
) -> list[str]:
    """Return a list of warnings — one per problem with the proposed
    transition. Empty list = OK. Unknown type yields empty (skip)."""
    from core.entity_types import get_type
    t = get_type(entity_type)
    if t is None:
        return []  # unknown type — skip
    states = list(t.status_model.get("states") or [])
    msgs: list[str] = []
    if to_status not in states:
        msgs.append(
            f"{entity_type}: target status '{to_status}' is not in "
            f"declared states {states}"
        )
        return msgs   # nothing more meaningful to say
    if from_status is None:
        # No current-state context (creation path or archive without
        # pre-fetch). Without a `from_`, we can only validate that the
        # target is a declared state.
        return msgs
    transitions = t.status_model.get("transitions") or {}
    allowed_from = transitions.get(from_status) or []
    if to_status not in allowed_from and from_status != to_status:
        msgs.append(
            f"{entity_type}: transition {from_status!r} -> {to_status!r} "
            f"is not declared (allowed from {from_status!r}: {allowed_from})"
        )
    return msgs
