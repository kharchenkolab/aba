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
# Used by results.suggest_interpretation. The /artifacts/* → disk translation
# now lives in core.web.artifacts (Item 2A.1); this thin wrapper adds only the
# bare-disk-path fallback that this caller also accepts.


def _artifact_url_to_path(url: str):
    """Resolve a /artifacts/<pid>/<name> URL (via core.web.artifacts) to a disk
    Path, or treat a non-/artifacts value as a bare disk path. None if empty."""
    if not url:
        return None
    if url.startswith("/artifacts/"):
        from core.web.artifacts import _artifact_url_to_path as _canon
        return _canon(url)
    return Path(url)


def _llm_figure_caption(artifact_path: str, producing_code: str,
                        chat_context: str, title: str) -> str:
    """Thin wrapper around the shared vision-LLM caption helper."""
    from content.bio.lifecycle.promote import caption_via_vision_llm
    disk = _artifact_url_to_path(artifact_path) if artifact_path else None
    return caption_via_vision_llm(disk, producing_code, chat_context, title)
