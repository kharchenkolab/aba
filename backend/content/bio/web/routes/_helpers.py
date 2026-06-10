"""Shared helpers used by multiple per-entity route sub-modules.

Kept in this private module so each entity file pulls only what it
actually uses. Helpers tightly coupled to one entity (e.g. _claim_or_404,
_save_claim, _result_or_404, _run_or_404) live alongside their handlers,
not here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_thread(thread_id: str) -> str:
    if thread_id == "default":
        from core.graph.threads import get_or_create_default_thread
        return get_or_create_default_thread()
    return thread_id


# Vision-LLM figure caption helpers --------------------------------------
#
# Used by results.suggest_interpretation. Kept here because the resolver
# duplicates main.py's /artifacts/* path translation and is callable from
# any sub-module that needs to materialize an artifact URL to disk.


def _artifact_url_to_path(url: str):
    """Resolve a /artifacts/<pid>/<name> URL to a disk Path, or None.
    Local copy — main.py has its own for the /artifacts/* GET route;
    these will dedupe when an artifact-resolver moves to core."""
    import json  # noqa: F401 (kept consistent with main.py's import block)
    if not url:
        return None
    if url.startswith("/artifacts/"):
        parts = url[len("/artifacts/"):].split("/")
        if len(parts) == 2 and parts[0] and parts[1]:
            from core.config import project_artifacts_dir
            return project_artifacts_dir(parts[0]) / parts[1]
        if len(parts) == 1:
            from core.config import ARTIFACTS_DIR
            return ARTIFACTS_DIR / parts[0]
        return None
    return Path(url) if url else None


def _llm_figure_caption(artifact_path: str, producing_code: str,
                        chat_context: str, title: str) -> str:
    """Thin wrapper around the shared vision-LLM caption helper."""
    from content.bio.lifecycle.promote import caption_via_vision_llm
    disk = _artifact_url_to_path(artifact_path) if artifact_path else None
    return caption_via_vision_llm(disk, producing_code, chat_context, title)
