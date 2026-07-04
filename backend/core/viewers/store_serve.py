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
    """Generic STORED single-file pack of a `.lstar.zarr` DIRECTORY (contents at
    the zip root) — the FALLBACK for the download deliverable.

    The pagoda3 launcher normally delegates to lstar's own `_pack_stored_zip` so
    the archive is byte-for-byte lstar's canonical `.lstar.zarr.zip`; this
    equivalent (STORED, uncompressed, metadata `.z*` first) is used only if that
    lstar entry point is unavailable. STORED (not DEFLATE) keeps chunks
    byte-range-readable inside the single file, which is the point of the format —
    a `.zip`-aware reader (pagoda3 ZipStore) opens it directly."""
    import os
    import zipfile
    store_dir = store_dir.resolve()
    entries: list[tuple[str, Path]] = []
    for dp, _dirs, files in os.walk(store_dir):
        for fn in files:
            fp = Path(dp) / fn
            entries.append((fp.relative_to(store_dir).as_posix(), fp))
    entries.sort(key=lambda e: (not os.path.basename(e[0]).startswith(".z"), e[0]))
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
        for arc, fp in entries:
            z.write(fp, arc)
    return dest
