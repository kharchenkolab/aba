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
from core.graph.edges import add_edge
from core.graph.entities import create_entity, get_entity
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
