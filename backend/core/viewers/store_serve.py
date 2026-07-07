"""Serving support for external-viewer data stores (viewers.md §3 external mode).

An external viewer like pagoda3 reads a directory-shaped data store (a
`.lstar.zarr` tree of dotfiles + chunks) from the browser over HTTP Range.
This module holds the domain-neutral, security-critical bit: resolving a
client-supplied relative path *within* a store root without letting it escape
(path traversal). Range handling itself is left to Starlette's FileResponse.
"""
from __future__ import annotations

from pathlib import Path


def resolve_within(base: Path, relpath: str,
                   extra_roots: "tuple[Path, ...]" = ()) -> Path:
    """Resolve `relpath` under `base`, guaranteeing the result stays inside
    `base` — or, if given, inside one of `extra_roots`. Raises ValueError if the
    target would fall outside every allowed root.

    Empty relpath resolves to `base` itself. The result is `.resolve()`d, so a
    symlink is FOLLOWED and its real target is what gets range-checked.

    `..` in the request path is rejected outright: the only legitimate way for a
    served path to leave `base` is a symlink WE placed inside it (whose real
    target we vet against `extra_roots`), never a `..` in the client's URL. This
    lets the pagoda3 store serve a run's `.lstar.zarr` in place (symlinked from
    pagoda3/ → work/) without copying the tree, while still confining reads to
    the project. Without the `..` block, widening the roots would let a crafted
    URL walk up out of the store dir — so the two changes are a matched pair."""
    base_r = base.resolve()
    rel = relpath.lstrip("/")   # a leading "/" would make (base / rel) ignore base
    if ".." in Path(rel).parts:
        raise ValueError(f"path {relpath!r} traverses upward")
    target = (base_r / rel).resolve()   # follows symlinks in the tree
    roots = (base_r, *(r.resolve() for r in extra_roots))
    if not any(target == r or r in target.parents for r in roots):
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
