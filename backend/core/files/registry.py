"""Display-path layout registry (files.md §5).

Per-entity-type pure functions that compute a relative POSIX path from
an entity dict. Content registers each one at import time, e.g.
    register_layout_computer(\"type_a\", compute_type_a_path)

The registry is consulted at artifact-registration time (F3 will persist
the result) and by the virtual files view (F2) to project the entity
graph as a folder tree.

Pure: computers never touch the filesystem, never make DB calls beyond
what's in the entity dict they're handed. That keeps them testable and
regeneratable when conventions change.
"""
from __future__ import annotations
from typing import Callable

# Computer signature: entity_dict -> POSIX path string (relative).
LayoutComputer = Callable[[dict], str]

_COMPUTERS: dict[str, LayoutComputer] = {}


def register_layout_computer(entity_type: str, fn: LayoutComputer) -> None:
    _COMPUTERS[entity_type] = fn


def display_path_for(entity: dict) -> str:
    """Compute the display path for an entity. Falls back to a generic
    "{type}s/{title_slug}" if no per-type computer is registered."""
    fn = _COMPUTERS.get(entity.get("type") or "")
    if fn is None:
        return _generic_path(entity)
    return fn(entity)


def slugify(text: str) -> str:
    """Conventional file-friendly slug: lowercase, ASCII, snake_case-ish.
    Spaces and dashes → underscore; strip punctuation; collapse runs."""
    import re
    import unicodedata
    if not text:
        return "untitled"
    s = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    s = s.lower().strip()
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"[^a-z0-9_./]+", "", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:80] or "untitled"


def ext_from_artifact(entity: dict, default: str = "") -> str:
    """Pluck the extension off `artifact_path` if any. Returns '' if none."""
    p = entity.get("artifact_path") or ""
    if not p:
        return default
    # paths look like "/artifacts/abc.png" — split off the suffix
    name = p.rsplit("/", 1)[-1]
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else default


def name_with_ext(slug: str, ext: str) -> str:
    """Compose a file name from a slug + extension, avoiding the duplicate-
    suffix bug (`sample_cells_15.csv.csv`). slugify() preserves dots, so
    a dataset whose title ends in `.csv` already carries the extension,
    and naively appending again doubles it up. Idempotent and case-
    insensitive: matches `.CSV` vs `.csv` too."""
    if ext and slug.lower().endswith(ext.lower()):
        return slug
    return slug + ext


def _generic_path(entity: dict) -> str:
    """Fallback layout for unregistered types: {type}s/{title_slug}.ext"""
    t = entity.get("type") or "entity"
    slug = slugify(entity.get("title") or "")
    ext = ext_from_artifact(entity)
    return f"{t}s/{slug}{ext}"
