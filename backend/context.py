"""
Context-package assembly for the Guide.

When a user is focused on an entity, the Guide's system prompt is augmented
with a compact description of that entity — its metadata, producing-code
summary, etc. Phase 11 adds two things on top:
  - The function reports *which fields* it loaded (for instrumentation).
  - It concatenates any per-entity-type policy file that's been promoted
    via the adaptive-context loop.
"""
from __future__ import annotations
from typing import Optional

from db import get_entity, WORKSPACE_ID
from adaptive import policy_for


def focus_preamble(entity_id: Optional[str]) -> str:
    """Plain-text preamble for system-prompt prepend."""
    text, _ = _build(entity_id)
    return text


def focus_preamble_with_fields(entity_id: Optional[str]) -> tuple[str, list[str]]:
    """Same as focus_preamble, plus the list of fields loaded (for logging)."""
    return _build(entity_id)


def _build(entity_id: Optional[str]) -> tuple[str, list[str]]:
    if not entity_id or entity_id == WORKSPACE_ID:
        # Even for workspace, an entity-type-specific policy may apply for
        # "no focused entity" guidance.
        policy = policy_for("workspace")
        if policy:
            return policy + "\n", ["policy"]
        return "", []

    e = get_entity(entity_id)
    if not e:
        return "", []

    fields: list[str] = ["type", "title"]
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

    # For an analysis RUN, surface what it is and — importantly — what it
    # produced, so the Guide can connect a question about a specific output
    # (e.g. "look at qc_S10.png") to this run without the user re-explaining.
    if e["type"] == "analysis":
        run = (e.get("metadata") or {}).get("run") or {}
        bits = []
        if run.get("status"):
            bits.append(f"status {run['status']}")
        if run.get("executor"):
            bits.append(f"executor {run['executor']}" + (f" on {run['where']}" if run.get("where") else ""))
        if bits:
            lines.append("This is an analysis run — " + ", ".join(bits) + ".")
            fields.append("run_meta")
        if run.get("command"):
            cmd = run["command"].strip()
            lines.append("Command: " + (cmd[:300] + " …" if len(cmd) > 300 else cmd))
            fields.append("run_command")
        labels = [o.get("label", "") for o in (run.get("outputs") or []) if o.get("label")]
        if labels:
            lines.append("Outputs produced by this run: " + ", ".join(labels[:30]) + ".")
            fields.append("run_outputs")
        if (run.get("bulk") or {}).get("count"):
            lines.append(f"(plus {run['bulk']['count']} bulk files, not individually listed).")

    lines.append(
        "When the user asks questions, answer in the context of this entity. "
        "Do not require the user to re-identify which entity they mean."
    )

    preamble = "\n".join(lines) + "\n\n"

    # Append any promoted context policy for this entity type. The text is
    # human-edited markdown, deliberately concatenated wholesale.
    policy = policy_for(e["type"])
    if policy:
        preamble += policy + "\n"
        fields.append("policy")

    return preamble, fields
