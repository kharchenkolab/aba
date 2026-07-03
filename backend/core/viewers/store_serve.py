"""Serving support for external-viewer data stores (viewers.md §3 external mode).

An external viewer like pagoda3 reads a directory-shaped data store (a
`.lstar.zarr` tree of dotfiles + chunks) from the browser over HTTP Range.
This module holds the domain-neutral, security-critical bit: resolving a
client-supplied relative path *within* a store root without letting it escape
(path traversal). Range handling itself is left to Starlette's FileResponse.
"""
from __future__ import annotations

from pathlib import Path


def resolve_within(base: Path, relpath: str) -> Path:
    """Resolve `relpath` under `base`, guaranteeing the result stays inside
    `base` (defeats `..`, absolute paths, and symlink escapes). Raises
    ValueError if the target would fall outside the root.

    Empty relpath resolves to `base` itself. Both are `.resolve()`d so a
    symlinked chunk that points out of the tree is rejected too."""
    base_r = base.resolve()
    # A leading "/" in relpath would make (base / relpath) ignore base — strip it.
    target = (base_r / relpath.lstrip("/")).resolve()
    if target != base_r and base_r not in target.parents:
        raise ValueError(f"path {relpath!r} escapes store root {base_r}")
    return target


def zip_store_stored(store_dir: Path, dest: Path) -> Path:
    """Pack a `.lstar.zarr` store DIRECTORY into a STORED (uncompressed) zip at
    `dest` — the "quick download" of a viewer store as one file.

    STORED (not DEFLATE) on purpose: an uncompressed zip is HTTP-range-readable,
    so the downloaded `.lstar.zarr.zip` re-opens directly in pagoda3 / lstar
    without unpacking (pagoda3's ZipStore issues one Range per chunk; a DEFLATE
    entry would defeat that and is rejected on read). Arcnames are relative to the
    store root (top-level `.zattrs`/`axes`/`fields`).

    Entry order mirrors lstar's canonical `_pack_stored_zip`: zarr metadata
    (`.z*` — `.zmetadata`/`.zgroup`/`.zarray`/`.zattrs`) FIRST so a range reader
    hits the manifest early, then the rest sorted — deterministic, byte-stable."""
    import os
    import zipfile
    store_dir = store_dir.resolve()
    entries: list[tuple[str, Path]] = []
    for root, _dirs, files in os.walk(store_dir):
        for fn in files:
            fp = Path(root) / fn
            entries.append((fp.relative_to(store_dir).as_posix(), fp))
    entries.sort(key=lambda e: (not os.path.basename(e[0]).startswith(".z"), e[0]))
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for arc, fp in entries:
            z.write(fp, arc)
    return dest
