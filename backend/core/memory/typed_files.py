"""Typed-file memory implementation.

Each memory is one .md file with frontmatter:

    ---
    name: feedback-control-sample
    description: Control sample is S0123; user noted on 2026-05-20.
    type: feedback
    ---

    Free-form body.

The MEMORY.md index is a separate file: one line per memory of the form
`- [<description>](<name>.md) — <hook>`. It's regenerated whenever a
memory is written or deleted, so the manifest always reflects truth.

The directory lives at `projects/<pid>/memory/`. For single-project
test mode (ABA_DB_PATH set), the path falls back to
`projects/single/memory/`.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from core.projects import PROJECTS_DIR, current as _current_project


# The four memory categories, mirroring Claude Code's pattern. The
# distinction is task-shaping (when to retrieve, when to overwrite),
# not domain-specific, so it lives in core rather than bio.
MEMORY_TYPES = ("user", "feedback", "project", "reference")

_INDEX_FILE = "MEMORY.md"
_SPLIT = "---"


@dataclass
class MemoryEntry:
    name:        str           # slug, becomes the filename
    description: str           # one-line summary shown in the index
    type:        str           # one of MEMORY_TYPES
    body:        str = ""
    path:        str = ""      # absolute path on disk; populated on read


def memory_dir() -> Path:
    """Resolve the current project's memory/ dir. Creates it on first
    use so callers never have to check existence."""
    pid = _current_project() or "single"
    d = PROJECTS_DIR / pid / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith(_SPLIT):
        return {}, text.strip()
    rest = text[len(_SPLIT):]
    end = rest.find("\n" + _SPLIT)
    if end == -1:
        raise ValueError("unterminated frontmatter")
    fm_raw = rest[:end]
    body = rest[end + len("\n" + _SPLIT):].lstrip("\n").rstrip()
    fm = yaml.safe_load(fm_raw) or {}
    if not isinstance(fm, dict):
        raise ValueError("frontmatter must be a mapping")
    return fm, body


def _entry_from_file(f: Path) -> Optional[MemoryEntry]:
    try:
        text = f.read_text()
        fm, body = _split_frontmatter(text)
    except (OSError, ValueError):
        return None
    name = (fm.get("name") or f.stem).strip()
    if not name:
        return None
    return MemoryEntry(
        name=name,
        description=str(fm.get("description") or "").strip(),
        type=str(fm.get("type") or "reference").strip(),
        body=body,
        path=str(f),
    )


def list_memories() -> list[MemoryEntry]:
    """Every memory file in the current project's memory/ dir
    (excluding the index itself), sorted by name."""
    out = []
    for f in sorted(memory_dir().glob("*.md")):
        if f.name == _INDEX_FILE:
            continue
        e = _entry_from_file(f)
        if e:
            out.append(e)
    return out


def read_memory(name: str) -> Optional[MemoryEntry]:
    """Look up one memory by name (the slug, not the filename)."""
    for e in list_memories():
        if e.name == name:
            return e
    return None


def write_memory(*, name: str, body: str, type: str = "reference",
                 description: str = "") -> MemoryEntry:
    """Write or overwrite a memory file + regenerate the index. The
    name is slugified to be filename-safe; the original is preserved
    in frontmatter so the agent can use it as-typed when reading."""
    name = (name or "").strip()
    if not name:
        raise ValueError("memory name is required")
    if type not in MEMORY_TYPES:
        raise ValueError(f"type must be one of {MEMORY_TYPES}; got {type!r}")
    fname = _slug(name) + ".md"
    d = memory_dir()
    fm = {
        "name": name,
        "description": (description or "").strip() or name,
        "type": type,
    }
    content = (
        _SPLIT + "\n"
        + yaml.safe_dump(fm, sort_keys=False).strip()
        + "\n" + _SPLIT + "\n\n"
        + (body or "").strip() + "\n"
    )
    (d / fname).write_text(content)
    _rewrite_index()
    e = read_memory(name)
    assert e is not None  # we just wrote it
    return e


def delete_memory(name: str) -> bool:
    """Remove a memory file (and refresh the index). Returns False if
    the name didn't resolve."""
    e = read_memory(name)
    if e is None:
        return False
    try:
        Path(e.path).unlink()
    except OSError:
        return False
    _rewrite_index()
    return True


def read_memory_index() -> str:
    """The current MEMORY.md contents, or '' when no memories exist."""
    f = memory_dir() / _INDEX_FILE
    if not f.exists():
        return ""
    return f.read_text()


def _rewrite_index() -> None:
    """Regenerate MEMORY.md from the current memory files. One line
    per entry, grouped by type, mirroring the auto-memory pattern."""
    d = memory_dir()
    entries = list_memories()
    if not entries:
        # Empty registry — clear the index so the manifest doesn't show
        # a stale heading.
        idx = d / _INDEX_FILE
        if idx.exists():
            idx.unlink()
        return
    lines: list[str] = []
    by_type: dict[str, list[MemoryEntry]] = {}
    for e in entries:
        by_type.setdefault(e.type, []).append(e)
    for t in MEMORY_TYPES:
        es = by_type.get(t)
        if not es:
            continue
        for e in es:
            lines.append(f"- [{e.description or e.name}]({_slug(e.name)}.md) — {t}")
    (d / _INDEX_FILE).write_text("\n".join(lines) + "\n")


def memory_index_block() -> str:
    """The block embedded in the per-turn system prompt. Header +
    index lines so the agent knows what's available without paying
    for every body. Returns '' when no memories exist."""
    body = read_memory_index().strip()
    if not body:
        return ""
    return (
        "### Project memory\n"
        "Notes you've kept across sessions. Use `read_memory(name)` to "
        "load the body; `write_memory(name, body, type)` to add or "
        "update one (types: " + ", ".join(MEMORY_TYPES) + ").\n\n"
        + body
    )


_SLUG_OK = "abcdefghijklmnopqrstuvwxyz0123456789-_"


def _slug(name: str) -> str:
    out = []
    for ch in name.strip().lower().replace(" ", "-"):
        out.append(ch if ch in _SLUG_OK else "-")
    s = "".join(out).strip("-") or "untitled"
    # Collapse runs of '-'
    while "--" in s:
        s = s.replace("--", "-")
    return s
