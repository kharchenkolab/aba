"""Output-cell entity lifecycle (Stage 6 of misc/exec_records_and_versioning.md).

A `cell` entity is a thin wrapper around an exec record that says "the user
wants to KEEP this view." The cell's content (stdout, stderr, produced
artifacts) lives in the exec record's JSON sidecar; the entity carries
only what's needed for navigation + preview (title, caption, the first
chunk of stdout, the exec_id pointer).

Cells are typically pinned-on-demand: the user is looking at a tool-result
chip in chat that printed a useful tabular summary and clicks "Pin this
output." That gesture calls `create_cell_from_exec(exec_id)` and then
optionally wraps the cell in a Result via the standard pin_evidence path.
"""
from __future__ import annotations
import logging
from typing import Optional

from core.graph.entities import create_entity, get_entity
from core.graph.edges import add_edge
from core.graph import exec_records

_log = logging.getLogger(__name__)

# How much of stdout to embed as a preview teaser on the cell entity's
# metadata. The full text stays in the exec record's JSON sidecar; this
# is what the rail / list shows without having to fetch the sidecar.
_PREVIEW_CHARS = 500


def _derive_title(exec_record: dict, override: Optional[str]) -> str:
    """Pick a human-readable title for a cell:
      1. caller-supplied override wins
      2. else, first non-empty line of stdout (capped to ~80 chars)
      3. else, "Output of <tool_name>" using the exec's tool_name
    """
    if override:
        return override.strip()[:120]
    stdout = (exec_record.get("stdout_tail") or "").strip()
    for line in stdout.splitlines():
        line = line.strip()
        if line:
            return line[:80]
    tool = exec_record.get("tool_name") or "tool"
    return f"Output of {tool}"


def create_cell_from_exec(
    exec_id: str,
    *,
    title: Optional[str] = None,
    parent_entity_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> str:
    """Materialize a `cell` entity that points at the exec record `exec_id`.

    The cell's content (stdout/stderr/produced) is NOT denormalized onto
    the entity — it stays in the exec record's JSON sidecar, reachable
    via `exec_records.get(exec_id)`. The entity carries a small preview
    in metadata so list/rail views don't have to hit disk.

    Edges: a `wasGeneratedBy` edge is written from the cell to the Run
    that produced its exec, if the exec is run-attributed (run_id set).

    Returns the new entity id. Raises ValueError if the exec record is
    missing or empty.
    """
    rec = exec_records.get(exec_id)
    if not rec:
        raise ValueError(f"exec record {exec_id} not found")
    # Idempotency: an exec can be the source of at most one active cell
    # entity. Repeat calls reuse the existing one rather than minting a
    # duplicate (same shape as materialize_entity_from_artifact for
    # figures/tables). Without this, every Pin click duplicates the
    # cell entity even before the Result-level dedupe in pin_evidence
    # gets a chance to fire.
    from content.bio.lifecycle.artifacts import _existing_entity_for_artifact
    existing = _existing_entity_for_artifact(exec_id, "cell", 0)
    if existing:
        return existing["id"]
    # An exec with neither stdout nor produced is uninteresting — refuse
    # to pin a blank cell rather than mint a useless entity.
    has_text = bool((rec.get("stdout_tail") or "").strip() or
                    (rec.get("stderr_tail") or "").strip())
    has_artifacts = bool(rec.get("produced"))
    if not has_text and not has_artifacts:
        raise ValueError(f"exec {exec_id} has no output to pin as a cell")

    derived_title = _derive_title(rec, title)
    tid = thread_id or rec.get("thread_id") or None
    stdout = rec.get("stdout_tail") or ""
    metadata = {
        "thread_id": tid,
        "origin": "internal",
        "preview_text": stdout[:_PREVIEW_CHARS] if stdout else "",
        "tool_name": rec.get("tool_name"),
        # `wall_time_s` etc. are reachable via the exec record; we don't
        # duplicate them here. preview_text is the only denormalization.
    }
    from core.graph.derivation import agent_actor_for_exec
    eid = create_entity(
        entity_type="cell",
        title=derived_title,
        parent_entity_id=parent_entity_id,
        metadata=metadata,
        exec_id=exec_id,
        artifact_kind="cell",
        artifact_idx=0,
        actor=agent_actor_for_exec(exec_id),   # Phase 2B: agent run that produced the cell
    )
    # Edge: cell wasGeneratedBy the Run that owns this exec. Match the
    # figure/table pattern — registry.py writes the same edge when it
    # auto-registers artifacts.
    run_id = rec.get("run_id")
    if run_id:
        try:
            add_edge(eid, run_id, "wasGeneratedBy")
        except Exception as e:  # noqa: BLE001 — the registry may not yet
            # declare cell→analysis wasGeneratedBy (we'll know from the test
            # whether we need to add it to cell.yaml allowed_edges).
            _log.warning(
                "cell %s wasGeneratedBy %s edge failed: %s "
                "(YAML allowed_edges may need extending)",
                eid, run_id, e,
            )
    return eid


def pin_cell_from_exec(
    exec_id: str,
    *,
    title: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> dict:
    """Higher-level convenience: create the cell entity AND wrap it in a
    Result via the standard pin_evidence path. The Result auto-archives
    on unpin if the user never edits it (B/#321 semantics).

    Returns: {cell_id, result_id, member_id} matching the shape of
    other pin_evidence callers.
    """
    from content.bio.lifecycle.promote import pin_evidence
    rec = exec_records.get(exec_id)
    if not rec:
        raise ValueError(f"exec record {exec_id} not found")
    tid = thread_id or rec.get("thread_id") or ""
    cell_id = create_cell_from_exec(exec_id, title=title, thread_id=tid)
    out = pin_evidence(
        thread_id=tid,
        target_result_id=None,
        evidence_kind="cell",
        evidence_id=cell_id,
        origin="internal",
    )
    return {
        "cell_id": cell_id,
        "result_id": out.get("result_id"),
        "member_id": out.get("member_id"),
    }
