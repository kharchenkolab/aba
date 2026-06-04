"""File-system collision helpers. Bi-side (platform + content); the two
copies that lived in `main.py` and `bio/web/routes.py` are now one
canonical pair. Pure path math — no I/O beyond `.exists()`, no bio /
domain concepts.

`unique_path(dest)` handles SINGLE-FILE upload collisions: keeps the
extension, appends `_1`, `_2`, ... to the stem (e.g. report.pdf →
report_1.pdf). Used by /api/upload + bio's dataset/evidence upload paths.

`unique_dir_path(p)` handles DIRECTORY upload collisions: keeps the full
name, appends ` (2)`, ` (3)`, ... (e.g. 10x_bundle → "10x_bundle (2)").
Used by bio's /api/upload-folder. The space-paren-N suffix mirrors macOS
Finder's collision style, which matches what users expect when they
drag-and-drop a duplicate folder into a project.

Loop bounds aren't aesthetic — they prevent infinite collision loops if
something corrupts the parent dir's listing. 1000 is plenty for any
realistic upload pattern; we'd be alerted to a deeper bug long before
hitting it.
"""
from __future__ import annotations
from pathlib import Path


def unique_path(dest: Path) -> Path:
    """Return `dest` if it doesn't exist; otherwise the next free
    `<stem>_N<suffix>` sibling (N = 1, 2, 3, ...)."""
    if not dest.exists():
        return dest
    stem, suf = dest.stem, dest.suffix
    i = 1
    while True:
        candidate = dest.parent / f"{stem}_{i}{suf}"
        if not candidate.exists():
            return candidate
        i += 1


def unique_dir_path(p: Path) -> Path:
    """Return `p` if it doesn't exist; otherwise the next free
    ` (N)` sibling (N = 2, 3, ...) — Finder-style folder collision."""
    if not p.exists():
        return p
    parent, stem = p.parent, p.name
    for n in range(2, 1000):
        cand = parent / f"{stem} ({n})"
        if not cand.exists():
            return cand
    raise RuntimeError(f"too many name collisions for {stem!r}")
