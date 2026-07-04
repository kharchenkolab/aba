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


def zip_store_dir(store_dir: Path, dest: Path, arc_root: str) -> Path:
    """Pack a `.lstar.zarr` store DIRECTORY into a zip that unpacks to a FOLDER
    `<arc_root>/…` — the download deliverable.

    We hand back the *regular directory* `.lstar.zarr` (not lstar's single-file
    STORED `.lstar.zarr.zip`): a directory store loads much faster in pagoda3
    (parallel chunk fetches vs one-range-at-a-time into a packed file) and stays
    updatable. The zip is just a transport container — every entry is nested under
    `arc_root` (e.g. `pbmc3k.lstar.zarr/…`), so unzipping recreates the directory
    store. DEFLATE (chunks are already codec-compressed, so this mostly shrinks
    the JSON metadata) and deterministic order for a byte-stable archive."""
    import os
    import zipfile
    store_dir = store_dir.resolve()
    root = arc_root.rstrip("/")
    entries: list[tuple[str, Path]] = []
    for dp, _dirs, files in os.walk(store_dir):
        for fn in files:
            fp = Path(dp) / fn
            entries.append((f"{root}/{fp.relative_to(store_dir).as_posix()}", fp))
    entries.sort(key=lambda e: e[0])
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        for arc, fp in entries:
            z.write(fp, arc)
    return dest
