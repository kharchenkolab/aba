"""Manifest dataclasses — the structured per-turn assembly.

Arch3_plan.md Pass C: Manifest is the single object that defines what
enters a turn. Today's fields are minimal; the drawer feature (deferred)
adds pinned/recent/suggested/memory/skills slots without breaking the
existing shape.

The assembler in core/manifest/assembler.py produces a Manifest; the
system-prompt assembler renders selected slots into the cached prefix +
volatile suffix the Anthropic API actually sees.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class FocusCard:
    """Per-entity-type focus card. `fields` is populated by a registered
    bio/cards/<type>.py builder; opaque to core."""
    entity_id: str
    entity_type: str
    title: str
    status: str
    text: str                            # rendered text for the system prompt
    fields_loaded: list[str] = field(default_factory=list)  # for log_context_assembly


@dataclass
class ThreadContext:
    """Bio-shaped thread context — pinned evidence + claims. Today this is
    rendered to text by the bio side and embedded as `text`; in Pass-D-and-
    later the structured pieces become drawer slots."""
    thread_id: str
    text: str = ""                       # rendered text for the system prompt


@dataclass
class Manifest:
    session_id: str
    turn_index: int
    focus: FocusCard | None = None
    thread: ThreadContext | None = None
    policy_text: str = ""                # adaptive.policy_for(focus.entity_type)
    # The rendered system prompt is composed in core/manifest/system_prompt_assembler;
    # the manifest carries the inputs, not the final string.

    def fields_for_audit(self) -> list[str]:
        """The set of focus-card field names actually populated this turn.
        Logged via log_context_assembly so we can see what the agent saw."""
        if self.focus is None:
            return []
        return list(self.focus.fields_loaded)

    def to_dict(self) -> dict:
        """JSON-serializable snapshot for the SSE sidecar / drawer UI."""
        return {
            "session_id": self.session_id,
            "turn_index": self.turn_index,
            "focus": (
                {
                    "entity_id": self.focus.entity_id,
                    "entity_type": self.focus.entity_type,
                    "title": self.focus.title,
                    "status": self.focus.status,
                    "text": self.focus.text,
                    "fields_loaded": list(self.focus.fields_loaded),
                }
                if self.focus
                else None
            ),
            "thread": (
                {"thread_id": self.thread.thread_id, "text": self.thread.text}
                if self.thread
                else None
            ),
            "policy_text": self.policy_text,
        }
