"""Entity CRUD over the `entities` table. Domain-neutral; types are
opaque strings. Per arch3_plan.md Pass B."""
from __future__ import annotations
import json
from typing import Optional

from core.graph._schema import _conn, _utcnow, gen_entity_id, WORKSPACE_ID
from core.graph.audit import log_event

# Infrastructure entity kinds — real entities, but not user-facing analysis
# artifacts. Hidden from the tree / entity feed / activity by default; a caller
# that explicitly passes type_filter="capability" still gets them (the catalog
# does this). Keeps the capability catalog off the project tree.
HIDDEN_TYPES = ("capability", "reference")


def create_entity(
    *,
    entity_type: str,
    title: str,
    artifact_path: Optional[str] = None,
    producing_code: Optional[str] = None,
    producing_params: Optional[dict] = None,
    parent_entity_id: Optional[str] = None,
    scenario_of: Optional[str] = None,
    metadata: Optional[dict] = None,
    entity_id: Optional[str] = None,
) -> str:
    eid = entity_id or gen_entity_id(prefix=entity_type[:3])
    now = _utcnow()
    with _conn() as c:
        c.execute(
            """INSERT INTO entities
               (id, type, title, status, artifact_path, producing_code,
                producing_params, parent_entity_id, scenario_of, metadata,
                created_at, updated_at)
               VALUES (?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                eid, entity_type, title, artifact_path, producing_code,
                json.dumps(producing_params) if producing_params else None,
                parent_entity_id, scenario_of,
                json.dumps(metadata) if metadata else None,
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
    return eid


def _row_to_entity(r) -> dict:
    # display_path may be absent on rows from older schemas — tolerate.
    try:
        dp = r["display_path"]
    except (KeyError, IndexError):
        dp = None
    return {
        "id": r["id"],
        "type": r["type"],
        "title": r["title"],
        "status": r["status"],
        "artifact_path": r["artifact_path"],
        "producing_code": r["producing_code"],
        "producing_params": json.loads(r["producing_params"]) if r["producing_params"] else None,
        "parent_entity_id": r["parent_entity_id"],
        "scenario_of": r["scenario_of"],
        "metadata": json.loads(r["metadata"]) if r["metadata"] else None,
        "tags": json.loads(r["tags"]) if r["tags"] else [],
        "notes": r["notes"],
        "pinned": bool(r["pinned"]) if r["pinned"] is not None else False,
        "display_path": dp,
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


def update_entity(entity_id: str, **fields) -> Optional[dict]:
    """Partial update. Accepted fields: title, notes, tags, pinned, status,
    metadata, artifact_path. Other keys silently ignored."""
    allowed = {"title", "notes", "tags", "pinned", "status", "metadata", "artifact_path", "display_path"}
    sets = []
    args = []
    for k, v in fields.items():
        if k not in allowed:
            continue
        if k == "tags" and isinstance(v, list):
            sets.append("tags = ?"); args.append(json.dumps(v))
        elif k == "metadata":
            sets.append("metadata = ?"); args.append(json.dumps(v) if v is not None else None)
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
    return get_entity(entity_id)


def archive_entity(entity_id: str) -> Optional[dict]:
    """Soft-delete: mark as archived and record deleted_at."""
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
    return get_entity(entity_id)


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
    return get_entity(entity_id)
