"""Artifact addressing layer for Option B of misc/exec_records_and_versioning.md.

An *artifact* is a thing an exec record's `produced[]` list describes:
a figure (PNG/JPG/SVG/PDF), a table (CSV/TSV), or a file (anything else).
It has no entity row of its own — entities are minted only when the user
pins an artifact (or when a plan's declared final outputs auto-pin).

Until then, artifacts are addressed by the canonical id
``<exec_id>:<kind>:<idx>`` — the index points into the exec record's
`produced[]` list, the kind dispatches the viewer, and the exec_id
locates the JSON sidecar.

This module is read-only: it resolves artifact ids back to their
underlying data (url, original_name, kind, exec_id, idx). Pinning lives
in `content/bio/lifecycle/artifacts.py` (Phase 2).
"""
from __future__ import annotations
import logging
from typing import Optional

from core.graph import exec_records

_log = logging.getLogger(__name__)


# ── ID parsing / formatting ──────────────────────────────────────────────────

_SEPARATOR = ":"


def format_artifact_id(exec_id: str, kind: str, idx: int) -> str:
    """Compose an artifact id. The kind is normalized to lowercase so
    case differences in the producer don't shatter the address space."""
    if not exec_id or not kind:
        raise ValueError("format_artifact_id: exec_id and kind required")
    if idx < 0:
        raise ValueError("format_artifact_id: idx must be >= 0")
    return f"{exec_id}{_SEPARATOR}{kind.lower()}{_SEPARATOR}{idx}"


def parse_artifact_id(artifact_id: str) -> tuple[str, str, int]:
    """Inverse of format_artifact_id. Raises ValueError on malformed input.

    Tolerates exec_ids that themselves contain underscores or hex hashes —
    splits from the RIGHT so multi-colon exec_ids would parse correctly,
    though `gen_exec_id` doesn't currently produce any.
    """
    if not artifact_id or _SEPARATOR not in artifact_id:
        raise ValueError(f"parse_artifact_id: malformed id {artifact_id!r}")
    parts = artifact_id.rsplit(_SEPARATOR, 2)
    if len(parts) != 3:
        raise ValueError(f"parse_artifact_id: malformed id {artifact_id!r}")
    exec_id, kind, idx_s = parts
    try:
        idx = int(idx_s)
    except ValueError as e:
        raise ValueError(f"parse_artifact_id: bad idx in {artifact_id!r}: {e}")
    if not exec_id or not kind or idx < 0:
        raise ValueError(f"parse_artifact_id: malformed id {artifact_id!r}")
    return exec_id, kind.lower(), idx


# ── Resolution ───────────────────────────────────────────────────────────────


def _normalize(produced: dict, exec_id: str, idx: int) -> dict:
    """Project a raw produced[] entry into the public artifact shape."""
    kind = (produced.get("kind") or "file").lower()
    return {
        "artifact_id": format_artifact_id(exec_id, kind, idx),
        "exec_id":     exec_id,
        "kind":        kind,
        "idx":         idx,
        "url":         produced.get("url"),
        # Both keys seen in producers — run_exec writes `name`; some older
        # producers used `original_name`. Coerce here so consumers don't
        # have to know.
        "original_name": (produced.get("original_name")
                          or produced.get("name")
                          or ""),
        "sha256":      produced.get("sha256"),
        "size":        produced.get("size"),
    }


def resolve_artifact(exec_id: str, kind: str, idx: int) -> Optional[dict]:
    """Look up a single artifact by (exec_id, kind, idx).

    Returns the normalized artifact dict, or None if the exec record is
    missing, the artifact list is shorter than `idx`, or the entry at
    `idx` doesn't match `kind` (we treat that as a no-match rather than
    silently returning a different-kinded artifact)."""
    rec = exec_records.get(exec_id)
    if not rec:
        return None
    produced = rec.get("produced") or []
    if not isinstance(produced, list) or idx < 0 or idx >= len(produced):
        return None
    entry = produced[idx]
    if not isinstance(entry, dict):
        return None
    if (entry.get("kind") or "").lower() != kind.lower():
        return None
    return _normalize(entry, exec_id, idx)


def list_artifacts(exec_id: str, *, kind: Optional[str] = None) -> list[dict]:
    """All artifacts an exec record produced, optionally filtered by `kind`."""
    rec = exec_records.get(exec_id)
    if not rec:
        return []
    produced = rec.get("produced") or []
    out: list[dict] = []
    for i, entry in enumerate(produced):
        if not isinstance(entry, dict):
            continue
        if kind and (entry.get("kind") or "").lower() != kind.lower():
            continue
        out.append(_normalize(entry, exec_id, i))
    return out


def find_by_produced_name(name: str, *, limit_execs: int = 400) -> list[dict]:
    """Artifacts whose `original_name` matches `name` — newest exec first.

    Matches on the recorded name OR its basename, because `original_name` keeps
    the producing subdir ('run_x/qc.png') while a caller almost always holds the
    bare leaf ('qc.png').

    This is the index that makes a NAME resolvable at all. The served copy is
    written under a generated id (harvest, run.py `_copy_and_record`) and the
    file that still carries the human name lives in the execution sandbox, which
    is not on the controller's filesystem — so name-based lookup that only globs
    the disk finds nothing, for files the system is actively serving. Found live
    2026-07-21: three `view_artifact` calls on names the agent itself had just
    written all returned "artifact not found".
    """
    leaf = (name or "").rsplit("/", 1)[-1].strip()
    if not leaf:
        return []
    out: list[dict] = []
    try:
        exec_ids = exec_records.list_recent_exec_ids(limit_execs)
    except Exception as e:  # noqa: BLE001 — lookup must never raise at a call site
        _log.warning("find_by_produced_name: index read failed: %s", e)
        return []
    for ex_id in exec_ids:
        for a in list_artifacts(ex_id):
            on = (a.get("original_name") or "")
            if on == name or on.rsplit("/", 1)[-1] == leaf:
                out.append(a)
    return out


def artifacts_for_run(run_id: str, *,
                      kind: Optional[str] = None) -> list[dict]:
    """All artifacts produced by every exec attributed to this Run.

    Each artifact carries its full address (`artifact_id`, `exec_id`,
    `kind`, `idx`) plus `url`, `original_name`, `sha256`, `size`.
    Ordered by the exec's started_at — so the chat-history reading
    order is preserved.
    """
    if not run_id:
        return []
    out: list[dict] = []
    for rec_index in exec_records.list_by_run(run_id):
        ex_id = rec_index["exec_id"]
        # We already have the index entry; calling list_artifacts re-reads
        # the JSON sidecar. That's a per-exec disk read; the manifest call
        # site batches via this loop so the total is `O(#execs)` per Run.
        # If hot-path latency matters, an in-memory cache keyed by
        # (run_id, last_updated_at) is a Phase-7+ optimization.
        for a in list_artifacts(ex_id, kind=kind):
            out.append(a)
    return out
