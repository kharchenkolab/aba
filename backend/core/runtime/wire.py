"""The UI wire contract — single source of truth for every event ABA streams to clients.

Two channels (docs/arch/contact-surface.md):

* ``turn``   — the per-turn chat stream (``POST /api/chat`` / ``/resume``, reattach via
  ``GET /api/turns/{run_id}/stream``). Payloads are pushed through
  ``core.runtime.turn_sink``, which injects ``seq`` and does the SSE framing.
* ``notify`` — the global out-of-band stream (``GET /api/notifications``), via
  ``core.runtime.notifications.broadcast()``.

Producers construct events through this module — ``wire.delta(text=...)``,
``wire.entity_updated(entity_id=..., reason=...)`` — never as ad-hoc dict literals
(``tests/test_wire_contract.py`` guards the producer files). Builders are strict:
a missing required field or an unknown field raises immediately (a producer bug,
caught by any test that exercises the site). The transports additionally run
``check()`` on every outbound payload — warn-once in production, so a slipped
payload degrades to a log line, never a broken stream.

``frontend/src/wire.ts`` is GENERATED from this registry by
``scripts/gen_wire_types.py``; the sync test fails if it drifts. Resource
interiors (``Entity``, ``JobInfo``, ``ManifestSnapshot``) stay hand-maintained in
``frontend/src/types.ts`` and are referenced by name — events are single-sourced
here, resource shapes are the follow-on step.

Field types use TS syntax (they only matter to the generator). ``None``-valued
optional fields are kept as-is (→ ``| null`` in the TS type), matching what the
historical inline literals emitted.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── auxiliary payload shapes (generated as TS interfaces, referenced by events) ──
AUX_TYPES: dict[str, dict[str, str]] = {}

# TS types whose interiors stay hand-maintained in frontend/src/types.ts and are
# referenced by name from event fields (resource-shape generation is the follow-on
# step; the EVENTS themselves are fully single-sourced here).
RESOURCE_IMPORTS = ("Entity", "JobRow", "ManifestSnapshot", "ModuleEnableOffer",
                    "PlanConcern", "PlanStepShape")


@dataclass(frozen=True)
class EventSpec:
    name: str
    channel: str                     # "turn" | "notify"
    doc: str
    required: dict[str, str] = field(default_factory=dict)   # field → TS type
    optional: dict[str, str] = field(default_factory=dict)


def _ev(name: str, channel: str, doc: str, required: dict[str, str] | None = None,
        optional: dict[str, str] | None = None) -> EventSpec:
    return EventSpec(name=name, channel=channel, doc=doc,
                     required=required or {}, optional=optional or {})


EVENTS: dict[str, EventSpec] = {s.name: s for s in [
    # ── turn channel ─────────────────────────────────────────────────────────
    _ev("manifest", "turn",
        "Turn-start drawer sidecar: the structured Manifest snapshot + the run_id "
        "the client needs for Stop/reattach.",
        {"manifest": "ManifestSnapshot", "run_id": "string"}),
    _ev("delta", "turn", "A streamed chunk of assistant text.",
        {"text": "string"}),
    _ev("tool_start", "turn",
        "The model issued a tool_use block; UI renders the running chip.",
        {"name": "string", "input": "Record<string, unknown>", "tool_use_id": "string"}),
    _ev("tool_progress", "turn",
        "A coarse progress line for a running tool (phase ticks).",
        {"name": "string", "tool_use_id": "string"},
        {"message": "string | null", "phase": "string | null"}),
    _ev("tool_chunk", "turn",
        "Live output tail from a running tool (stdout/stderr), coalesced.",
        {"tool_use_id": "string", "stream": "'stdout' | 'stderr'", "text": "string",
         "bytes_total": "number", "elapsed_s": "number"}),
    _ev("tool_result", "turn", "A finished tool call's result envelope.",
        {"name": "string", "result": "Record<string, unknown>", "tool_use_id": "string"}),  # noqa: seam — wire field name, not the entity type
    _ev("entity_registered", "turn",
        "A new entity was minted during the turn (artifact registrar / create_scenario).",
        {"entity": "Entity"}),
    _ev("plan", "turn",
        "present_plan halt-after card: the structured plan (steps enriched with "
        "param_form where a pipeline schema is known).",
        {"entity_id": "string", "title": "string", "summary": "string",
         "rationale": "string", "assumptions": "string[]",
         "steps": "(PlanStepShape | string)[]", "concerns": "PlanConcern[]"}),
    _ev("clarification_pending", "turn",
        "ask_clarification halt-after: the question, plus one-click Enable options "
        "when it is about a turned-off module.",
        {"question": "string", "tool_use_id": "string", "run_id": "string"},
        {"enable": "ModuleEnableOffer"}),
    _ev("approval_pending", "turn",
        "Approval gate halt-before: the held tool runs only after /resume approves.",
        {"tool_name": "string", "summary": "string", "tool_use_id": "string",
         "run_id": "string", "policy": "string | null"}),
    _ev("deferred_tool_pending", "turn",
        "A deferred tool parked the turn (AWAITING_TOOL_RESULT); the result arrives "
        "via /tool_result or a finished background job.",
        {"tool_name": "string", "deferred_id": "string", "tool_use_id": "string",
         "run_id": "string"}),
    _ev("job_submitted", "turn", "A background job was queued for this turn.",
        {"job": "JobRow"}),
    _ev("notice", "turn",
        "A transient, non-fatal notice line (model busy, output cap hit).",
        {"text": "string"}),
    _ev("cancelled", "turn", "The turn was cancelled (Stop).",
        {"run_id": "string"}, {"reason": "string | null"}),
    _ev("error", "turn", "The turn failed; `text` is the user-facing message.",
        {"text": "string"}, {"detail": "string"}),
    _ev("usage", "turn", "Guide token usage for the turn (emitted before done).",
        {"input": "number", "output": "number",
         "cache_read": "number", "cache_write": "number"}),
    _ev("done", "turn", "Terminal sentinel: the stream is complete."),
    _ev("suggestion_logged", "turn",
        "An end-of-turn reflection hook logged a context suggestion.",
        {"trigger": "string"}, {"entity_type": "string | null"}),

    # ── notify channel ───────────────────────────────────────────────────────
    _ev("hello", "notify", "Connect handshake for /api/notifications."),
    _ev("entity_updated", "notify",
        "An entity changed out-of-band (captions, revisions, promotions); the UI "
        "re-fetches. `reason` names the change; the optional keys carry the "
        "revision-chain specifics.",
        {"entity_id": "string", "reason": "string"},
        {"member_id": "string", "attached_entity_id": "string",
         "wasRevisionOf": "string", "superseded": "string[]",
         "reanchored": "string[]", "deleted_revision": "string",
         "re_parented_children": "unknown[]", "re_anchored_members": "unknown[]",
         "new_current": "string", "restored": "string[]"}),
    _ev("module", "notify",
        "Module install/state change (Settings → Modules toasts + live refresh).",
        {"id": "string", "title": "string", "state": "string"},
        {"progress": "string | null", "error": "string | null"}),
    _ev("compute", "notify",
        "Compute-site change (Settings → Compute live refresh): registration "
        "narration (weft bootstrap.step relay), background queue verification, "
        "connect/disconnect.",
        {"site": "string", "phase": "string"},
        {"step": "string | null", "note": "string | null", "ok": "boolean | null"}),
]}


# ── builders ──────────────────────────────────────────────────────────────────
def _build(spec: EventSpec, fields: dict[str, Any]) -> dict:
    missing = [k for k in spec.required if k not in fields]
    unknown = [k for k in fields if k not in spec.required and k not in spec.optional]
    if missing or unknown:
        raise TypeError(
            f"wire.{spec.name}: "
            + (f"missing required {missing} " if missing else "")
            + (f"unknown fields {unknown}" if unknown else ""))
    return {"type": spec.name, **fields}


def __getattr__(name: str):
    """wire.<event_name>(**fields) → validated payload dict. The registry above is
    the single source; builders are derived, so adding an event is one entry."""
    spec = EVENTS.get(name)
    if spec is None:
        raise AttributeError(f"module 'wire' has no event {name!r}")

    def builder(**fields: Any) -> dict:
        return _build(spec, fields)
    builder.__name__ = name
    builder.__doc__ = spec.doc
    return builder


def __dir__():
    return sorted(list(globals()) + list(EVENTS))


# ── transport-side check (lenient) ───────────────────────────────────────────
_warned: set[str] = set()


def check(payload: Any, channel: str) -> None:
    """Validate an outbound payload against the registry; warn once per event
    name on violation. Called by the transports (turn_sink / notifications) —
    the last line of defence, deliberately non-fatal: a malformed payload
    degrades to a log line, never a broken stream."""
    if not isinstance(payload, dict):
        return
    name = payload.get("type")
    key = f"{channel}:{name}"
    if key in _warned:
        return
    spec = EVENTS.get(name or "")
    problem = None
    if spec is None:
        problem = "unknown event type"
    elif spec.channel != channel:
        problem = f"declared for channel {spec.channel!r}, sent on {channel!r}"
    else:
        missing = [k for k in spec.required if k not in payload]
        unknown = [k for k in payload
                   if k not in spec.required and k not in spec.optional
                   and k not in ("type", "seq")]
        if missing or unknown:
            problem = (f"missing {missing} " if missing else "") + \
                      (f"unknown {unknown}" if unknown else "")
    if problem:
        _warned.add(key)
        print(f"[wire] non-conformant {channel} event {name!r}: {problem} "
              f"(keys: {sorted(payload)})")


def schema() -> dict:
    """The whole contract as plain data (for the TS generator and tests)."""
    return {
        "aux_types": AUX_TYPES,
        "resource_imports": list(RESOURCE_IMPORTS),
        "events": {
            n: {"channel": s.channel, "doc": s.doc,
                "required": s.required, "optional": s.optional}
            for n, s in EVENTS.items()
        },
    }
