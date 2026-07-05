"""Artifact URL↔disk resolution — a domain-neutral web helper.

Single source of truth for mapping an `/artifacts/...` URL (as stored in an
entity's `artifact_path`) to a disk path. Lived in `main.py` until Item 2A.1;
moved here so it sits BELOW the content pack — content code (and the composition
root) import it from `core.web`, dissolving the old `content → main` up-import
(entity_ops.py used to do `from main import _artifact_url_to_path`).
"""
from __future__ import annotations

from pathlib import Path

from core.config import ARTIFACTS_DIR, project_artifacts_dir


def _artifact_url_to_path(url: str) -> Path | None:
    """Resolve an `/artifacts/...` URL stored in an entity record to a disk path.
    Returns None if the URL doesn't match the expected shape or escapes a project
    boundary. Single source of truth for URL→file mapping across handlers."""
    if not url or not url.startswith("/artifacts/"):
        return None
    parts = url[len("/artifacts/"):].split("/")
    if len(parts) == 2 and parts[0] and parts[1] and ".." not in parts[0] and ".." not in parts[1]:
        # New per-project shape: /artifacts/<pid>/<name>
        return project_artifacts_dir(parts[0]) / parts[1]
    if len(parts) == 1 and parts[0] and ".." not in parts[0]:
        # Legacy workspace-level fallback: /artifacts/<name>
        return ARTIFACTS_DIR / parts[0]
    return None
