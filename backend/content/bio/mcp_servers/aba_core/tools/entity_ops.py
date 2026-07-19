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

  - read_entity(id, fields=None)         — Phase 2
  - update_entity_fields(id, fields={})  — Phase 4
  - list_entity_operations(type)         — Phase 5

The YAML contract:
  - entity_types/<type>.yaml `focus.agent_sees`       → readable fields
  - entity_types/<type>.yaml `focus.agent_can_update` → writable fields
    (Phase 3 — added per type with conservative defaults; matches what
    the HTTP API exposes for editing today.)
  - entity_types/<type>.yaml `creation.agent_tools`   → typed workflow
    tools that touch this type (read by list_entity_operations).
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


def _run_outputs_summary(e: dict) -> Optional[dict]:
    """Run (analysis) → a CHEAP keep/retention roll-up: retain-row counts
    per state from weft's LOCAL index + the run's own metadata (outputs
    count, retention_alert). Answers "what did this run produce and is it
    safe" without the durable view — run_durable_view is the UI Files-panel
    builder and does up to 50 live per-file stat round-trips, far too heavy
    to fire on EVERY default read_entity of a run (review F2). No remote
    I/O here: retention.retained() reads local substrate state only.

    Defensive + best-effort: any failure → None (the field simply doesn't
    surface rather than erroring the whole read)."""
    try:
        md = e.get("metadata") or {}
        out: dict = {}
        n_outputs = len(((md.get("run") or {}).get("outputs")) or [])
        if n_outputs:
            out["outputs"] = n_outputs
        alert = md.get("retention_alert")
        if alert:
            out["retention_alert"] = alert
        try:
            from core.compute import retention
            states: dict = {}
            for row in (retention.retained(label=e["id"]) or []):
                s = str(row.get("state") or "unknown")
                states[s] = states.get(s, 0) + 1
            if states:
                out["keeps"] = states     # e.g. {"done": 3, "pinned-pending": 1}
        except Exception:  # noqa: BLE001 — substrate down: metadata half still surfaces
            pass
        return out or None
    except Exception:  # noqa: BLE001
        return None


def _dataset_drift_state(e: dict) -> Optional[str]:
    """Dataset → a single human-readable drift line derived from the
    `source_missing` / `source_changed` metadata keys the recheck /
    revalidate routes record (datasets.py). Drives the same signal as
    the UI DriftBanner. None when the source is clean (the common case),
    so a healthy dataset shows nothing."""
    md = e.get("metadata") or {}
    if md.get("source_missing"):
        home = md.get("home") or {}
        path = home.get("path") if isinstance(home, dict) else None
        return f"source missing ({path})" if path else "source missing"
    if md.get("source_changed"):
        return "source changed since registration"
    return None


def _thread_scope_ids(e: dict) -> list[str]:
    """The thread_id values entities carry to signal membership in this
    thread: its own id, plus the "default" sentinel when this is the
    implicit default thread."""
    ids = [e["id"]]
    if (e.get("metadata") or {}).get("is_default"):
        ids.append("default")
    return ids


def _thread_pinned_count(e: dict) -> int:
    """Thread → count of active Result entities scoped to this thread —
    the "N pinned" the ProjectTree shows (a Result is the wrapper the
    user creates when they pin something). Direct thread_id membership
    (find_entities metadata scoping), mirroring the server query."""
    try:
        from core.graph.entities import find_entities
        n = 0
        for tid in _thread_scope_ids(e):
            n += len(find_entities(type="result", status="active",
                                   metadata_contains={"thread_id": tid}))
        return n
    except Exception:  # noqa: BLE001
        return 0


def _thread_claim_count(e: dict) -> int:
    """Thread → count of Claim entities scoped to this thread — the
    "N claims" the ProjectTree shows. Direct thread_id membership."""
    try:
        from core.graph.entities import find_entities
        n = 0
        for tid in _thread_scope_ids(e):
            n += len(find_entities(type="claim", include_archived=False,
                                   metadata_contains={"thread_id": tid}))
        return n
    except Exception:  # noqa: BLE001
        return 0


# Field name → extractor. Lambdas for simple cases; named functions
# above for the projecting ones.
_PROJECTORS: dict[str, Callable[[dict], Any]] = {
    # Top-level columns
    "title":              lambda e: e.get("title"),
    "status":             lambda e: e.get("status"),
    "tags":               lambda e: e.get("tags") or [],
    "notes":              lambda e: e.get("notes") or "",
    # retention-critical: a Run whose keepers could not be kept carries
    # this alert — invisible to the agent before it was projected here
    # (live finding: the agent read the run and IMPROVISED a wrong cause)
    "retention_alert":    lambda e: _md(e, "retention_alert"),
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
    "run_outputs_summary": _run_outputs_summary,
    "drift_state":        _dataset_drift_state,
    "pinned_count":       _thread_pinned_count,
    "claim_count":        _thread_claim_count,
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


def _agent_can_update_for(entity_type: str) -> list[str]:
    """Read the YAML's focus.agent_can_update for this type. Empty list
    means 'not editable via the generic surface' — caller should reject
    with a helpful message pointing at any typed alternative."""
    from core.entity_types import get_type
    spec = get_type(entity_type)
    if not spec:
        return []
    return list((spec.focus or {}).get("agent_can_update") or [])


def _agent_tools_for(entity_type: str) -> list[str]:
    """Read the YAML's creation.agent_tools — typed workflow tools
    declared as touching this type. Used by list_entity_operations to
    point the agent at the right tool when the generic primitive is
    insufficient (e.g. make_revision for a figure)."""
    from core.entity_types import get_type
    spec = get_type(entity_type)
    if not spec:
        return []
    return list((spec.creation or {}).get("agent_tools") or [])


def _resolve_view_path(path: str):
    """Resolve view_artifact's `path` arg to an EXISTING on-disk Path, or None.

    Accepts (in order): an `/artifacts/<pid>/<name>` URL (the exact handle a
    run/exec tool-result already hands back), an absolute path, or a path
    relative to the active project. Relative paths resolve against the
    project's work / artifacts / data areas — NOT the backend process cwd
    (the old `Path(path).resolve()` did the latter, so a bare plot name 404'd
    against `…/backend/`). A bare filename is matched anywhere under the
    project work tree (newest mtime wins), so a plot a kernel just wrote into
    its run subdir (`work/ana_*/foo.png`) is found without needing the run dir
    or a pre-harvested entity id. Rejects `..` traversal."""
    from pathlib import Path as _P
    if not path:
        return None
    # /artifacts/... URL → disk, via the same mapper the entity branch uses.
    if path.startswith("/artifacts/"):
        try:
            from core.web.artifacts import _artifact_url_to_path
            d = _artifact_url_to_path(path)
        except Exception:  # noqa: BLE001
            d = None
        return d if (d and d.exists()) else None
    p = _P(path).expanduser()
    if ".." in p.parts:
        return None
    if p.is_absolute():
        return p if p.exists() else None
    from core.config import project_work_dir, project_artifacts_dir, project_data_dir
    from core.projects import current_project_id
    pid = current_project_id()
    for base in (project_work_dir(pid), project_artifacts_dir(pid),
                 project_data_dir(pid)):
        cand = base / p
        if cand.exists():
            return cand
    # Bare filename → search the project work tree (kernels write into a
    # per-run subdir, so a simple join above won't catch it). Newest wins.
    if p.name == str(p):
        try:
            hits = [m for m in project_work_dir(pid).rglob(p.name) if m.is_file()]
            if hits:
                return max(hits, key=lambda m: m.stat().st_mtime)
        except OSError:
            pass
    return None


_UNIVERSAL_FALLBACK = ["title", "status", "tags", "notes", "retention_alert"]

# Top-level entity columns (vs metadata fields). The HTTP PATCH route
# (main.py: entities_patch) is the source of truth; this list mirrors
# what update_entity() accepts as direct kwargs.
_TOP_LEVEL_COLUMNS = {"title", "notes", "tags", "status"}


def _log_warn(msg: str) -> None:
    """Module-internal warn helper that doesn't crash if logging is misconfigured."""
    try:
        import logging
        logging.getLogger(__name__).warning(msg)
    except Exception:  # noqa: BLE001
        pass


def _broadcast_member_change(result_id: str, member_id: str | None,
                             reason: str, *,
                             entity_id: str | None = None) -> None:
    """Fire an entity_updated SSE so the focused Result card re-fetches.
    Best-effort — broadcast must NEVER fail the write."""
    try:
        from core.runtime import wire
        from core.runtime.notifications import broadcast
        extra: dict = {}
        if member_id:
            extra["member_id"] = member_id
        if entity_id:
            extra["attached_entity_id"] = entity_id
        broadcast(wire.entity_updated(entity_id=result_id, reason=reason, **extra))
    except Exception:  # noqa: BLE001
        pass


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

    @mcp.tool()
    def update_entity_fields(entity_id: str,
                             fields: dict,
                             aba_ctx_id: str | None = None) -> dict:
        """Update editable fields on ANY entity. The write-set comes
        from the type's YAML focus.agent_can_update slot — fields
        outside that slot are rejected with a list of what IS allowed.

        Fields map naturally to either the entity's top-level column
        (title, notes, tags, status) or its metadata (interpretation,
        statement, caveats, alternatives, text, source, organism,
        layout_hint, description, …). The primitive figures out which
        is which and dispatches accordingly.

        Common writable fields by type (see entity_types/*.yaml for
        the authoritative list):
          - result:    title, notes, tags, interpretation
          - claim:     title, notes, tags, statement, caveats, alternatives
          - finding:   title, notes, tags, text
          - dataset:   title, notes, tags, description, source, organism, layout_hint
          - figure:    title, notes, tags                     (structural changes → make_revision)
          - table:     title, notes, tags                     (structural changes → make_revision)
          - narrative: title, notes, tags, text
          - note:      title, notes, tags, text

        Result.interpretation vs per-figure caption — DO NOT CONFUSE.
        `interpretation` is the Result-level SYNTHESIS / READING /
        OVERVIEW prose that the UI renders as its own block ABOVE the
        member panels. The text directly under each figure or table
        image is a PER-MEMBER caption on members[i].caption — a
        different field, written via the separate
        `update_member_caption(result_id, member_id, caption)` tool.

        If the user says "update the caption" while a figure is the
        focused / discussed evidence, they almost always mean the
        per-figure caption — call `update_member_caption`, NOT
        update_entity_fields with `interpretation`. Live bug
        (prj_128380fd thr_deed230d, 2026-06-11): the agent picked
        `interpretation`, leaving the old per-figure caption in place
        and surfacing the new text as a separate block.

        Semantics:
          - Only keys present in `fields` are touched; everything else
            is left alone.
          - A `None` value on a metadata field DELETES that key from
            metadata (used to clear, say, an organism that was wrong).
            A `None` on a top-level column is treated as 'no change'
            (matches PATCH /api/entities/{id} convention).
          - `tags=[...]` REPLACES the whole tag list (not additive).
            Same for caveats/alternatives: whole-list replace. Use the
            typed claim endpoints for per-item caveat/alternative ops.
          - Empty-string title is rejected (entities must have a title).

        Returns:
          {"status": "ok", "entity_id": ..., "updated": [field names]}
          on success, or {"error": "..."} on entity-not-found / disallowed
          field / empty title.

        For STRUCTURAL changes (members, revisions, edges), the typed
        workflow tools still apply: add_result_member / remove_result_member,
        make_revision, add_to_dataset / remove_from_dataset,
        promote_to_result, create_finding, create_claim. Call
        list_entity_operations(type) to see them.
        """
        from core.graph.entities import get_entity, update_entity
        e = get_entity(entity_id)
        if not e:
            return {"error": f"entity {entity_id} not found"}

        if not isinstance(fields, dict) or not fields:
            return {"error": "fields must be a non-empty dict"}

        ftype = e.get("type") or "entity"
        allowed = _agent_can_update_for(ftype)
        if not allowed:
            return {"error":
                    f"type '{ftype}' is not editable via update_entity_fields "
                    f"(no agent_can_update declared in entity_types/{ftype}.yaml). "
                    f"Use a typed workflow tool — see list_entity_operations('{ftype}')."}

        rejected = [k for k in fields.keys() if k not in allowed]
        if rejected:
            return {"error":
                    f"fields not editable for type '{ftype}': {rejected}. "
                    f"Allowed: {allowed}. For structural changes (members, "
                    f"revisions, edges) use the typed workflow tools — see "
                    f"list_entity_operations('{ftype}')."}

        # Validate title non-empty if being updated. A title=None is the
        # PATCH "no change" sentinel (see Semantics in the docstring); it
        # gets skipped below alongside other top-level Nones. Only reject
        # explicit attempts to BLANK the title (empty string or whitespace).
        if "title" in fields and fields["title"] is not None:
            t = fields["title"]
            if not isinstance(t, str) or not t.strip():
                return {"error": "title cannot be empty"}

        # Split fields into top-level columns vs metadata.
        top: dict[str, Any] = {}
        meta_updates: dict[str, Any] = {}
        for k, v in fields.items():
            if k in _TOP_LEVEL_COLUMNS:
                if v is None:
                    continue   # PATCH semantics: None = no change
                top[k] = v
            else:
                meta_updates[k] = v

        if meta_updates:
            merged = dict(e.get("metadata") or {})
            for k, v in meta_updates.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v
            top["metadata"] = merged

        if not top:
            # All present keys were None top-level — nothing to do but
            # don't fail (matches a polite no-op).
            return {"status": "ok", "entity_id": entity_id, "updated": []}

        updated = update_entity(entity_id, **top)
        if not updated:
            return {"error": f"update failed for entity {entity_id}"}

        # If a Result interpretation was edited, mirror the HTTP convention:
        # don't auto-flip interpretation_origin — let the caller decide. The
        # frontend sets it explicitly; the agent's interpretation is left as
        # whatever it was (usually 'ai').

        # Notify the frontend so cards driven by this entity re-fetch.
        # Without this the data writes succeed silently and the Result
        # card / focus pane keeps showing the pre-update value (live bug
        # prj_128380fd thr_deed230d, 2026-06-11: agent updated a Result's
        # interpretation, disk had the new value, UI kept the old caption).
        # Mirrors the typed-tool broadcast pattern in
        # lifecycle/promote.py:549 and lifecycle/revisions.py:408.
        try:
            from core.runtime import wire
            from core.runtime.notifications import broadcast
            broadcast(wire.entity_updated(entity_id=entity_id,
                                          reason="agent_update"))
        except Exception:  # noqa: BLE001 — broadcast must NEVER fail the write
            pass

        return {"status": "ok", "entity_id": entity_id,
                "updated": list(fields.keys())}

    @mcp.tool()
    def update_member_caption(result_id: str, member_id: str,
                              caption: str,
                              aba_ctx_id: str | None = None) -> dict:
        """Update the caption text directly under a Result's member —
        the per-figure (or per-table) caption the user sees beneath
        each panel image. NOT the same as the Result's `interpretation`
        field, which is the result-level synthesis prose.

        USE THIS WHEN the user asks to "update / change / fix / rewrite
        the caption" on a figure or table inside a Result, or asks to
        recast the description of a single panel. The per-member
        caption renders directly under the image; updating
        `interpretation` via update_entity_fields would have left the
        old caption in place AND added the new text as a separate
        prose block (live bug prj_128380fd thr_deed230d, 2026-06-11).

        Use update_entity_fields(result_id, {interpretation: ...}) for
        the Result-level READING / OVERVIEW that spans all members —
        the high-level interpretation block, not per-panel text.

        member_id comes from read_entity(result_id) →
        fields.members_summary[i].member_id. Also accepts a
        figure/table id from anywhere in a member's revision chain —
        the tool resolves it to the right slot (see note below).

        Sets caption_origin='ai' so the ✨ indicator stays accurate
        (the user can later edit it manually, which flips the origin
        to 'user' and clears the indicator).

        Arguments:
          result_id  — the Result entity id.
          member_id  — the member id (preferred), OR any figure/table
                       id that lives in a member's revision chain. If a
                       figure id is passed and exactly one member's
                       chain contains it, that member is targeted —
                       saves a round-trip read_entity. Pass an explicit
                       member id when the same figure id appears in
                       multiple slots (rare).
          caption    — new caption text. Empty string clears.

        Returns: {"status": "ok", "result_id", "member_id",
        "resolved_via": "member_id" | "figure_chain"} on success;
        {"error": "..."} on bad inputs.
        """
        from core.graph.entities import get_entity
        from content.bio.graph.result_members import update_result_member
        r = get_entity(result_id)
        if not r:
            return {"error": f"result {result_id} not found"}
        if r.get("type") != "result":
            return {"error":
                    f"entity {result_id} is type {r.get('type')!r}, "
                    f"not 'result'. update_member_caption only "
                    f"operates on Result members."}
        members = (r.get("metadata") or {}).get("members") or []

        resolved_via = "member_id"
        if not any(m.get("id") == member_id for m in members):
            # Fallback: the agent may have passed a figure/table id
            # (any entry in a member's revision chain) instead of the
            # slot's member id. This is the same confusion shape that
            # bit prj_ab1b55fe thr_e692a202 (2026-06-11) — same figure
            # title across four revisions, the agent kept the latest
            # figure id in working memory and tried to update on it.
            # Resolve to the slot whose revision chain contains the
            # supplied id; refuse only if there's no match or it's
            # ambiguous (multiple slots share that chain entry).
            from content.bio.graph.figure_history import figure_history
            candidates: list[str] = []
            for m in members:
                ref = m.get("ref")
                if not ref:
                    continue
                try:
                    chain = figure_history(ref, include_superseded=True)
                except Exception:  # noqa: BLE001
                    chain = []
                if any(e.get("id") == member_id for e in chain):
                    candidates.append(m.get("id"))

            if len(candidates) == 1:
                member_id = candidates[0]
                resolved_via = "figure_chain"
            else:
                seen = [m.get("id") for m in members]
                if len(candidates) > 1:
                    return {"error":
                            f"id {member_id!r} matches the revision "
                            f"chain of multiple members ({candidates}). "
                            f"Pass the specific member id you mean. "
                            f"Result members: {seen}."}
                return {"error":
                        f"member {member_id!r} not in result {result_id}. "
                        f"Members: {seen}. Call read_entity to see "
                        f"members_summary."}

        if not isinstance(caption, str):
            return {"error":
                    f"caption must be a string, got "
                    f"{type(caption).__name__}"}
        out = update_result_member(result_id, member_id,
                                    caption=caption, caption_origin="ai")
        if out is None:
            return {"error":
                    f"update_result_member failed for "
                    f"{result_id}/{member_id}"}
        try:
            from core.runtime import wire
            from core.runtime.notifications import broadcast
            broadcast(wire.entity_updated(entity_id=result_id,
                                          reason="member_caption_updated",
                                          member_id=member_id))
        except Exception:  # noqa: BLE001 — broadcast must NEVER fail the write
            pass
        return {"status": "ok",
                "result_id": result_id,
                "resolved_via": resolved_via,
                "member_id": member_id}

    @mcp.tool()
    def add_result_member(result_id: str,
                          kind: str = "figure",
                          ref: str | None = None,
                          exec_id: str | None = None,
                          artifact_idx: int = 0,
                          text: str | None = None,
                          caption: str | None = None,
                          at: int | None = None,
                          aba_ctx_id: str | None = None) -> dict:
        """Append a panel to an existing Result. The primary way to add
        figures, tables, or text panels to a Result the user is curating.

        USE THIS WHEN the user asks to "add this to the Result", "put
        the new plot next to the existing one", "add a text note to
        this Result", or any similar gesture extending a Result the
        user already created. Pre-2026-06-12 the agent literally had
        no MCP tool for this — the entity_ops docstring named the
        function but it wasn't registered, so the agent's only
        affordances were promote_to_result (creates a NEW Result —
        wrong shape) or giving up. Now you can attach in one call.

        Three input shapes:

          1. EXISTING entity — pass `ref` = the entity id of the
             figure/table you want to attach.

          2. FRESH run output — pass `kind` + `exec_id` + optional
             `artifact_idx` (default 0 = the first artifact). The
             tool calls pin_artifact internally to mint the entity
             from the exec record's produced[] list (post-Phase-5
             cutover: run_python / run_r artifacts no longer
             auto-mint entities; they live in the exec record until
             explicitly pinned). The new entity is then attached.
             Result: ONE call goes from 'figure I just rendered' to
             'panel in the Result'.

          3. TEXT panel — pass `kind='text'` + `text='…'`. No
             entity ref needed.

        Arguments:
          result_id    — the Result to append to.
          kind         — 'figure' | 'table' | 'text'. Defaults to 'figure'.
          ref          — entity id of an existing figure/table to attach.
          exec_id      — exec record from a recent run_python/run_r call.
                         Used when no ref is supplied to pin-then-attach
                         in one shot.
          artifact_idx — which artifact in the exec's produced[] list
                         (default 0). For runs producing multiple plots
                         this picks the Nth one (0-based, in produced[]
                         order).
          text         — inline text for kind='text' panels.
          caption      — optional initial caption for the panel.
          at           — insert position; None = append.

        Returns:
          {"status": "ok", "result_id", "member_id",
           "entity_id": id_of_attached_entity_or_None_for_text,
           "was_new": True_if_pin_artifact_minted_a_fresh_entity}
        On failure: {"error": "..."}.
        """
        from core.graph.entities import get_entity
        from core.graph.edges import add_edge
        from content.bio.graph.result_members import add_result_member as _add
        from core.runtime.tool_ctx import peek_ctx

        ctx = peek_ctx(aba_ctx_id) or {}
        tid = ctx.get("thread_id")

        # Validate the Result up front so the error names what's wrong.
        r = get_entity(result_id)
        if not r:
            return {"error": f"result {result_id} not found"}
        if r.get("type") != "result":
            return {"error":
                    f"entity {result_id} is type {r.get('type')!r}, "
                    f"not 'result'. add_result_member only operates "
                    f"on Result entities."}

        kind = (kind or "figure").strip().lower()
        if kind not in ("figure", "table", "text"):
            return {"error":
                    f"kind must be 'figure', 'table', or 'text'; "
                    f"got {kind!r}."}

        # ── kind=text: simplest path, no entity needed ────────────────
        if kind == "text":
            if not (text or "").strip():
                return {"error":
                        "kind='text' requires non-empty `text` content."}
            out = _add(result_id, kind="text", text=text,
                        caption=caption or "", at=at)
            if not out:
                return {"error": f"add_result_member failed for {result_id}"}
            mid = ((out.get("metadata") or {}).get("members") or [])[-1].get("id")
            _broadcast_member_change(result_id, mid, "member_added")
            return {"status": "ok", "result_id": result_id,
                    "member_id": mid, "entity_id": None, "was_new": False}

        # ── kind=figure / table: need a ref. Either supplied or pinned. ─
        was_new = False
        entity_id = ref
        if not entity_id:
            if not exec_id:
                return {"error":
                        f"kind={kind!r} needs either `ref` (an existing "
                        f"entity id) or `exec_id` (to pin a loose "
                        f"artifact from a recent run_python/run_r). "
                        f"Got neither."}
            try:
                from content.bio.lifecycle.artifacts import pin_artifact
                pin = pin_artifact(exec_id, kind, int(artifact_idx),
                                    wrap_in_result=False, thread_id=tid)
                entity_id = pin.get("entity_id")
                was_new = bool(pin.get("was_new"))
            except Exception as e:  # noqa: BLE001
                return {"error":
                        f"pin_artifact({exec_id!r}, {kind!r}, "
                        f"{artifact_idx}) failed: {e}"}
            if not entity_id:
                return {"error":
                        f"pin_artifact returned no entity_id for "
                        f"{exec_id}/{kind}/{artifact_idx}"}

        # Sanity: the ref must actually exist and match the requested kind.
        ent = get_entity(entity_id)
        if not ent:
            return {"error": f"entity {entity_id} not found"}
        if ent.get("type") != kind:
            return {"error":
                    f"entity {entity_id} is type {ent.get('type')!r}, "
                    f"not the requested kind {kind!r}. Pass a matching "
                    f"ref or omit it and let exec_id+idx pick the "
                    f"right artifact."}

        out = _add(result_id, kind=kind, ref=entity_id,
                    caption=caption or "", at=at)
        if not out:
            return {"error":
                    f"add_result_member failed for {result_id}/{entity_id}"}
        # The new member is at the end (or at `at`); pull its id from the
        # updated metadata.
        members = (out.get("metadata") or {}).get("members") or []
        if at is None or at >= len(members):
            mid = members[-1].get("id") if members else None
        else:
            mid = members[max(0, at)].get("id")

        # Mirror the HTTP endpoint: add an `includes` edge so provenance
        # walkers see the membership without scanning metadata.
        try:
            add_edge(source_id=result_id, target_id=entity_id,
                     rel_type="includes",
                     attributes={"created_by": "add_result_member"})
        except Exception as e:  # noqa: BLE001
            _log_warn(f"add_result_member: edge add failed: {e}")

        _broadcast_member_change(result_id, mid, "member_added",
                                 entity_id=entity_id)
        return {"status": "ok", "result_id": result_id,
                "member_id": mid, "entity_id": entity_id,
                "was_new": was_new}

    @mcp.tool()
    def remove_result_member(result_id: str, member_id: str,
                             aba_ctx_id: str | None = None) -> dict:
        """Remove a panel from a Result without deleting the underlying
        figure / table entity.

        USE THIS WHEN the user asks to drop a panel from a Result —
        "remove the UMAP from this Result", "take out the second
        figure". Does NOT delete the figure entity; just unlinks the
        membership. To delete the figure too, use delete_revision (for
        a revision) or archive_entity (for the whole figure).

        Arguments:
          result_id  — the Result to edit.
          member_id  — the member slot to remove (from
                       read_entity → members_summary[i].member_id).

        Returns: {"status": "ok", "result_id", "removed_member_id"}
        on success; {"error": "..."} on bad inputs.
        """
        from core.graph.entities import get_entity
        from content.bio.graph.result_members import (
            remove_result_member as _remove,
        )
        r = get_entity(result_id)
        if not r:
            return {"error": f"result {result_id} not found"}
        if r.get("type") != "result":
            return {"error":
                    f"entity {result_id} is type {r.get('type')!r}, "
                    f"not 'result'."}
        members = (r.get("metadata") or {}).get("members") or []
        if not any(m.get("id") == member_id for m in members):
            seen = [m.get("id") for m in members]
            return {"error":
                    f"member {member_id!r} not in result {result_id}. "
                    f"Members: {seen}."}
        out = _remove(result_id, member_id)
        if not out:
            return {"error":
                    f"remove_result_member failed for "
                    f"{result_id}/{member_id}"}
        _broadcast_member_change(result_id, member_id, "member_removed")
        return {"status": "ok", "result_id": result_id,
                "removed_member_id": member_id}

    @mcp.tool()
    def list_entity_operations(type: str | None = None,
                               entity_id: str | None = None,
                               aba_ctx_id: str | None = None) -> dict:
        """List what the agent can do for an entity type — the generic
        write-set, the typed workflow tools, and the user gestures in
        the UI. Useful when the agent isn't sure whether the right move
        is update_entity_fields or a typed tool like make_revision.

        Call with either a `type` (e.g. 'result', 'claim') OR
        an `entity_id` (the tool will look up its type).

        Returns:
          {
            "type": "result",
            "readable":  [...agent_sees field names...],
            "writable":  [...agent_can_update field names...],
            "workflow_tools": [...creation.agent_tools entries...],
            "user_gestures":  {chat: [...], focus: [...]},
            "status_states":  [...active, archived, ...],
            "status_initial": "active",
          }

        Or {"error": "..."} if neither argument is usable.
        """
        from core.entity_types import get_type
        from core.graph.entities import get_entity

        if type:
            ftype = type
        elif entity_id:
            e = get_entity(entity_id)
            if not e:
                return {"error": f"entity {entity_id} not found"}
            ftype = e.get("type") or "entity"
        else:
            return {"error": "pass type or entity_id"}

        spec = get_type(ftype)
        if not spec:
            return {"error": f"unknown entity type '{ftype}'"}

        creation = spec.creation or {}
        return {
            "type": ftype,
            "display": spec.display,
            "readable": list((spec.focus or {}).get("agent_sees") or []),
            "writable": list((spec.focus or {}).get("agent_can_update") or []),
            "workflow_tools": list(creation.get("agent_tools") or []),
            "user_gestures": {
                "chat": list(creation.get("user_gestures_chat") or []),
                "focus": list(creation.get("user_gestures_focus") or []),
            },
            "status_states": spec.status_states(),
            "status_initial": spec.initial_status(),
        }

    @mcp.tool()
    def view_artifact(entity_id: str | None = None,
                      path: str | None = None,
                      page: int = 1,
                      aba_ctx_id: str | None = None) -> dict:
        """LOOK at an artifact — image, PDF, table, or short text doc —
        so the agent can VERIFY what's actually in it rather than
        reasoning open-loop from the source code that produced it.

        For images and PDFs the result carries the rendered image
        DIRECTLY into the next turn's context as a vision content block;
        the model SEES the figure. For tables (CSV/TSV/Parquet) and short
        text artifacts (Markdown, JSON, YAML, logs) the result carries a
        text preview (shape + head for tables; first ~3 KB for text).

        USE THIS:
          - After produce-then-verify steps where the user asked for a
            specific VISUAL change ("legend on right", "remove the grid",
            "Figure 3: in bold", "no in-plot color key"). Don't claim the
            change landed based on the code you wrote — verify by
            looking.
          - When you need to know what's actually in a file the user
            referred to (e.g. "what's in the CSV the run produced?").
          - When iterating on a composed figure / manuscript / report —
            view_artifact each revision to catch wrong layout drift
            BEFORE the user has to.

        Arguments (one of `entity_id` or `path` is required):
          entity_id — id of an entity with an artifact_path (figure,
            table, dataset, file, …). Looks up the artifact URL.
          path      — an artifact handle for files that aren't entities
            (intermediate outputs, downloads, a plot a run just wrote).
            Accepts: an `/artifacts/<pid>/<name>` URL (exactly what a
            run/exec tool-result hands back in its `plots`/`files`), an
            absolute path, or a path relative to the active project. A
            relative path resolves against the project's work / artifacts
            / data areas — NOT the backend process cwd — and a bare
            filename is matched anywhere under the project work tree
            (newest wins), so a plot a kernel just saved into its run
            subdir is found without needing its run dir or an entity id.
          page      — for multi-page PDFs: which page to rasterize
            (1-indexed; default 1). Ignored for non-PDF inputs.

        Returns by file kind:
          - PNG/JPG/GIF/WebP: vision envelope (image block + preamble).
          - PDF: rasterize requested page → vision envelope.
          - CSV/TSV/Parquet: text preview (shape, dtypes, head 20 rows).
          - Markdown/TXT/LOG/JSON/YAML/HTML: first ~3 KB as text.
          - SVG / unknown / binary: {"error": "..."} with hint.

        On error, returns {"error": "..."} (entity not found / artifact
        missing on disk / unsupported format / rasterization failure).
        """
        import base64
        from pathlib import Path as _P
        from core.graph.entities import get_entity

        # Resolve (entity_id | path) → disk path, with light metadata.
        title = ""
        ent_type: str | None = None
        ent_meta: dict = {}
        ap_url: str | None = None
        if entity_id and path:
            return {"error": "pass entity_id OR path, not both"}
        if not entity_id and not path:
            return {"error": "pass entity_id or path"}
        if entity_id:
            e = get_entity(entity_id)
            if not e:
                return {"error": f"entity {entity_id} not found"}
            ent_type = e.get("type") or "entity"
            title = (e.get("title") or "").strip()
            ent_meta = e.get("metadata") or {}
            ap_url = e.get("artifact_path") or ""
            if not ap_url:
                return {"error": f"entity {entity_id} has no artifact_path "
                                 f"(type={ent_type})"}
            try:
                from core.web.artifacts import _artifact_url_to_path
                disk = _artifact_url_to_path(ap_url) if ap_url.startswith("/artifacts/") else None
            except Exception:  # noqa: BLE001
                disk = None
            if disk is None:
                disk = _P(ap_url)
        else:
            disk = _resolve_view_path(path)
            if disk is None:
                return {"error": f"artifact not found for path {path!r} "
                                 f"(looked under the active project's "
                                 f"work/artifacts/data area; pass an "
                                 f"/artifacts/<pid>/<name> URL or an absolute "
                                 f"path for files outside it)"}

        if not disk.exists():
            return {"error": f"artifact missing on disk: {disk}"}
        if disk.is_dir():
            return {"error": f"path is a directory, not an artifact: {disk}"}

        suffix = disk.suffix.lower()

        # ── Image branch ────────────────────────────────────────────
        if suffix in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
            media_type = {".png": "image/png", ".jpg": "image/jpeg",
                          ".jpeg": "image/jpeg", ".gif": "image/gif",
                          ".webp": "image/webp"}[suffix]
            try:
                img_bytes = disk.read_bytes()
            except OSError as ex:
                return {"error": f"failed to read image bytes: {ex}"}
            return _vision_envelope(entity_id, ent_type, title, str(ap_url or disk),
                                    ent_meta, disk.name, img_bytes, media_type)

        # ── PDF branch ──────────────────────────────────────────────
        if suffix == ".pdf":
            try:
                import pypdfium2 as pdfium  # type: ignore[import-not-found]
            except ImportError:
                return {"error": "pypdfium2 not installed; cannot render PDF"}
            try:
                doc = pdfium.PdfDocument(str(disk))
                n_pages = len(doc)
                if n_pages == 0:
                    return {"error": f"PDF has 0 pages: {disk}"}
                idx = max(1, min(int(page or 1), n_pages)) - 1
                pg = doc[idx]
                page_w_pt = max(50, pg.get_width())
                scale = max(0.5, min(200 / 72, 1600 / page_w_pt))
                bitmap = pg.render(scale=scale)
                from io import BytesIO
                buf = BytesIO()
                bitmap.to_pil().save(buf, "PNG", optimize=True)
                img_bytes = buf.getvalue()
            except Exception as ex:  # noqa: BLE001
                return {"error": f"PDF rasterize failed: {ex}"}
            extra = (f" (page {idx + 1}/{n_pages})" if n_pages > 1 else "")
            return _vision_envelope(entity_id, ent_type, title, str(ap_url or disk),
                                    ent_meta, disk.name + extra, img_bytes,
                                    "image/png")

        # ── Tabular branch ──────────────────────────────────────────
        if suffix in (".csv", ".tsv", ".parquet"):
            try:
                import pandas as pd
                if suffix == ".csv":
                    df = pd.read_csv(disk, nrows=200)
                elif suffix == ".tsv":
                    df = pd.read_csv(disk, sep="\t", nrows=200)
                else:
                    df = pd.read_parquet(disk).head(200)
            except Exception as ex:  # noqa: BLE001
                return {"error": f"failed to read tabular file: {ex}"}
            with _pd_display():
                head_str = df.head(20).to_string(max_colwidth=60)
            return {"id": entity_id, "type": ent_type, "title": title,
                    "artifact_path": str(ap_url or disk),
                    "kind": "table",
                    "shape": list(df.shape),
                    "columns": [str(c) for c in df.columns][:50],
                    "dtypes": {str(c): str(df[c].dtype) for c in df.columns[:50]},
                    "head_20_rows_text": head_str}

        # ── Short text branch ───────────────────────────────────────
        if suffix in (".md", ".txt", ".log", ".json", ".yaml", ".yml", ".html", ".py", ".r"):
            try:
                raw = disk.read_bytes()
            except OSError as ex:
                return {"error": f"failed to read text bytes: {ex}"}
            text = raw[:3072].decode("utf-8", errors="replace")
            return {"id": entity_id, "type": ent_type, "title": title,
                    "artifact_path": str(ap_url or disk),
                    "kind": "text",
                    "bytes": len(raw),
                    "truncated": len(raw) > 3072,
                    "text_head": text}

        return {"error": f"don't know how to view {suffix or '<no-extension>'} — "
                         f"supported: PNG/JPG/GIF/WebP, PDF, CSV/TSV/Parquet, "
                         f"MD/TXT/LOG/JSON/YAML/HTML/PY/R"}


def _vision_envelope(entity_id, ent_type, title, ap_str, ent_meta,
                     display_name, img_bytes, media_type):
    """Build the standard vision-bearing tool_result envelope."""
    import base64
    img_b64 = base64.b64encode(img_bytes).decode("ascii")
    if entity_id:
        preamble = (f"Artifact for entity {entity_id} (type={ent_type}, "
                    f"title={title!r}). Image of {display_name} follows. "
                    f"Compare to the user's last visual request and report "
                    f"whether it matches.")
    else:
        preamble = (f"Artifact at {ap_str}. Image of {display_name} follows. "
                    f"Inspect what's actually there.")
    return {
        "id": entity_id, "type": ent_type, "title": title,
        "artifact_path": ap_str,
        "metadata_summary": {k: ent_meta.get(k) for k in
                             ("interpretation", "thread_id", "exec_id")
                             if k in ent_meta},
        "_vision_blocks": [
            {"type": "text", "text": preamble},
            {"type": "image",
             "source": {"type": "base64", "media_type": media_type, "data": img_b64}},
        ],
    }


def _pd_display():
    """Context manager: widen pandas display so head().to_string() is
    actually readable (default truncates at ~80 chars)."""
    import contextlib, pandas as pd
    @contextlib.contextmanager
    def _cm():
        with pd.option_context("display.max_columns", 50,
                               "display.width", 200,
                               "display.max_colwidth", 60):
            yield
    return _cm()
