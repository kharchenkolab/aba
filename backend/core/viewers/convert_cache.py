"""Cache derived viewer stores: convert a source artifact once, reuse after.

Keyed by (source identity, converter version): a changed source (size/mtime) or
a bumped version re-derives; otherwise the cached store is reused so re-opening
a file in an external viewer is instant. Domain-neutral (an external launcher
supplies the concrete `convert` — e.g. .h5ad → .lstar.zarr via lstar).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Callable


def source_sig(src: Path) -> str:
    """Cheap identity for a source file: name + size + mtime. (Content hashing a
    multi-GB .h5ad on every launch would defeat the point of the cache.)"""
    st = src.stat()
    return f"{src.name}:{st.st_size}:{int(st.st_mtime)}"


def ensure_derived(src: Path, cache_dir: Path, out_name: str, version: str,
                   convert: Callable[[Path, Path], None]) -> Path:
    """Return a derived store at `cache_dir/out_name`, (re)building it via
    `convert(src, out)` only when missing or stale. Freshness is tracked in a
    sidecar `<out_name>.cache.json` holding the source sig + version. The build
    goes into a temp dir and is swapped in, so a crashed conversion never leaves
    a half-written store masquerading as valid."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / out_name
    meta = cache_dir / (out_name + ".cache.json")
    want = {"sig": source_sig(src), "version": version}

    if out.exists() and meta.exists():
        try:
            if json.loads(meta.read_text()) == want:
                return out
        except Exception:  # noqa: BLE001
            pass  # unreadable meta → rebuild

    tmp = cache_dir / (out_name + ".building")
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    if meta.exists():
        meta.unlink()                       # mark stale during the (re)build
    convert(src, tmp)
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    tmp.rename(out)
    meta.write_text(json.dumps(want))
    return out
