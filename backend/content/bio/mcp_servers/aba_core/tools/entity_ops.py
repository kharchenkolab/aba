"""Generic entity-management primitives (entity-mgmt refactor, 2026-06-08).

The agent's entity-management surface has historically been a grab-bag
of type-specific tools (pin_entity, promote_to_result, create_claim,
annotate_entity, …) clustered by lifecycle phase. That works for the
common workflows but leaves visible gaps: the agent can't read a
member's caption, can't edit a Result's interpretation, can't list
caveats on a Claim. See misc/entity_mgmt_refactor.md (this session).

This module is the YAML-driven generic surface that COMPLEMENTS the
existing workflow tools (make_revision, promote_to_result, etc., which
encode multi-step lifecycle ops worth keeping named):

  - read_entity(id, fields=None)  — Phase 2
  - update_entity_fields(id, fields={})  — Phase 4
  - list_entity_operations(type)   — Phase 5

The YAML contract:
  - entity_types/<type>.yaml `focus.agent_sees`  → readable fields
  - entity_types/<type>.yaml `focus.agent_can_update`  → writable fields
    (Phase 3 — added per type with conservative defaults; matches what
    the HTTP API exposes for editing today.)
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from mcp.server.fastmcp import FastMCP


# ── Field projectors ───────────────────────────────────────────────
#
# Maps an `agent_sees` field name to a function that extracts/computes
# its value from the entity dict. Keeps read_entity declarative — the
# only knowledge here is "this field name means this projection".
#
# Most projectors are dict accessors (top-level column or
# metadata.<field>). The "summary" ones do small lookups (resolve a
# parent id to title+type; resolve member.ref to figure title +
# caption + caption_origin).


def _md(e: dict, key: str, default=None):
    return (e.get("metadata") or {}).get(key, default)


def _parent_summary(e: dict) -> Optional[dict]:
    """parent_entity_id → {id, type, title}."""
    pid = e.get("parent_entity_id")
    if not pid:
        return None
    from core.graph.entities import get_entity
    p = get_entity(pid)
    if not p:
        return {"id": pid, "missing": True}
    return {"id": p["id"], "type": p.get("type"),
            "title": (p.get("title") or "").strip()}


def _members_summary(e: dict) -> list[dict]:
    """metadata.members[] → list of {member_id, kind, ref, title,
    caption, caption_origin, displayed_id}. Resolves figure/table refs
    to current cells (with chain-aware displayed id when revisions
    exist) so the agent doesn't get stale anchor ids.

    This is the field that fixes the long-standing 'agent can't read
    the auto-generated caption' gap — the caption + caption_origin
    are surfaced here per member, no separate tool roundtrip.
    """
    members = (e.get("metadata") or {}).get("members") or []
    if not isinstance(members, list):
        return []
    from core.graph.entities import get_entity
    out: list[dict] = []
    for m in members:
        if not isinstance(m, dict):
            continue
        row: dict[str, Any] = {
            "member_id": m.get("id"),
            "kind": m.get("kind"),
            "ref": m.get("ref"),
        }
        if m.get("caption") is not None:
            row["caption"] = m.get("caption")
        if m.get("caption_origin"):
            row["caption_origin"] = m.get("caption_origin")
        if m.get("kind") == "text" and m.get("text") is not None:
            row["text"] = m.get("text")
        if m.get("ref"):
            ref = m["ref"]
            cell = get_entity(ref)
            if cell:
                row["title"] = (cell.get("title") or "").strip()
                row["artifact_path"] = cell.get("artifact_path")
                # Chain-aware displayed id (panel shows chain[0] = latest)
                if cell.get("type") in ("figure", "table"):
                    try:
                        from content.bio.graph.figure_history import figure_history
                        chain = figure_history(ref)
                        if chain:
                            row["displayed_id"] = chain[0]["id"]
                            row["chain_length"] = len(chain)
                    except Exception:  # noqa: BLE001
                        pass
        out.append(row)
    return out


def _evidence_summary(e: dict) -> list[dict]:
    """Look up incoming `supports` edges (claim/finding → result/figure)
    to produce {id, type, title} per piece of evidence. For Findings +
    Claims that anchor evidence-bearing curation entities."""
    from core.graph.edges import edges_from
    from core.graph.entities import get_entity
    out: list[dict] = []
    for edge in edges_from(e["id"]):
        if edge.get("rel_type") != "supports":
            continue
        target = get_entity(edge.get("target_id"))
        if target:
            out.append({"id": target["id"], "type": target.get("type"),
                        "title": (target.get("title") or "").strip()})
    return out


def _advisor_notes(e: dict) -> list[dict]:
    """metadata.advisor_notes (set by various advisor passes — skeptic,
    explorer, etc.). Returned as-is."""
    return (e.get("metadata") or {}).get("advisor_notes") or []


def _status_log_tail(e: dict) -> list[dict]:
    """Last 5 entries of metadata.status_log (used by Claim's confidence
    ladder history)."""
    log = (e.get("metadata") or {}).get("status_log") or []
    return log[-5:] if isinstance(log, list) else []


# Field name → extractor. Lambdas for simple cases; named functions
# above for the projecting ones.
_PROJECTORS: dict[str, Callable[[dict], Any]] = {
    # Top-level columns
    "title":              lambda e: e.get("title"),
    "status":             lambda e: e.get("status"),
    "tags":               lambda e: e.get("tags") or [],
    "notes":              lambda e: e.get("notes") or "",
    "artifact_path":      lambda e: e.get("artifact_path"),
    "exec_id":            lambda e: e.get("exec_id"),
    "artifact_kind":      lambda e: e.get("artifact_kind"),
    "artifact_idx":       lambda e: e.get("artifact_idx"),
    "producing_params":   lambda e: e.get("producing_params"),
    "scenario_of":        lambda e: e.get("scenario_of"),
    "created_at":         lambda e: e.get("created_at"),
    "updated_at":         lambda e: e.get("updated_at"),
    # metadata.* scalars
    "interpretation":     lambda e: _md(e, "interpretation"),
    "interpretation_origin": lambda e: _md(e, "interpretation_origin"),
    "origin":             lambda e: _md(e, "origin"),
    "statement":          lambda e: _md(e, "statement"),
    "confidence":         lambda e: _md(e, "confidence"),
    "text":               lambda e: _md(e, "text"),
    "thread_id":          lambda e: _md(e, "thread_id"),
    "source":             lambda e: _md(e, "source"),
    "organism":           lambda e: _md(e, "organism"),
    "size_bytes":         lambda e: _md(e, "size_bytes"),
    "file_count":         lambda e: _md(e, "file_count"),
    "description":        lambda e: _md(e, "description"),
    "layout_hint":        lambda e: _md(e, "layout_hint"),
    "caveats":            lambda e: _md(e, "caveats") or [],
    "alternatives":       lambda e: _md(e, "alternatives") or [],
    # Projections (lookups, walks)
    "parent_summary":     _parent_summary,
    "members_summary":    _members_summary,
    "evidence_summary":   _evidence_summary,
    "advisor_notes":      _advisor_notes,
    "status_log_tail":    _status_log_tail,
}


def _project(e: dict, field: str) -> Any:
    """Return the projected value of `field` for entity `e`. Falls back
    to direct dict access if no projector is registered — keeps the
    door open for ad-hoc explicit-field reads."""
    fn = _PROJECTORS.get(field)
    if fn is not None:
        return fn(e)
    # Fallback: top-level then metadata
    if field in e:
        return e[field]
    md = e.get("metadata") or {}
    if field in md:
        return md[field]
    return None


def _agent_sees_for(entity_type: str) -> list[str]:
    """Read the YAML's focus.agent_sees for this type. Empty list when
    the type isn't registered or hasn't declared the slot — caller
    falls back to a small universal default in that case."""
    from core.entity_types import get_type
    spec = get_type(entity_type)
    if not spec:
        return []
    return list((spec.focus or {}).get("agent_sees") or [])


_UNIVERSAL_FALLBACK = ["title", "status", "tags", "notes"]


def register_entity_ops_tools(mcp: FastMCP) -> None:
    """Register the generic entity-mgmt primitives on `mcp`."""

    @mcp.tool()
    def read_entity(entity_id: str,
                    fields: list[str] | None = None,
                    aba_ctx_id: str | None = None) -> dict:
        """Read an entity's fields. Works for ANY entity type (figure,
        table, result, dataset, claim, finding, narrative, run, …).

        Returns a dict shaped {id, type, title, fields: {...}}.

        Field selection:
          - fields=None (default) → returns the fields declared in the
            type's YAML `focus.agent_sees` slot. This is the curated
            view of "what the agent should see for this type" — title,
            interpretation, captions, evidence summaries, etc. depending
            on the type. For a Result this includes per-member CAPTIONS
            (caption + caption_origin), the long-standing read-blindness
            gap that prompted this tool.
          - fields=[...] → returns the explicitly requested fields, in
            order. Use this to focus on one field (e.g. fields=["caveats"])
            or to read a field not in agent_sees. Unknown field names
            return None for that key (no error).

        Notable shapes (when included):
          - members_summary: list of {member_id, kind, ref, title,
            caption, caption_origin, displayed_id, chain_length} — the
            chain-aware displayed_id is the entity_id the panel actually
            shows (latest revision), which is what tool calls like
            make_revision should operate on.
          - evidence_summary: list of {id, type, title} for supports
            edges (Claim/Finding → Result/Figure).
          - parent_summary: {id, type, title} from parent_entity_id.

        Returns {"error": "..."} if entity not found.
        """
        from core.graph.entities import get_entity
        e = get_entity(entity_id)
        if not e:
            return {"error": f"entity {entity_id} not found"}
        ftype = e.get("type") or "entity"
        selected = list(fields) if fields else (_agent_sees_for(ftype) or _UNIVERSAL_FALLBACK)
        out: dict[str, Any] = {
            "id": e["id"], "type": ftype,
            "title": (e.get("title") or "").strip(),
            "fields": {},
        }
        for f in selected:
            out["fields"][f] = _project(e, f)
        return out
