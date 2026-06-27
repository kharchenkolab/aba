"""Entity CRUD over the `entities` table. Domain-neutral; types are
opaque strings. Per arch3_plan.md Pass B."""
from __future__ import annotations
import json
import logging
from typing import Optional

from core.graph._schema import _conn, _utcnow, gen_entity_id, WORKSPACE_ID
from core.graph.audit import log_event

_log = logging.getLogger(__name__)

# Infrastructure entity kinds — real entities, but not user-facing analysis
# artifacts. Hidden from the tree / entity feed / activity by default; a caller
# that explicitly passes type_filter="capability" still gets them (the catalog
# does this). Keeps the capability catalog off the project tree.
HIDDEN_TYPES = ("capability", "reference")


_warned_unbound = False


def _warn_if_unbound(entity_type: str) -> None:
    """Follow-on 3: surface a likely misroute — an entity created with NO bound
    project lands in the `_workspace` fallback. Once per process, non-breaking
    (the real gate is the access-gate CI invariant / require_project). No-op in
    SINGLE mode, where projects.current() is never None."""
    global _warned_unbound
    if _warned_unbound:
        return
    try:
        from core import projects
        if projects.current() is None:
            _warned_unbound = True
            import logging
            logging.getLogger(__name__).warning(
                "create_entity(%s) with no bound project — writing to the _workspace "
                "fallback; a caller likely forgot to bind a project (access-gate).",
                entity_type)
    except Exception:  # noqa: BLE001
        pass


def create_entity(
    *,
    entity_type: str,
    title: str,
    artifact_path: Optional[str] = None,
    producing_params: Optional[dict] = None,
    parent_entity_id: Optional[str] = None,
    scenario_of: Optional[str] = None,
    metadata: Optional[dict] = None,
    entity_id: Optional[str] = None,
    # Post Cutover 4 (misc/exec_records_and_versioning.md): pointer to
    # the exec_record that produced this entity, addressed as
    # <exec_id>:<artifact_kind>:<artifact_idx>. Optional — set by
    # paths that materialize entities from a tool-call harvest; entities
    # that don't come from an exec (containers like result/finding/etc)
    # leave these None.
    exec_id: Optional[str] = None,
    artifact_kind: Optional[str] = None,
    artifact_idx: Optional[int] = None,
    # Phase 2 (modularity_audit2 §Phase 2): typed provenance. `derivation` is a
    # core.graph.derivation constructor result (exec/derived_from/imported/manual/
    # legacy) as a dict; `actor` is agent:<run_id> | human:<uid> | system | legacy.
    # Optional during the migration window (2A); enforced at the seam in 2C.
    derivation: Optional[dict] = None,
    actor: Optional[str] = None,
) -> str:
    # WU-2 (post-Phase-4.5): schema validation is now HARD-REJECT, not
    # warning-only. p10 confirmed every add_edge call site is declared
    # in the YAMLs, and the one schema-violating create_entity call
    # site (run_register_dataset for by-reference datasets) was fixed
    # alongside this flip. New violations raise ValueError — the bio
    # router converts that to a 422 at the boundary; lifecycle code
    # surfaces it directly. Unknown types still pass through (legacy
    # data, synthetic test types).
    try:
        from core.entity_types import check_create_fields
        warnings = check_create_fields(entity_type, {
            "title": title,
            "artifact_path": artifact_path,
            "producing_params": producing_params,
            "parent_entity_id": parent_entity_id,
            "scenario_of": scenario_of,
            "metadata": metadata,
        })
    except Exception:  # noqa: BLE001 — registry import failure ≠ data violation
        warnings = []
    if warnings:
        raise ValueError("entity_types: " + "; ".join(warnings))
    eid = entity_id or gen_entity_id(prefix=entity_type[:3])
    # Honor the type's declared initial status (status_model.initial) instead of
    # hardcoding 'active' — e.g. thread->'open', claim->'preliminary'.
    try:
        from core.entity_types.registry import get_type
        _spec = get_type(entity_type)
        init_status = _spec.initial_status() if _spec else "active"
    except Exception:  # noqa: BLE001
        init_status = "active"
    # Phase 2: auto-derive `exec` when an exec_id is supplied, so every exec-born
    # path (figures/tables/cells/revisions/materialize) gets its derivation for
    # free; container/import callers pass derived_from/imported/manual explicitly.
    if derivation is None and exec_id:
        from core.graph.derivation import exec_derivation
        derivation = exec_derivation(exec_id)
    # Phase 2B: default the actor from the ambient context (set at the HTTP /
    # turn boundary) when the caller doesn't pass one explicitly.
    if actor is None:
        from core.runtime.actor import current_actor
        actor = current_actor()
    _warn_if_unbound(entity_type)
    now = _utcnow()
    with _conn() as c:
        c.execute(
            """INSERT INTO entities
               (id, type, title, status, artifact_path,
                producing_params, parent_entity_id, scenario_of, metadata,
                exec_id, artifact_kind, artifact_idx,
                derivation, actor,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, entity_type, title, init_status, artifact_path,
                json.dumps(producing_params) if producing_params else None,
                parent_entity_id, scenario_of,
                json.dumps(metadata) if metadata else None,
                exec_id, artifact_kind, artifact_idx,
                json.dumps(derivation) if derivation else None, actor,
                now, now,
            ),
        )
        c.commit()
    # Log meaningful entity creations. The exclusion list happens to be bio-
    # shaped (workspace + analysis run); generalizing it is part of Pass D
    # (event-source policy). For now it's a small, harmless coupling.
    if entity_type not in ("workspace", "analysis", *HIDDEN_TYPES):  # noqa: seam
        kind = "scenario_created" if scenario_of else "entity_created"
        log_event(kind, entity_id=eid, title=title, detail={"type": entity_type})
    _emit_upsert(eid, {
        "id": eid, "type": entity_type, "title": title, "status": init_status,
        "artifact_path": artifact_path,
        "producing_params": producing_params,
        "parent_entity_id": parent_entity_id,
        "scenario_of": scenario_of,
        "metadata": metadata,
        "exec_id": exec_id,
        "artifact_kind": artifact_kind,
        "artifact_idx": artifact_idx,
        "derivation": derivation,
        "actor": actor,
        "created_at": now, "updated_at": now,
    })
    return eid


def _row_to_entity(r) -> dict:
    # display_path / exec_id / artifact_* may be absent on rows from older
    # schemas — tolerate during the migration window.
    def _opt(name):
        try:
            return r[name]
        except (KeyError, IndexError):
            return None
    return {
        "id": r["id"],
        "type": r["type"],
        "title": r["title"],
        "status": r["status"],
        "artifact_path": r["artifact_path"],
        "producing_params": json.loads(r["producing_params"]) if r["producing_params"] else None,
        "parent_entity_id": r["parent_entity_id"],
        "scenario_of": r["scenario_of"],
        "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
        "tags": json.loads(r["tags"]) if r["tags"] else [],
        "notes": r["notes"],
        "pinned": bool(r["pinned"]) if r["pinned"] is not None else False,
        "display_path": _opt("display_path"),
        "exec_id": _opt("exec_id"),
        "artifact_kind": _opt("artifact_kind"),
        "artifact_idx": _opt("artifact_idx"),
        "derivation": json.loads(_opt("derivation")) if _opt("derivation") else None,
        "actor": _opt("actor"),
        "deleted_at": r["deleted_at"],
        "created_at": r["created_at"],
        "updated_at": r["updated_at"],
    }


def get_entity(entity_id: str) -> Optional[dict]:
    with _conn() as c:
        r = c.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return _row_to_entity(r) if r else None


def list_entities(
    *,
    exclude_workspace: bool = False,
    include_archived: bool = True,
    type_filter: Optional[str] = None,
    title_query: Optional[str] = None,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[dict]:
    q = "SELECT * FROM entities WHERE 1=1"
    args: list = []
    if exclude_workspace:
        q += " AND id != 'workspace'"
    if not include_archived:
        q += " AND status != 'archived'"
    if type_filter:
        q += " AND type = ?"
        args.append(type_filter)
    elif HIDDEN_TYPES:
        # Default: hide infrastructure kinds (capability/reference) from the
        # tree + entity feed. An explicit type_filter overrides this.
        q += " AND type NOT IN (%s)" % ",".join("?" * len(HIDDEN_TYPES))
        args.extend(HIDDEN_TYPES)
    if title_query:
        q += " AND lower(title) LIKE ?"
        args.append(f"%{title_query.lower()}%")
    q += " ORDER BY pinned DESC, created_at"
    if limit is not None:
        q += " LIMIT ? OFFSET ?"
        args.append(int(limit)); args.append(int(offset))
    with _conn() as c:
        return [_row_to_entity(r) for r in c.execute(q, args).fetchall()]


def count_entities(
    *,
    include_archived: bool = True,
    type_filter: Optional[str] = None,
    title_query: Optional[str] = None,
) -> int:
    q = "SELECT COUNT(*) AS n FROM entities WHERE id != 'workspace'"
    args: list = []
    if not include_archived:
        q += " AND status != 'archived'"
    if type_filter:
        q += " AND type = ?"; args.append(type_filter)
    elif HIDDEN_TYPES:
        q += " AND type NOT IN (%s)" % ",".join("?" * len(HIDDEN_TYPES))
        args.extend(HIDDEN_TYPES)
    if title_query:
        q += " AND lower(title) LIKE ?"; args.append(f"%{title_query.lower()}%")
    with _conn() as c:
        return c.execute(q, args).fetchone()["n"]


# --- Typed read API (modularity_audit2 §Phase 3.1) -------------------------
# So callers query the store by PREDICATE instead of reaching for raw `_conn` +
# SQL. `_conn` is forbidden outside `core/graph/` (tests/check_store_port.py).
_ORDER_COLS = {"created_at": "created_at", "updated_at": "updated_at",
               "pinned": "pinned DESC, created_at"}


def find_entities(
    *,
    type: Optional[str] = None,                 # noqa: A002 — public predicate name
    type_in: Optional[list] = None,
    status: Optional[str] = None,
    status_not: Optional[str] = None,
    include_archived: bool = True,
    not_deleted: bool = False,
    parent_entity_id: Optional[str] = None,
    scenario_of: Optional[str] = None,
    exec_id: Optional[str] = None,
    artifact_kind: Optional[str] = None,
    artifact_idx: Optional[int] = None,
    title: Optional[str] = None,
    title_query: Optional[str] = None,
    text_query: Optional[str] = None,
    metadata_contains: Optional[dict] = None,
    exclude_workspace: bool = False,
    order_by: str = "created_at",
    descending: bool = False,
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[dict]:
    """Find entities by predicate. The store's typed read surface — callers use
    this (or get_entity / count_entities) instead of raw SQL on the entities table.
    `metadata_contains` ANDs JSON1 key==value checks; `text_query` matches title OR
    notes; `order_by` is one of created_at|updated_at|pinned."""
    q = "SELECT * FROM entities WHERE 1=1"
    args: list = []
    if exclude_workspace:
        q += " AND id != 'workspace'"
    if type is not None:
        q += " AND type = ?"; args.append(type)
    if type_in is not None:
        ts = list(type_in)
        q += " AND type IN (%s)" % ",".join("?" * len(ts)); args.extend(ts)
    if status is not None:
        q += " AND status = ?"; args.append(status)
    if status_not is not None:
        q += " AND status != ?"; args.append(status_not)
    if not include_archived:
        q += " AND status != 'archived'"
    if not_deleted:
        q += " AND deleted_at IS NULL"
    if parent_entity_id is not None:
        q += " AND parent_entity_id = ?"; args.append(parent_entity_id)
    if scenario_of is not None:
        q += " AND scenario_of = ?"; args.append(scenario_of)
    if exec_id is not None:
        q += " AND exec_id = ?"; args.append(exec_id)
    if artifact_kind is not None:
        q += " AND artifact_kind = ?"; args.append(artifact_kind)
    if artifact_idx is not None:
        q += " AND artifact_idx = ?"; args.append(artifact_idx)
    if title is not None:
        q += " AND title = ?"; args.append(title)
    if title_query:
        q += " AND lower(title) LIKE ?"; args.append(f"%{title_query.lower()}%")
    if text_query:
        q += " AND (lower(title) LIKE ? OR lower(COALESCE(notes,'')) LIKE ?)"
        p = f"%{text_query.lower()}%"; args.extend([p, p])
    if metadata_contains:
        for k, v in metadata_contains.items():
            q += " AND json_extract(metadata, ?) = ?"; args.extend([f"$.{k}", v])
    col = _ORDER_COLS.get(order_by, "created_at")
    q += f" ORDER BY {col}{' DESC' if descending and order_by != 'pinned' else ''}"
    if limit is not None:
        q += " LIMIT ? OFFSET ?"; args.extend([int(limit), int(offset)])
    with _conn() as c:
        return [_row_to_entity(r) for r in c.execute(q, args).fetchall()]


def exists_entity(**predicates) -> bool:
    """True iff at least one entity matches the predicates (limit-1 find)."""
    return bool(find_entities(limit=1, **predicates))


def update_entity(entity_id: str, **fields) -> Optional[dict]:
    """Partial update. Accepted fields: title, notes, tags, pinned, status,
    metadata, artifact_path. Other keys silently ignored."""
    # Phase 4.4 — validate status transitions against the registered
    # status_model. Warning-only; the update proceeds either way.
    if "status" in fields and fields["status"] is not None:
        try:
            from core.lifecycle import validate_transition
            current = get_entity(entity_id)
            if current is not None:
                msgs = validate_transition(
                    entity_type=current["type"],
                    from_status=current.get("status"),
                    to_status=fields["status"],
                )
        except Exception:  # noqa: BLE001 — registry import failure ≠ data violation
            msgs = []
        if msgs:
            # WU-2: hard-reject — same flip as schema + edge validators.
            # An undeclared transition either reveals a YAML gap (add the
            # transition) or a buggy update (surface for the caller).
            raise ValueError("entity_types: " + "; ".join(msgs))
    allowed = {"title", "notes", "tags", "pinned", "status", "metadata", "artifact_path",
               "display_path", "producing_params",
               "exec_id", "artifact_kind", "artifact_idx"}
    sets = []
    args = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "tags" and isinstance(v, list):
            sets.append("tags = ?"); args.append(json.dumps(v))
        elif k in ("metadata", "producing_params"):
            sets.append(f"{k} = ?"); args.append(json.dumps(v) if v is not None else None)
        elif k == "pinned":
            sets.append("pinned = ?"); args.append(1 if v else 0)
        else:
            sets.append(f"{k} = ?"); args.append(v)
    if not sets:
        return get_entity(entity_id)
    sets.append("updated_at = ?"); args.append(_utcnow())
    args.append(entity_id)
    with _conn() as c:
        cur = c.execute(f"UPDATE entities SET {', '.join(sets)} WHERE id = ?", args)
        c.commit()
        if cur.rowcount == 0:
            return None
    row = get_entity(entity_id)
    if row:
        _emit_upsert(entity_id, row)
    return row


def archive_entity(entity_id: str) -> Optional[dict]:
    """Soft-delete: mark as archived and record deleted_at."""
    # WU-2: hard-reject if 'archived' isn't a declared state for this
    # type or the current → archived transition isn't declared.
    try:
        from core.lifecycle import validate_transition
        current = get_entity(entity_id)
        msgs = []
        if current is not None:
            msgs = validate_transition(
                entity_type=current["type"],
                from_status=current.get("status"),
                to_status="archived",
            )
    except Exception:  # noqa: BLE001 — registry import failure ≠ data violation
        msgs = []
    if msgs:
        raise ValueError("entity_types: " + "; ".join(msgs))
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "UPDATE entities SET status='archived', deleted_at=?, updated_at=? "
            "WHERE id = ? AND id != 'workspace'",
            (now, now, entity_id),
        )
        c.commit()
        if cur.rowcount == 0:
            return None
    row = get_entity(entity_id)
    if row:
        _emit_upsert(entity_id, row)
    return row


def delete_entity_hard(entity_id: str) -> bool:
    """Hard-delete: remove the row + its edges. Caller is responsible for
    removing any on-disk artifact and for refusing if external references
    exist. Workspace cannot be deleted. Returns True if a row was removed."""
    if entity_id == WORKSPACE_ID:
        return False
    with _conn() as c:
        c.execute("DELETE FROM entity_edges WHERE source_id = ? OR target_id = ?",
                  (entity_id, entity_id))
        cur = c.execute("DELETE FROM entities WHERE id = ?", (entity_id,))
        c.commit()
        ok = cur.rowcount > 0
    if ok:
        _emit_delete(entity_id)
    return ok


def restore_entity(entity_id: str) -> Optional[dict]:
    now = _utcnow()
    with _conn() as c:
        cur = c.execute(
            "UPDATE entities SET status='active', deleted_at=NULL, updated_at=? WHERE id = ?",
            (now, entity_id),
        )
        c.commit()
        if cur.rowcount == 0:
            return None
    row = get_entity(entity_id)
    if row:
        _emit_upsert(entity_id, row)
    return row


# ─── Recovery archive emit ────────────────────────────────────────────────
# Best-effort mirror of each entity mutation to the FS recovery archive
# (misc/recovery.md). Failures here are swallowed: the DB write succeeded,
# so the project is fine; we'll re-mirror on the next mutation. The drift
# detector (P5) catches any sustained gap.

def _emit_upsert(eid: str, row: dict) -> None:
    try:
        from core.recovery import get_scribe, EntityUpserted  # noqa: PLC0415
        from core.config import current_project_id            # noqa: PLC0415
        get_scribe().enqueue(EntityUpserted(pid=current_project_id(), entity_id=eid, row=row))
    except Exception:
        _log.debug("scribe emit_upsert failed (eid=%s)", eid, exc_info=True)


def _emit_delete(eid: str) -> None:
    try:
        from core.recovery import get_scribe, EntityHardDeleted  # noqa: PLC0415
        from core.config import current_project_id               # noqa: PLC0415
        get_scribe().enqueue(EntityHardDeleted(pid=current_project_id(), entity_id=eid))
    except Exception:
        _log.debug("scribe emit_delete failed (eid=%s)", eid, exc_info=True)
