"""
Context-package assembly for the Guide.

When a user is focused on an entity, the Guide's system prompt is augmented
with a compact description of that entity — its metadata, producing-code
summary, and (eventually) provenance neighborhood and per-entity advisor
notes. This is the Phase-1 implementation: minimal, hand-crafted rules.

§3.6 (self-improving context) will plug in here once Phase 5 begins.
"""
from __future__ import annotations
from typing import Optional

from db import get_entity, WORKSPACE_ID


def focus_preamble(entity_id: Optional[str]) -> str:
    """
    Return a short string to prepend to the system prompt describing what the
    user is currently focused on. Empty string when focused on the workspace
    root (i.e. unfocused).
    """
    if not entity_id or entity_id == WORKSPACE_ID:
        return ""

    e = get_entity(entity_id)
    if not e:
        return ""

    lines = [
        "### Currently focused",
        f"The user is looking at a {e['type']} titled \"{e['title']}\" (id: {e['id']}).",
    ]

    if e.get("status") and e["status"] != "active":
        lines.append(f"Status: {e['status']}.")

    if e.get("artifact_path"):
        lines.append(f"Artifact on disk: {e['artifact_path']}")

    if e.get("producing_code"):
        snippet = e["producing_code"].strip()
        if len(snippet) > 600:
            snippet = snippet[:600] + "\n# ... (truncated)"
        lines.append("Producing code:")
        lines.append("```python")
        lines.append(snippet)
        lines.append("```")

    if e.get("producing_params"):
        lines.append(f"Producing parameters: {e['producing_params']}")

    if e.get("parent_entity_id") and e["parent_entity_id"] != WORKSPACE_ID:
        parent = get_entity(e["parent_entity_id"])
        if parent:
            lines.append(
                f"Derived from: {parent['type']} \"{parent['title']}\" "
                f"(id: {parent['id']})."
            )

    lines.append(
        "When the user asks questions, answer in the context of this entity. "
        "Do not require the user to re-identify which entity they mean."
    )

    return "\n".join(lines) + "\n\n"
