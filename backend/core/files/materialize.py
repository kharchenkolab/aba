"""Materialize the virtual file tree to disk (files.md §8).

Walks every artifact-bearing entity in the active project, computes
display_path via the registered layout computers, and lays out
projects/<pid>/files/ as a mirror — symlinks to the canonical
artifacts/{uuid} files by default, copies as the fallback on
filesystems that don't support symlinks.

Domain-neutral: this module dispatches to the registered layout
computers but does not import bio types directly.
"""
from __future__ import annotations
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

from core.config import ARTIFACTS_DIR, BASE_DIR
from core.graph import _schema as _schema_mod
from core.graph.entities import list_entities


def materialize_tree(
    out_dir: Path,
    *,
    include_archived: bool = False,
    clean: bool = False,
) -> dict:
    """Build out_dir as a mirror of the virtual file tree.

    `clean=True` removes everything under out_dir first (use for a fresh
    re-materialize). Otherwise stale links are pruned but new files are
    just added.

    Returns a summary: counts of linked vs copied vs skipped, total
    size, output path. Never raises on per-file failures — they're
    collected as warnings.
    """
    from core.files.registry import display_path_for
    summary: dict = {
        "out_dir": str(out_dir),
        "linked": 0, "copied": 0, "skipped": 0, "missing": 0,
        "warnings": [], "items": 0,
    }
    if clean and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    seen: set[Path] = set()
    for e in list_entities(include_archived=include_archived):
        if e["type"] == "workspace":
            continue
        rel = display_path_for(e)
        if rel.endswith("/"):
            # Directory entity (e.g., a result). Create as an empty
            # directory; members are projected as their own entries.
            (out_dir / rel.rstrip("/")).mkdir(parents=True, exist_ok=True)
            continue
        artifact = e.get("artifact_path")
        if not artifact:
            # Text-only entity (claim, narrative, note) — write a
            # synthesized .md so the tree carries the content.
            target = out_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(_text_for(e))
            seen.add(target)
            summary["copied"] += 1
            continue
        # Resolve canonical disk path from artifact_path.
        src = _resolve_artifact_disk_path(artifact)
        if src is None or not src.exists():
            summary["missing"] += 1
            summary["warnings"].append(f"missing artifact for {rel}: {artifact}")
            continue
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        seen.add(target)
        if target.exists() or target.is_symlink():
            try:
                target.unlink()
            except OSError:
                summary["skipped"] += 1
                continue
        if _link_or_copy(src, target):
            summary["linked"] += 1
        else:
            summary["copied"] += 1
        summary["items"] += 1

    # Sidecar files — README + conventions snapshot + entity graph.
    _write_readme(out_dir)
    _write_entities_json(out_dir, include_archived=include_archived)
    _write_conventions_snapshot(out_dir)

    return summary


def _link_or_copy(src: Path, target: Path) -> bool:
    """Returns True if linked, False if fell back to copy."""
    try:
        os.symlink(src.resolve(), target)
        return True
    except (OSError, NotImplementedError):
        try:
            shutil.copy2(src, target)
        except OSError:
            pass
        return False


def _resolve_artifact_disk_path(artifact_path: str) -> Optional[Path]:
    """artifact_path is either '/artifacts/<uuid>.<ext>' or an absolute
    fs path. Map to a Path on disk."""
    if artifact_path.startswith("/artifacts/"):
        return ARTIFACTS_DIR / Path(artifact_path).name
    return Path(artifact_path) if artifact_path else None


def _text_for(entity: dict) -> str:
    """Synthesize prose for a text-only entity (claim/narrative/note)."""
    meta = entity.get("metadata") or {}
    lines = [f"# {entity['title']}", ""]
    if entity.get("notes"):
        lines.append(entity["notes"])
        lines.append("")
    for k in ("statement", "interpretation", "text"):
        if meta.get(k):
            lines.append(str(meta[k]))
            lines.append("")
    lines.append(f"<!-- entity {entity['id']} · type {entity['type']} · "
                 f"created {entity['created_at']} -->")
    return "\n".join(lines)


def _write_readme(out_dir: Path) -> None:
    txt = (
        "# Project files\n\n"
        "This directory mirrors the project's entity graph as a folder tree.\n"
        "Layout follows the conventions snapshot in `conventions.md`.\n"
        "Image / data files are symlinks (or copies, on filesystems that\n"
        "don't support links) into the canonical artifacts store.\n\n"
        "Regenerate at any time by re-running materialize.\n"
    )
    (out_dir / "README.md").write_text(txt)


def _write_entities_json(out_dir: Path, *, include_archived: bool) -> None:
    rows = []
    for e in list_entities(include_archived=include_archived):
        rows.append({
            "id": e["id"], "type": e["type"], "title": e["title"],
            "status": e["status"], "display_path": e.get("display_path"),
            "artifact_path": e.get("artifact_path"),
            "created_at": e["created_at"], "pinned": e.get("pinned", False),
        })
    (out_dir / "ENTITIES.json").write_text(json.dumps(rows, indent=2))


def _write_conventions_snapshot(out_dir: Path) -> None:
    """Copy the active conventions.md so the recipient sees the rules
    that produced this layout. Source comes from content/bio/.
    """
    src = BASE_DIR / "content" / "bio" / "conventions.md"
    if src.exists():
        shutil.copy2(src, out_dir / "conventions.md")
