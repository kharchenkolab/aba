"""Observability emit helper — the one-liner platform code calls to put a
structured event on the Console feed (`console` event, notify channel).

Instrumentation must never break the operation it observes: `emit` filters
None fields, validates through the wire builder, and swallows every failure
(a malformed emit degrades to a stderr line, never an exception in the
serving path). Producers stay honest because the wire builder rejects
unknown fields — a typo'd fact name surfaces in any test that exercises
the call site, while production keeps serving.

Categories (the Console's facet vocabulary — keep this closed set in sync
with the frontend's CATEGORIES in console.ts):
  run      job/kernel/execution lifecycle
  data     transfers, chunk streaming, retention, ingest
  env      environment solve/realize/publish
  compute  site lifecycle (bootstrap, register, probe)
  serve    viewers, services, tunnels
  system   entity/module/platform events
"""
from __future__ import annotations

from typing import Any

CATEGORIES = ("run", "data", "env", "compute", "serve", "system")


def emit(category: str, verb: str, **fields: Any) -> None:
    """Broadcast a `console` event. None-valued fields are dropped; failures
    are swallowed (warn-once semantics live in wire.check on the transport)."""
    try:
        from core.runtime import notifications, wire
        clean = {k: v for k, v in fields.items() if v is not None}
        notifications.broadcast(wire.console(category=category, verb=verb, **clean))
    except Exception as e:  # instrumentation never breaks the observed path
        print(f"[obs] dropped console event {verb!r}: {e}")
