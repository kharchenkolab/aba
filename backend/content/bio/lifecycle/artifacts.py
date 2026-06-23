"""Materialize-then-pin path for unpinned artifacts (Option B / Phase 2
of misc/exec_records_and_versioning.md).

The companion of `core/exec/artifacts.py` (which is read-only addressing).
Here we mint a real entity from an artifact when the user pins it.

Idempotency: a pin is identified by (exec_id, artifact_kind, artifact_idx).
Re-pinning the same artifact returns the entity that already materialized
the first time rather than creating a duplicate. The Stage 3 pin lifecycle
keeps unpinning reversible — repin after unpin reuses the same entity_id.

This module deliberately stays small. The wrap-in-Result step delegates
to the existing `pin_evidence` flow so all the side effects (advisor
hooks, auto-interpret daemon, etc.) keep firing the same way.
"""
from __future__ import annotations
import logging
from typing import Optional

from core.graph._schema import _conn
from core.graph.edges import add_edge
from core.graph.entities import create_entity, get_entity
from core.exec.artifacts import (
    parse_artifact_id, resolve_artifact, format_artifact_id,
)
from core.graph import exec_records

_log = logging.getLogger(__name__)


# Kinds that materialize as their own entity type. `cell` has a dedicated
# helper (`lifecycle/cells.create_cell_from_exec`) because its rendering
# rules differ; we route through it here to keep one entry point per kind.
_KIND_TO_TYPE = {
    "figure": "figure",
    "table":  "table",
    "cell":   "cell",
}


def _existing_entity_for_artifact(exec_id: str, kind: str, idx: int) -> Optional[dict]:
    """Lookup an entity already materialized from this artifact.

    The exec_id + artifact_kind + artifact_idx triple is unique per
    artifact, so at most one entity should match. If multiple rows
    match (a bug elsewhere), the newest wins."""
    with _conn() as c:
        r = c.execute(
            "SELECT id FROM entities "
            "WHERE exec_id = ? AND artifact_kind = ? AND artifact_idx = ? "
            "AND status != 'archived' "
            "ORDER BY created_at DESC LIMIT 1",
            (exec_id, kind, idx),
        ).fetchone()
    return get_entity(r["id"]) if r else None


def materialize_entity_from_artifact(
    exec_id: str,
    kind: str,
    idx: int,
    *,
    title: Optional[str] = None,
    parent_entity_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> str:
    """Create the entity that represents this artifact, if one doesn't
    already exist. Idempotent: re-materializing returns the existing id.

    The new entity's `artifact_path` is the artifact's `url`, and
    `exec_id` + `artifact_kind` + `artifact_idx` form the pointer back
    into the exec record's `produced[]` (matching the Stage 2 shape).

    Cells have their own materializer (lifecycle/cells.create_cell_from_exec)
    because the rendering pulls from stdout, not artifact_path. We
    delegate to it here.

    Raises ValueError if the artifact can't be resolved or `kind` isn't
    materializable (e.g. `kind='file'` — generic files don't have a
    dedicated entity type today).
    """
    # Idempotency check first — fast-path for repinning an unpinned thing.
    existing = _existing_entity_for_artifact(exec_id, kind, idx)
    if existing:
        return existing["id"]

    # Cell goes through its own builder (richer metadata: preview_text etc.).
    if kind == "cell":
        from content.bio.lifecycle.cells import create_cell_from_exec
        return create_cell_from_exec(
            exec_id, title=title,
            parent_entity_id=parent_entity_id, thread_id=thread_id,
        )

    if kind not in _KIND_TO_TYPE:
        raise ValueError(
            f"materialize_entity_from_artifact: kind {kind!r} is not "
            f"materializable (supported: {sorted(_KIND_TO_TYPE)})"
        )
    artifact = resolve_artifact(exec_id, kind, idx)
    if not artifact:
        raise ValueError(
            f"artifact {format_artifact_id(exec_id, kind, idx)} not found"
        )

    # Title: explicit override, then artifact's original_name, then a
    # generic fallback. Strip the bare leaf because original_name may
    # carry subdir context ('pagoda2_GSM/qc_violin.png') that's noise
    # in a title.
    rec = exec_records.get(exec_id) or {}
    tid = thread_id or rec.get("thread_id") or None
    derived_title = (title or "").strip()
    if not derived_title:
        derived_title = (artifact.get("original_name") or "").split("/")[-1]
    if not derived_title:
        derived_title = f"{kind.capitalize()} from {rec.get('tool_name') or 'tool'}"
    derived_title = derived_title[:120]

    # parent_entity_id resolution: prefer caller-supplied; else the Run
    # the exec is attributed to; else the workspace (anonymous).
    if not parent_entity_id:
        parent_entity_id = rec.get("run_id") or None

    entity_type = _KIND_TO_TYPE[kind]
    artifact_url = artifact.get("url")
    # Browser-displayable preview for non-raster canonicals (PDF today).
    # PNGs return None and we never write a preview_path — the frontend
    # falls back to artifact_path. Best-effort: a failed rasterize
    # leaves preview_path null, the panel shows a broken thumb but the
    # download still works.
    from core.exec.previews import ensure_preview
    preview_url = ensure_preview(artifact_url) if artifact_url else None
    eid = create_entity(
        entity_type=entity_type,
        title=derived_title,
        artifact_path=artifact_url,
        parent_entity_id=parent_entity_id,
        metadata={
            "thread_id": tid,
            "origin": "internal",
            "original_name": artifact.get("original_name"),
            **({"preview_path": preview_url} if preview_url else {}),
        },
        exec_id=exec_id,
        artifact_kind=kind,
        artifact_idx=idx,
    )
    # PROV-O edges: artifact wasGeneratedBy the Run that produced its exec.
    # Mirrors what registry.register_artifacts_from_tool_result writes
    # today — keeping parity so Stage 5 chevrons + advisor notes work on
    # newly-materialized entities the same way they do on pre-cutover ones.
    if rec.get("run_id"):
        try:
            add_edge(eid, rec["run_id"], "wasGeneratedBy")
        except Exception as e:  # noqa: BLE001
            _log.warning(
                "materialize %s wasGeneratedBy %s edge failed: %s",
                eid, rec["run_id"], e,
            )
    return eid


def pin_artifact(
    exec_id: str,
    kind: str,
    idx: int,
    *,
    title: Optional[str] = None,
    wrap_in_result: bool = True,
    thread_id: Optional[str] = None,
) -> dict:
    """Materialize the artifact's entity and optionally wrap it in a Result.

    Returns: {entity_id, result_id, member_id, kind, exec_id, idx,
              was_new (bool), was_new_result (bool)}.

    Two distinct newness signals:
      - `was_new`        — the figure/table entity is freshly minted (vs.
                           reusing one materialized earlier).
      - `was_new_result` — pin_evidence created a fresh wrapping Result
                           (vs. reusing one that already wraps this
                           evidence). Drives auto_interpret gating and
                           UX "pinned just now" vs "already pinned"
                           toasts. Decoupled because an entity can exist
                           without a Result (e.g., transcluded into chat
                           but never pinned) — wrapping it later still
                           creates a new Result even though was_new=False.
    """
    pre_existing = _existing_entity_for_artifact(exec_id, kind, idx)
    eid = materialize_entity_from_artifact(
        exec_id, kind, idx, title=title, thread_id=thread_id,
    )
    was_new = pre_existing is None

    out: dict = {
        "entity_id":      eid,
        "result_id":      None,
        "member_id":      None,
        "kind":           kind,
        "exec_id":        exec_id,
        "idx":            idx,
        "was_new":        was_new,
        "was_new_result": False,
    }
    if not wrap_in_result:
        return out

    # Wrap-in-Result reuses the standard evidence flow (kind-aware for
    # cells per pin_evidence's evidence_kind argument). pin_evidence is
    # idempotent — re-wrap of an already-wrapped evidence returns the
    # existing Result with created_result=False.
    from content.bio.lifecycle.promote import pin_evidence
    rec = exec_records.get(exec_id) or {}
    tid = thread_id or rec.get("thread_id") or ""
    wrap = pin_evidence(
        thread_id=tid,
        target_result_id=None,
        evidence_kind=kind if kind in ("figure", "table", "cell") else "figure",
        evidence_id=eid,
        origin="internal",
    )
    out["result_id"]      = wrap.get("result_id")
    out["member_id"]      = wrap.get("member_id")
    out["was_new_result"] = bool(wrap.get("created_result"))
    return out


def pin_artifact_by_id(artifact_id: str, **kwargs) -> dict:
    """Convenience wrapper that takes the canonical `<exec>:<kind>:<idx>`
    address string. Same return shape as `pin_artifact`."""
    exec_id, kind, idx = parse_artifact_id(artifact_id)
    return pin_artifact(exec_id, kind, idx, **kwargs)
