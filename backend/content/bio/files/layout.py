"""Per-entity-type display-path computers (files.md §5).

Each function takes an entity dict and returns a relative POSIX path,
following the conventions in bio/conventions.md.

These are pure functions — no DB calls, no filesystem touches.
Registered at import time via register_layout_computer().
"""
from __future__ import annotations

from core.files.registry import (
    register_layout_computer, slugify, ext_from_artifact,
)


def _date_prefix(entity: dict) -> str:
    """ISO date (YYYY-MM-DD) parsed from created_at, or '' if missing."""
    s = entity.get("created_at") or ""
    return s[:10] if len(s) >= 10 else ""


def figure_path(e: dict) -> str:
    slug = slugify(e.get("title") or "")
    ext = ext_from_artifact(e, default=".png")
    # qc_*, de_* titles cluster under their group; otherwise top-level
    # figures/. This matches conventions.md "group/prefix hints".
    if slug.startswith("qc_"):
        return f"figures/qc/{slug}{ext}"
    if slug.startswith("de_"):
        return f"figures/de/{slug}{ext}"
    if slug.startswith("umap_") or slug.startswith("tsne_") or slug.startswith("pca_"):
        return f"figures/embeddings/{slug}{ext}"
    return f"figures/{slug}{ext}"


def table_path(e: dict) -> str:
    slug = slugify(e.get("title") or "")
    ext = ext_from_artifact(e, default=".csv")
    return f"tables/{slug}{ext}"


def dataset_path(e: dict) -> str:
    slug = slugify(e.get("title") or "")
    ext = ext_from_artifact(e, default="")
    return f"datasets/{slug}{ext}"


def result_path(e: dict) -> str:
    # Results are containers (one or more members). Map to a directory.
    return f"results/{slugify(e.get('title') or '')}/"


def analysis_path(e: dict) -> str:
    date = _date_prefix(e)
    slug = slugify(e.get("title") or "run")
    prefix = f"{date}_" if date else ""
    return f"runs/{prefix}{slug}/"


def thread_path(e: dict) -> str:
    return f"threads/{slugify(e.get('title') or 'thread')}/"


def claim_path(e: dict) -> str:
    return f"claims/{slugify(e.get('title') or 'claim')}.md"


def narrative_path(e: dict) -> str:
    return f"narratives/{slugify(e.get('title') or 'narrative')}.md"


def finding_path(e: dict) -> str:
    return f"findings/{slugify(e.get('title') or 'finding')}/"


def note_path(e: dict) -> str:
    return f"notes/{slugify(e.get('title') or 'note')}.md"


def plan_path(e: dict) -> str:
    # Plans are .md prose under their thread (the tree composer places
    # them under threads/T/plans/NN_slug.md; this is the fallback
    # display_path for plans that aren't thread-scoped).
    return f"plans/{slugify(e.get('title') or 'plan')}.md"


register_layout_computer("figure", figure_path)
register_layout_computer("table", table_path)
register_layout_computer("dataset", dataset_path)
register_layout_computer("result", result_path)
register_layout_computer("analysis", analysis_path)
register_layout_computer("thread", thread_path)
register_layout_computer("claim", claim_path)
register_layout_computer("narrative", narrative_path)
register_layout_computer("finding", finding_path)
register_layout_computer("note", note_path)
register_layout_computer("plan", plan_path)
