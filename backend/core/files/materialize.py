"""Materialize the nested files tree to disk (files.md §3, §8).

Walks the tree composed by content.bio.files.tree.build_files_tree and
mirrors it under `projects/<pid>/files/`:
  - file nodes with `artifact_path` → symlinks (or copies on filesystems
    that can't link)
  - readme / synthesized text nodes → real files written with content;
    mtime set to the entity's created_at so `ls -lt` shows meaningful order
  - folder nodes → directories

Symlinks naturally preserve the target's mtime when listed with `ls -l`
(the symlink itself has its own mtime, but most tools follow the link
for stat purposes when the target is a regular file).
"""
from __future__ import annotations
import os
import shutil
from pathlib import Path

from core.config import ARTIFACTS_DIR


def materialize_tree(
    out_dir: Path,
    *,
    include_archived: bool = False,
    clean: bool = False,
) -> dict:
    from content.bio.files.tree import build_files_tree  # noqa: seam — Phase C.1 (move materialize.py to content/bio/files/)
    summary: dict = {
        "out_dir": str(out_dir),
        "linked": 0, "copied": 0, "synthesized": 0, "skipped": 0,
        "missing": 0, "items": 0, "warnings": [],
    }
    if clean and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    tree = build_files_tree(include_archived=include_archived)
    _materialize_node(tree, out_dir, summary)
    return summary


def _materialize_node(node: dict, out_dir: Path, summary: dict) -> None:
    kind = node.get("kind")
    if kind in ("root", "folder"):
        # Make this folder (root maps to out_dir itself).
        target = out_dir / node["path"] if node["path"] else out_dir
        target.mkdir(parents=True, exist_ok=True)
        for child in node.get("children", []):
            _materialize_node(child, out_dir, summary)
        return

    if kind == "readme":
        target = out_dir / node["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(node.get("content", ""))
        _set_mtime_if(target, node.get("mtime"))
        summary["synthesized"] += 1
        summary["items"] += 1
        return

    if kind != "file":
        return

    target = out_dir / node["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    summary["items"] += 1

    # Synthesized file (readme content / claim .md / producing_code.py /
    # result-text member). Tree builder embeds the full content as
    # `synthesized_content` so this module stays pure mechanism.
    if node.get("synthesized"):
        content = node.get("synthesized_content") or ""
        try:
            target.write_text(content)
            _set_mtime_if(target, node.get("mtime"))
            summary["synthesized"] += 1
        except OSError as e:
            summary["skipped"] += 1
            summary["warnings"].append(f"write {target}: {e}")
        return

    # Artifact-backed file
    src = _resolve_artifact_disk_path(node.get("artifact_path"))
    if src is None or not src.exists():
        summary["missing"] += 1
        summary["warnings"].append(f"missing artifact for {node['path']}: {node.get('artifact_path')}")
        return

    if target.exists() or target.is_symlink():
        try:
            target.unlink()
        except OSError:
            summary["skipped"] += 1
            return

    if _link_or_copy(src, target):
        summary["linked"] += 1
    else:
        summary["copied"] += 1


# ---------- helpers ----------

def _link_or_copy(src: Path, target: Path) -> bool:
    try:
        os.symlink(src.resolve(), target)
        # Mirror the target file's mtime onto the symlink itself so
        # `ls -l` (which uses lstat) shows when the artifact was actually
        # produced, not when the link was materialized.
        try:
            st = src.stat()
            os.utime(target, (st.st_atime, st.st_mtime), follow_symlinks=False)
        except (OSError, NotImplementedError):
            pass
        return True
    except (OSError, NotImplementedError):
        try:
            shutil.copy2(src, target)  # preserves mtime
        except OSError:
            pass
        return False


def _set_mtime_if(target: Path, mtime: float | None) -> None:
    if mtime is None:
        return
    try:
        os.utime(target, (mtime, mtime))
    except OSError:
        pass


def _resolve_artifact_disk_path(artifact_path: str | None) -> Path | None:
    if not artifact_path:
        return None
    if artifact_path.startswith("/artifacts/"):
        parts = artifact_path[len("/artifacts/"):].split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            from core.config import project_artifacts_dir
            return project_artifacts_dir(parts[0]) / parts[1]
        if len(parts) == 1:
            return ARTIFACTS_DIR / parts[0]
        return None
    return Path(artifact_path) if artifact_path else None
