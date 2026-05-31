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
    article = "an" if (e['type'][:1].lower() in "aeiou") else "a"
    lines = [
        "### Currently focused",
        f"The user is looking at {article} {e['type']} titled \"{e['title']}\" (id: {e['id']}).",
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


def render_project_sidebar(thread_id: Optional[str] = None) -> str:
    """A compact, always-fresh snapshot of project-wide entities the agent
    might want to reference across threads — datasets, sibling threads,
    and counts of active curated items. Injected near the top of the
    system prompt as STRUCTURED context (not narrative).

    Per the history-compaction redesign (misc/history_compaction_redesign.md
    §4.3): shared cross-thread state belongs HERE — queryable, deterministic,
    no LLM. The thread's own chat history stays as the conversational
    record. This is Phase 1 of that redesign.

    Empty string when there are no entities to surface (fresh project),
    so we don't inject a confusing "PROJECT — (nothing)" block.
    """
    from core.graph.entities import list_entities, count_entities
    parts: list[str] = []
    parts.append("[PROJECT — current snapshot]")

    # Datasets: small N, very useful. Show name + path (the actual disk
    # location the agent can pass to inspect_upload / read straight away).
    datasets = list_entities(type_filter="dataset", include_archived=False)
    if datasets:
        parts.append(f"Datasets ({len(datasets)}):")
        for e in datasets[:10]:                          # cap at 10
            title = (e.get("title") or "").strip() or e.get("id", "")
            path = e.get("artifact_path") or ""
            line = f"  - {title}"
            if path:
                line += f"  →  {path}"
            parts.append(line)
        if len(datasets) > 10:
            parts.append(f"  (… {len(datasets)-10} more — list_data_files for full list)")

    # Threads: small N usually. Mark the CURRENT one. Title-only — paths/
    # detail belong on a focused-thread card, not the firehose.
    threads = list_entities(type_filter="thread", include_archived=False)
    if threads:
        parts.append(f"Threads ({len(threads)}):")
        for t in threads[:12]:                           # cap at 12
            tid = t.get("id", "")
            title = (t.get("title") or "").strip()
            marker = " (this thread)" if thread_id and tid == thread_id else ""
            parts.append(f"  - {tid}{marker} — {title!r}")
        if len(threads) > 12:
            parts.append(f"  (… {len(threads)-12} more)")

    # Curation counts — Results / Claims / Findings are the user's
    # judgments. Cheap one-liner; the agent can look them up by name
    # via list_entities if needed.
    n_results  = count_entities(type_filter="result",  include_archived=False)
    n_claims   = count_entities(type_filter="claim",   include_archived=False)
    n_findings = count_entities(type_filter="finding", include_archived=False)
    if n_results or n_claims or n_findings:
        parts.append(
            f"Curated entities: results={n_results}  claims={n_claims}  findings={n_findings}"
        )

    parts.append("[/PROJECT]")
    # If we collected nothing (no datasets, no threads, no curation),
    # don't emit a useless wrapper — the agent should not be told about
    # an empty project state every turn.
    if len(parts) <= 2:
        return ""
    return "\n".join(parts) + "\n"


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
    role: str = "primary",
) -> Manifest:
    """Compose the Manifest for one Guide (or advisor) turn.

    `role` (A3) selects the audience: "primary" gets focus + thread +
    policy; advisor roles get focus + policy only (no thread firehose).
    Today the structural difference is small — Manifest doesn't yet
    encode different recent-activity windows by role — but the
    parameter is plumbed so future passes (B-tier) can narrow context
    per role without changing call sites.

    Does not touch tools, knowledge files, memory, or skills — those
    slots get filled by the system-prompt assembler at render time.
    """
    focus, policy_text = _build_focus(focus_entity_id)

    thread: ThreadContext | None = None
    if thread_id and role == "primary":
        # Bio owns the thread-context text shape (pinned figures, claims).
        # Deferred import to avoid pulling bio at module-load time.
        # Advisors don't get the thread firehose — they're invoked for
        # a focused critique, not to drive a conversation forward.
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
