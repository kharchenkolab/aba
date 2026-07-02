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
