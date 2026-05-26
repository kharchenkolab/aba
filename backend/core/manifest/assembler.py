"""Manifest assembler — the single owner of per-turn context composition.

Arch3_plan.md Pass C. Dispatches to per-entity-type focus-card builders
registered by content (bio/cards/*.py) at import time. The assembler
itself does not import content; it exposes a registration API and uses
whatever builder was registered for the focus entity's type, falling
back to a generic builder for unregistered types.
"""
from __future__ import annotations
from typing import Callable, Optional

from core.graph.entities import get_entity
from core.graph._schema import WORKSPACE_ID
from core.manifest.types import FocusCard, ThreadContext, Manifest


# Registry: type-name → builder(entity_dict) -> (text, fields_loaded)
CardBuilder = Callable[[dict], tuple[str, list[str]]]
_BUILDERS: dict[str, CardBuilder] = {}


def register_card_builder(entity_type: str, builder: CardBuilder) -> None:
    """Content registers per-type builders at import time, e.g.
        register_card_builder(\"type_a\", build_type_a_card)
    Each builder takes the entity dict and returns (text_for_system_prompt,
    list_of_field_names_loaded). The list goes into log_context_assembly so
    we can see what the agent saw."""
    _BUILDERS[entity_type] = builder


def _generic_card(entity: dict) -> tuple[str, list[str]]:
    """Fallback builder used when no per-type builder is registered. Renders
    the universal fields (type, title, status, artifact_path, producing_code,
    producing_params, parent_entity_id summary, tags, notes) in the same
    format the pre-refactor focus_preamble produced."""
    import json
    fields: list[str] = ["type", "title"]
    e = entity
    lines = [
        "### Currently focused",
        f"The user is looking at a {e['type']} titled \"{e['title']}\" (id: {e['id']}).",
    ]
    if e.get("status") and e["status"] != "active":
        lines.append(f"Status: {e['status']}.")
        fields.append("status")
    if e.get("artifact_path"):
        lines.append(f"Artifact on disk: {e['artifact_path']}")
        fields.append("artifact_path")
    if e.get("producing_code"):
        snippet = e["producing_code"].strip()
        if len(snippet) > 600:
            snippet = snippet[:600] + "\n# ... (truncated)"
        lines.append("Producing code:")
        lines.append("```python")
        lines.append(snippet)
        lines.append("```")
        fields.append("producing_code")
    if e.get("producing_params"):
        lines.append(f"Producing parameters: {e['producing_params']}")
        fields.append("producing_params")
    if e.get("parent_entity_id") and e["parent_entity_id"] != WORKSPACE_ID:
        parent = get_entity(e["parent_entity_id"])
        if parent:
            lines.append(
                f"Derived from: {parent['type']} \"{parent['title']}\" "
                f"(id: {parent['id']})."
            )
            fields.append("parent_summary")
    if e.get("tags"):
        lines.append(f"Tags: {', '.join(e['tags'])}")
        fields.append("tags")
    if e.get("notes"):
        lines.append(f"User notes: {e['notes']}")
        fields.append("notes")
    return "\n".join(lines), fields


def _build_focus(focus_entity_id: Optional[str]) -> tuple[FocusCard | None, str]:
    """Build the focus card. Returns (card, policy_text). The bio adaptive
    layer contributes policy_text via a deferred import — it is content,
    not platform, but the manifest carries the slot."""
    if not focus_entity_id or focus_entity_id == WORKSPACE_ID:
        # The "workspace" focus is the no-focus case: just the optional
        # policy text for workspace-scoped guidance.
        from content.bio.lifecycle.adaptive import policy_for  # deferred
        policy = policy_for("workspace") or ""  # noqa: seam (workspace pseudo-type)
        return None, policy

    e = get_entity(focus_entity_id)
    if not e:
        return None, ""

    builder = _BUILDERS.get(e["type"], _generic_card)
    text, fields_loaded = builder(e)
    # The card text includes a trailing "answer in this context" line
    # the renderer appends, so it's not duplicated by per-type builders.

    from content.bio.lifecycle.adaptive import policy_for  # deferred
    policy = policy_for(e["type"]) or ""

    card = FocusCard(
        entity_id=e["id"],
        entity_type=e["type"],
        title=e["title"],
        status=e.get("status") or "active",
        text=text,
        fields_loaded=fields_loaded,
    )
    return card, policy


def render_focus_preamble(manifest: Manifest) -> tuple[str, list[str]]:
    """Produce the system-prompt-prepend text + the fields-loaded audit list.
    Matches the pre-Pass-C `context.focus_preamble_with_fields` output
    exactly so prompt-cache hashes don't change."""
    fields = list(manifest.focus.fields_loaded) if manifest.focus else []
    if manifest.focus is None:
        # Workspace / no focus: just optional policy text.
        if manifest.policy_text:
            fields.append("policy")
            return manifest.policy_text + "\n", fields
        return "", []
    # The focused-entity case appends a trailing CTA line + two-newline
    # separator + policy text (if any) + one-newline tail.
    text = manifest.focus.text + "\n"
    text += (
        "When the user asks questions, answer in the context of this entity. "
        "Do not require the user to re-identify which entity they mean."
    )
    text += "\n\n"
    if manifest.policy_text:
        text += manifest.policy_text + "\n"
        fields.append("policy")
    return text, fields


def build_manifest(
    *,
    session_id: str,
    turn_index: int,
    focus_entity_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> Manifest:
    """Compose the Manifest for one Guide (or advisor) turn.

    Does not touch tools, knowledge files, memory, or skills — those
    slots get filled by the system-prompt assembler at render time.
    """
    focus, policy_text = _build_focus(focus_entity_id)

    thread: ThreadContext | None = None
    if thread_id:
        # Bio owns the thread-context text shape (pinned figures, claims).
        # Deferred import to avoid pulling bio at module-load time.
        from content.bio.cards.thread import render_thread_context  # noqa: seam
        text = render_thread_context(thread_id)
        thread = ThreadContext(thread_id=thread_id, text=text)

    return Manifest(
        session_id=session_id,
        turn_index=turn_index,
        focus=focus,
        thread=thread,
        policy_text=policy_text,
    )
