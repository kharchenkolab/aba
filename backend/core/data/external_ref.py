"""External-reference bookkeeping for imported Runs & Datasets (misc/external_import.md).

An imported entity references a payload in an EXTERNAL, read-only directory (Location 1) that
ABA does not own and must never write to. All ABA bookkeeping — the entity sidecar, provenance,
and the drift baseline below — lives in the project tree (Location 2), so crash-recovery is
unaffected (recovery only ever reads Location 2; see misc/recovery.md).

Drift policy is FLAG-ONLY: at import we snapshot a compact, stat-only fingerprint of Location 1
and store it INLINE in the entity metadata (→ it lands in the entity sidecar → it survives DB
loss). Later we can re-walk the path, recompute, and compare — if it differs or the path is gone,
the entity is flagged stale. We never re-copy or block.

The fingerprint is deliberately content-free (stat only): a `stat()` per file is cheap even over
NFS for a large results tree, and (relpath, size, mtime) is enough to detect a re-run, a partial
delete, or a moved/removed tree. mtimes are compared FS-to-FS (import snapshot vs current), never
against wall-clock, so the 1 s NFS mtime granularity is fine.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

# Guard against a pathological tree (millions of files) making the walk unbounded. Past this we
# stop, flag `truncated`, and the digest covers only the first N entries in walk order — still a
# usable change signal, just approximate. 200k stats is a few seconds even on NFS.
_MAX_ENTRIES = 200_000


def resolve_external(path: str) -> tuple[str, bool]:
    """Absolute path + whether it exists. Expands user/vars; does NOT require existence
    (a caller may want to register a not-yet-mounted path and let drift flag it)."""
    abspath = os.path.abspath(os.path.expanduser(os.path.expandvars(str(path))))
    return abspath, os.path.exists(abspath)


def fingerprint(path: str, *, max_entries: int = _MAX_ENTRIES) -> dict:
    """Compact stat-only snapshot of an external file or directory:

        {exists, n_files, total_bytes, max_mtime, digest, truncated}

    `digest` = sha1 over sorted "relpath\\tsize\\tmtime" lines, so it is stable across walks and
    changes iff any file is added/removed/resized/re-timestamped. Small enough to store inline in
    entity metadata. `{"exists": False}` when the path is gone."""
    p = Path(path)
    if not p.exists():
        return {"exists": False}
    if p.is_file():
        try:
            st = p.stat()
        except OSError:
            return {"exists": False}
        h = hashlib.sha1(f"{p.name}\t{st.st_size}\t{int(st.st_mtime)}\n".encode())
        return {"exists": True, "n_files": 1, "total_bytes": int(st.st_size),
                "max_mtime": int(st.st_mtime), "digest": h.hexdigest(), "truncated": False}
    rows: list[tuple[str, int, int]] = []
    total = 0
    max_mtime = 0
    truncated = False
    for f in p.rglob("*"):
        try:
            if not f.is_file() or f.is_symlink():   # symlinks: record the link, not its target bytes
                st = f.lstat()
                if not f.is_file():
                    continue
            else:
                st = f.stat()
        except OSError:
            continue
        try:
            rel = f.relative_to(p).as_posix()
        except ValueError:
            rel = f.name
        mt = int(st.st_mtime)
        rows.append((rel, int(st.st_size), mt))
        total += int(st.st_size)
        if mt > max_mtime:
            max_mtime = mt
        if len(rows) >= max_entries:
            truncated = True
            break
    rows.sort()
    h = hashlib.sha1()
    for rel, sz, mt in rows:
        h.update(f"{rel}\t{sz}\t{mt}\n".encode())
    return {"exists": True, "n_files": len(rows), "total_bytes": total,
            "max_mtime": max_mtime, "digest": h.hexdigest(), "truncated": truncated}


def check_drift(metadata: Optional[dict]) -> dict:
    """Compare an imported entity's stored baseline against Location 1 now. Returns:

        {"stale": False}                                    — fresh, or not an external entity
        {"stale": True, "reason": "missing", "detail": …}   — ref_path is gone/unreadable
        {"stale": True, "reason": "changed", "detail": …}   — contents differ from the baseline

    Reads `ref_path` + `import_fingerprint` from the entity metadata (both live in the sidecar, so
    this works after a DB-crash recovery too). Never writes anything."""
    md = metadata or {}
    ref = md.get("ref_path")
    if not ref:
        return {"stale": False}                    # not a by-reference/external entity
    base = md.get("import_fingerprint") or {}
    cur = fingerprint(str(ref))
    if not cur.get("exists"):
        return {"stale": True, "reason": "missing",
                "detail": f"referenced location is gone or unreadable: {ref}"}
    if base and base.get("digest") and cur.get("digest") != base.get("digest"):
        nb, nc = base.get("n_files"), cur.get("n_files")
        det = "contents changed since import"
        if nb is not None and nc is not None and nb != nc:
            det += f" ({nb}→{nc} files)"
        return {"stale": True, "reason": "changed", "detail": det}
    return {"stale": False}
