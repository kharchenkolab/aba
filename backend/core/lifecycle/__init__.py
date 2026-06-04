"""Domain-neutral lifecycle engine.

Today (Phase 4.4): the status-transition validator. Wired into
core/graph/entities.update_entity + archive_entity so disallowed
transitions log a warning (consistent with Phase 4.5's create/edge
validators — warning-only first; flip to 422 later).

Future scope: a generic status_log mechanism that every type can
opt into via the YAML; on_status_change hooks dispatched here from
the transitions; per-type confidence/lifecycle sub-state machines
(claim's confidence ladder, thread's open/parked/concluded).
"""
from core.lifecycle.state_machine import validate_transition  # noqa: F401
