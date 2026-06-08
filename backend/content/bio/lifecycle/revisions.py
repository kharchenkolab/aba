"""Figure/table revisions (Stage 5 of misc/exec_records_and_versioning.md).

Two operations:

  - make_revision(entity_id, modified_code) → run the modified code, harvest
    the result, materialize a new figure/table entity, and link it to the
    parent with a wasRevisionOf edge. Both stay pinned siblings — the
    original is NOT auto-superseded; users can navigate between them.

  - reproduce_from_exec(entity_id) → fetch the entity's exec record, run
    its code in the current kernel, return the reproduction summary
    (plots produced, env_fingerprint drift status, new exec_id). No
    entity created unless the caller separately materializes one via
    make_revision afterwards.

Both call run_python / run_r directly (NOT via execute_tool/MCP) — these
are internal lifecycle operations, not agent tool dispatches, so the
pre/post hook chain isn't appropriate. We still get exec records (Stage 1
wrote the helper inside run_python/run_r itself) and artifact harvesting.
"""
from __future__ import annotations
import logging
from typing import Literal, Optional

from core.graph._schema import _conn
from core.graph.edges import add_edge, remove_edge, edges_from, edges_to
from core.graph.entities import (
    create_entity, get_entity, update_entity, delete_entity_hard,
)
from core.graph import exec_records
from content.bio.lifecycle.scenarios import _detect_language

_log = logging.getLogger(__name__)

Language = Literal["python", "r"]


def _resolve_language(parent_entity: dict) -> Language:
    """Pick the language for re-running this entity's code.

    Prefer the exec record's `language` field (Stage 1 wrote it from
    the dispatcher); fall back to sniffing the legacy producing_code
    string. Defaults to python when neither source helps.
    """
    eid = parent_entity.get("exec_id")
    if eid:
        try:
            rec = exec_records.get(eid)
            if rec and rec.get("language") in ("python", "r"):
                return rec["language"]  # type: ignore[return-value]
        except Exception:  # noqa: BLE001
            pass
    code = exec_records.lookup_code_for_entity(parent_entity)
    return _detect_language(code) if code else "python"


def _newer_than(entity_id: str) -> list[str]:
    """Walk the wasRevisionOf graph FORWARD from `entity_id` and collect
    every entity that is "newer than" it (i.e., any descendant via
    `--wasRevisionOf-->`-incoming edges). Returns active entity ids in
    BFS order. Used to find what to mark `superseded` when a user
    revises from a non-latest revision."""
    out: list[str] = []
    seen = {entity_id}
    frontier = [entity_id]
    while frontier:
        nxt: list[str] = []
        for cur in frontier:
            with _conn() as c:
                rows = c.execute(
                    "SELECT eb.source_id FROM entity_edges eb "
                    "JOIN entities e ON e.id = eb.source_id "
                    "WHERE eb.target_id=? AND eb.rel_type='wasRevisionOf' "
                    "AND e.status='active'",
                    (cur,),
                ).fetchall()
            for r in rows:
                sid = r["source_id"]
                if sid in seen:
                    continue
                seen.add(sid)
                nxt.append(sid)
                out.append(sid)
        frontier = nxt
    return out


def make_revision(
    entity_id: str,
    modified_code: str,
    *,
    language: Optional[Language] = None,
    title: Optional[str] = None,
    thread_id: Optional[str] = None,
    supersede_newer: bool = False,
) -> dict:
    """Run `modified_code` and pin the new artifact as a wasRevisionOf the
    given parent figure/table entity. Both stay pinned siblings.

    By default (supersede_newer=False): if the parent already has a
    newer revision, the call REFUSES with ValueError. This protects
    the linear-chain invariant for callers that don't know whether
    they're revising the latest. Pass `supersede_newer=True` to
    explicitly accept that any currently-newer revisions will be
    marked status='superseded' to make the new revision the latest.
    The UI surfaces this via a confirmation dialog before passing the
    flag through.

    Returns: {
        "new_entity_id": str,
        "exec_id": str,                # exec record of the revision's run
        "wasRevisionOf": entity_id,    # parent
        "superseded": [...],           # ids marked superseded (if any)
        "produced": [...],             # artifacts from the run
    }

    Raises ValueError if the parent doesn't exist, has the wrong type,
    the modified code produced no artifacts, OR the parent has newer
    active revisions and `supersede_newer` is False.
    """
    parent = get_entity(entity_id)
    if not parent:
        raise ValueError(f"revision parent {entity_id} not found")
    if parent.get("type") not in ("figure", "table"):
        raise ValueError(
            f"can only revise figure/table entities, got {parent.get('type')}"
        )
    if not modified_code or not modified_code.strip():
        raise ValueError("modified_code is empty")

    # Linear-chain guard: refuse if there are newer revisions and the
    # caller hasn't opted in to superseding them. Returns the list so
    # the frontend can surface them in the confirmation dialog.
    newer = _newer_than(entity_id)
    if newer and not supersede_newer:
        raise ValueError(
            "cannot revise from a non-latest revision without "
            "supersede_newer=True (would create a branch; "
            f"newer entries: {newer})"
        )

    lang: Language = language or _resolve_language(parent)
    from content.bio.tools.run_exec import run_python, run_r

    # Carry forward the parent's thread so the exec lands in the same Run
    # if one is open (Stage 4 lifecycle); else thread scratch.
    parent_md = parent.get("metadata") or {}
    tid = thread_id or parent_md.get("thread_id") or ""
    tool_ctx = {"thread_id": tid} if tid else None
    runner_name = "run_r" if lang == "r" else "run_python"
    runner_fn = run_r if lang == "r" else run_python
    result = runner_fn({"code": modified_code}, ctx=tool_ctx)
    if result.get("error"):
        raise ValueError(f"revision run failed ({runner_name}): {result['error']}")

    new_exec_id = result.get("exec_id")
    # Pick the artifact matching the parent's kind. For Stage 5 the parent
    # is always figure or table; tables come from result["tables"], figures
    # from result["plots"]. (Stage 6 generalizes both via produced[].)
    kind = parent["type"]
    artifacts = result.get("plots") if kind == "figure" else result.get("tables")
    if not artifacts:
        stderr = (result.get("stderr") or "")[:300]
        raise ValueError(
            f"revision produced no {kind} artifacts"
            + (f"; stderr: {stderr}" if stderr else "")
        )

    # We materialize the FIRST artifact as the revision entity. Multi-output
    # revisions would land more entities via the normal registry path; for
    # the make_revision UX, idx=0 is the user's intent.
    art = artifacts[0]
    art_url = art.get("url")
    # Mirror materialize_entity_from_artifact: non-raster canonicals
    # (PDF today) get a derived PNG preview so the browser can render
    # the panel thumbnail faithfully from the actual artifact.
    from core.exec.previews import ensure_preview
    preview_url = ensure_preview(art_url) if art_url else None
    derived_title = (title or parent.get("title") or "Revision").strip()[:120]
    new_eid = create_entity(
        entity_type=kind,
        title=derived_title,
        artifact_path=art_url,
        parent_entity_id=parent.get("parent_entity_id"),
        metadata={
            "thread_id": tid or None,
            "origin": "internal",
            "revision_of": entity_id,
            "original_name": art.get("original_name") or art.get("name"),
            **({"preview_path": preview_url} if preview_url else {}),
        },
        exec_id=new_exec_id,
        artifact_kind=kind,
        artifact_idx=0,
    )
    # The edge that makes navigation work: new --wasRevisionOf--> old.
    # figure_history walks this in both directions.
    add_edge(new_eid, entity_id, "wasRevisionOf",
             {"created_by": "make_revision"})

    # Supersede any previously-newer revisions so the chain stays
    # linear when displayed. The status_model on figure.yaml /
    # table.yaml already declares `superseded` as a legal transition
    # from `active`. update_entity validates via core.lifecycle.
    superseded_ids: list[str] = []
    if newer:
        from core.graph.entities import update_entity as _upd_ent
        for old_id in newer:
            try:
                _upd_ent(old_id, status="superseded")
                superseded_ids.append(old_id)
            except Exception as e:  # noqa: BLE001
                _log.warning("supersede failed for %s: %s", old_id, e)

    # Broadcast entity_updated so the frontend's SSE listener triggers a
    # refresh — without this, the user has to reload the page to see the
    # new chevrons appear on the focused Result. Best-effort: a failed
    # broadcast must not roll back the revision.
    try:
        from core.runtime.notifications import broadcast
        broadcast({
            "type": "entity_updated",
            "entity_id": new_eid,
            "reason": "revision_created",
            "wasRevisionOf": entity_id,
            "superseded": superseded_ids,
        })
    except Exception:  # noqa: BLE001
        pass

    return {
        "new_entity_id": new_eid,
        "exec_id": new_exec_id,
        "superseded": superseded_ids,
        "wasRevisionOf": entity_id,
        "produced": result.get("plots") if kind == "figure" else result.get("tables"),
    }


def _active_children(entity_id: str) -> list[str]:
    """Active (non-superseded) children pointing AT `entity_id` via
    wasRevisionOf. Returned in DB insertion order — the natural
    chronological order for re-parenting decisions."""
    out: list[str] = []
    for e in edges_to(entity_id):
        if e.get("rel_type") != "wasRevisionOf":
            continue
        sid = e.get("source_id")
        child = get_entity(sid)
        if child and child.get("status") != "superseded":
            out.append(sid)
    return out


def _wasrevof_parent(entity_id: str) -> Optional[str]:
    """The entity this revision points at via wasRevisionOf, or None
    if this is the chain anchor."""
    for e in edges_from(entity_id):
        if e.get("rel_type") == "wasRevisionOf":
            return e.get("target_id")
    return None


def _result_members_referencing(entity_id: str) -> list[tuple[str, str]]:
    """Find all (result_id, member_id) pairs whose member.ref ==
    entity_id. Searches via the includes edge to short-list candidate
    Results, then walks each Result's metadata.members for the exact
    match (members can carry the same ref multiple times in pathological
    cases; we return one entry per occurrence)."""
    candidates: set[str] = set()
    for e in edges_to(entity_id):
        if e.get("rel_type") == "includes":
            src = e.get("source_id")
            if src:
                candidates.add(src)
    out: list[tuple[str, str]] = []
    for rid in candidates:
        r = get_entity(rid)
        if not r or r.get("type") != "result":
            continue
        for m in (r.get("metadata") or {}).get("members") or []:
            if m.get("ref") == entity_id:
                out.append((rid, m.get("id") or ""))
    return out


def delete_revision(entity_id: str) -> dict:
    """Hard-delete a figure/table revision while preserving chain
    integrity.

    Three things happen, in order:

    1. Re-parent: every ACTIVE wasRevisionOf child of `entity_id` is
       re-linked to `entity_id`'s wasRevisionOf parent (if any). For a
       chain v1 ← v2 ← v3, deleting v2 leaves v1 ← v3 (v3's parent
       edge is rewritten). Deleting the chain head v3 has no children
       to re-parent. Deleting the chain anchor v1 (no parent) leaves
       v2 as the new anchor with no wasRevisionOf edge.

    2. Re-anchor Result members: if a Result has a member whose
       `ref == entity_id`, the ref is updated to the new chain anchor.
       The new anchor is the wasRevisionOf parent if one exists; else
       the first active child (the next-oldest in the chain); else
       None (the member is left pointing at the deleted id and will be
       silently dropped on the next cleanup pass — but in practice the
       caller blocks this case by refusing to delete the only active
       version). The `includes` edge from Result → entity is removed
       by the hard-delete edge-cascade; a fresh edge to the new anchor
       is added.

    3. Hard-delete `entity_id` and all its incident edges (artifacts on
       disk are NOT cleaned — figures live in run output dirs we don't
       own; the exec record stays, since older revisions may still
       reference it through their own provenance).

    Refuses (raises ValueError) when `entity_id` is the only active
    entry in its chain. The UI offers "Remove from Result" for that
    case (unlinks the member; keeps the figure).

    Returns: {
        "deleted": entity_id,
        "re_parented_children": [...],     # child ids whose edges moved
        "new_parent": parent_id | None,    # where those children now point
        "re_anchored_members": [{"result_id", "member_id", "new_ref"}],
        "new_anchor": entity_id | None,
    }
    """
    ent = get_entity(entity_id)
    if not ent:
        raise ValueError(f"entity {entity_id} not found")
    if ent.get("type") not in ("figure", "table"):
        raise ValueError(
            f"delete_revision only operates on figure/table entities, "
            f"got {ent.get('type')}"
        )

    # Chain-size guard: refuse on the only active version.
    from content.bio.graph.figure_history import figure_history
    chain = figure_history(entity_id)
    if len([c for c in chain if c.get("id")]) <= 1:
        raise ValueError(
            "cannot delete the only active version in the chain — "
            "use 'Remove from Result' to unlink the member, or delete "
            "the Result itself"
        )

    parent_id = _wasrevof_parent(entity_id)
    children = _active_children(entity_id)

    # 1) Re-parent children to grandparent (or detach if no grandparent).
    re_parented: list[str] = []
    for cid in children:
        try:
            remove_edge(cid, entity_id, "wasRevisionOf")
        except Exception as e:  # noqa: BLE001
            _log.warning("re-parent: remove edge %s→%s failed: %s",
                         cid, entity_id, e)
        if parent_id:
            try:
                add_edge(cid, parent_id, "wasRevisionOf",
                         {"created_by": "delete_revision",
                          "via_deleted": entity_id})
            except Exception as e:  # noqa: BLE001
                _log.warning("re-parent: add edge %s→%s failed: %s",
                             cid, parent_id, e)
        re_parented.append(cid)

    # 2) Re-anchor any Result members whose ref points at entity_id.
    # The new anchor is the parent (if we deleted a non-anchor mid- or
    # head-revision) — but when we deleted the anchor itself, parent_id
    # is None and the natural new anchor is the first child.
    new_anchor = parent_id if parent_id else (children[0] if children else None)
    re_anchored: list[dict] = []
    for rid, mid in _result_members_referencing(entity_id):
        if not new_anchor:
            continue
        r = get_entity(rid)
        if not r:
            continue
        meta = dict(r.get("metadata") or {})
        members = list(meta.get("members") or [])
        changed = False
        for m in members:
            if m.get("ref") == entity_id:
                m["ref"] = new_anchor
                changed = True
        if changed:
            meta["members"] = members
            update_entity(rid, metadata=meta)
            # Add the includes edge to the new anchor (idempotent — the
            # old includes edge is removed by delete_entity_hard below).
            try:
                add_edge(rid, new_anchor, "includes",
                         {"created_by": "delete_revision"})
            except Exception as e:  # noqa: BLE001
                _log.warning("re-anchor: add includes %s→%s failed: %s",
                             rid, new_anchor, e)
            re_anchored.append({"result_id": rid, "member_id": mid,
                                "new_ref": new_anchor})

    # 3) Hard-delete the entity (cleans remaining incident edges via
    # FK cascade inside delete_entity_hard).
    delete_entity_hard(entity_id)

    # Broadcast so the focused Result re-fetches and the chevrons
    # rebuild against the new chain.
    try:
        from core.runtime.notifications import broadcast
        for rid, _ in _result_members_referencing(new_anchor) if new_anchor else []:
            broadcast({"type": "entity_updated",
                       "entity_id": rid,
                       "reason": "revision_deleted",
                       "deleted_revision": entity_id})
        broadcast({"type": "entity_updated",
                   "entity_id": new_anchor or entity_id,
                   "reason": "revision_deleted",
                   "deleted_revision": entity_id,
                   "re_parented_children": re_parented,
                   "re_anchored_members": re_anchored})
    except Exception:  # noqa: BLE001
        pass

    return {
        "deleted": entity_id,
        "re_parented_children": re_parented,
        "new_parent": parent_id,
        "re_anchored_members": re_anchored,
        "new_anchor": new_anchor,
    }


def reproduce_from_exec(entity_id: str, *,
                         thread_id: Optional[str] = None) -> dict:
    """Re-run the exec that produced `entity_id` and report the result.

    Used by the "reproduce earlier figure" UX. Does NOT create a new
    entity — the caller may follow up with make_revision if they want
    to pin the reproduction as a revision.

    Returns: {
        "reproduced": True if the run succeeded,
        "new_exec_id": str | None,         # the new exec record
        "env_drift": True if env_fingerprint differs from the original,
        "original_fingerprint": str,
        "new_fingerprint": str,
        "produced": [...],                 # artifacts from the re-run
        "warnings": [...],                  # human-readable notes
        "error": str | None,
    }

    Raises ValueError if the entity has no exec record to reproduce from.
    """
    ent = get_entity(entity_id)
    if not ent:
        raise ValueError(f"entity {entity_id} not found")
    eid = ent.get("exec_id")
    code = exec_records.lookup_code_for_entity(ent)
    if not eid and not code:
        raise ValueError(f"entity {entity_id} has no recoverable code")
    rec = exec_records.get(eid) if eid else None
    if not code:
        code = (rec or {}).get("code") or ""
    if not code.strip():
        raise ValueError(f"entity {entity_id} exec record has no code")

    lang: Language = _resolve_language(ent)
    from content.bio.tools.run_exec import run_python, run_r
    tid = thread_id or (ent.get("metadata") or {}).get("thread_id") or ""
    tool_ctx = {"thread_id": tid} if tid else None
    runner_fn = run_r if lang == "r" else run_python
    result = runner_fn({"code": code}, ctx=tool_ctx)
    err = result.get("error")
    new_exec_id = result.get("exec_id")

    orig_fp = (rec or {}).get("env_fingerprint")
    new_fp = None
    drift = False
    if new_exec_id:
        try:
            new_rec = exec_records.get(new_exec_id)
            new_fp = (new_rec or {}).get("env_fingerprint")
        except Exception:  # noqa: BLE001
            pass
    if orig_fp and new_fp and orig_fp != new_fp:
        drift = True
    warnings: list[str] = []
    if drift:
        warnings.append(
            "env_fingerprint changed since the original run — "
            "reproduction may differ in numeric details."
        )

    return {
        "reproduced": (err is None),
        "new_exec_id": new_exec_id,
        "env_drift": drift,
        "original_fingerprint": orig_fp,
        "new_fingerprint": new_fp,
        "produced": result.get("plots") or result.get("tables") or [],
        "warnings": warnings,
        "error": err,
    }
