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


_UNIVERSAL_FALLBACK = ["title", "status", "tags", "notes"]

# Top-level entity columns (vs metadata fields). The HTTP PATCH route
# (main.py: entities_patch) is the source of truth; this list mirrors
# what update_entity() accepts as direct kwargs.
_TOP_LEVEL_COLUMNS = {"title", "notes", "tags", "status"}


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

        # Validate title non-empty if being updated.
        if "title" in fields:
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

        return {"status": "ok", "entity_id": entity_id,
                "updated": list(fields.keys())}

    @mcp.tool()
    def list_entity_operations(entity_type: str | None = None,
                               entity_id: str | None = None,
                               aba_ctx_id: str | None = None) -> dict:
        """List what the agent can do for an entity type — the generic
        write-set, the typed workflow tools, and the user gestures in
        the UI. Useful when the agent isn't sure whether the right move
        is update_entity_fields or a typed tool like make_revision.

        Call with either an `entity_type` (e.g. 'result', 'claim') OR
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

        if entity_type:
            ftype = entity_type
        elif entity_id:
            e = get_entity(entity_id)
            if not e:
                return {"error": f"entity {entity_id} not found"}
            ftype = e.get("type") or "entity"
        else:
            return {"error": "pass entity_type or entity_id"}

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
          path      — explicit filesystem path (absolute or relative to
            the project's artifacts/work area). Use for files that
            aren't entities (intermediate outputs, downloads, …).
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
                from main import _artifact_url_to_path
                disk = _artifact_url_to_path(ap_url) if ap_url.startswith("/artifacts/") else None
            except Exception:  # noqa: BLE001
                disk = None
            if disk is None:
                disk = _P(ap_url)
        else:
            disk = _P(path).expanduser().resolve()

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
