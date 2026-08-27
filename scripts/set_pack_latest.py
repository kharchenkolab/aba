#!/usr/bin/env python
"""Repoint a published pack's `latest` at a version already in the store.

WHY THIS EXISTS. `latest` is written by exactly one thing — `env_publish`,
which BUILDS a squashfs image. There is no supported way to correct the pointer
for an image that is already published, so the only alternative is editing
catalog.json by hand. That was tried on 2026-08-27 and made things worse: the
edit looked right, resolution is by recorded EnvID rather than by the pointer,
and hours went into misdiagnosing the result.

So: a small, auditable operation that uses weft's OWN catalog serialization
(atomic temp-file + mv, the same `_write_catalog` publish uses) and REFUSES
anything it cannot verify first.

WHEN IT IS NEEDED. A promote used to mirror packs from a per-target tree into
the shared store, re-registering older builds and dragging `latest` backwards —
so a deployment riding `latest` silently moved onto images it had never tested.
That step is gone; this repairs stores it already damaged.

    python scripts/set_pack_latest.py --tree /path/to/store \\
        --pack python-bio --version 2026.08.27-8d4389ba
    python scripts/set_pack_latest.py --tree ... --pack r-bio --newest

Refuses to point `latest` at a version whose image is missing: a dangling
pointer is worse than a stale one, because every consumer riding `latest`
fails to adopt rather than merely running something old.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path


def _catalog_path(tree: Path) -> Path:
    return tree / "catalog.json"


def _image_for(tree: Path, env_id: str) -> Path:
    h = env_id.split(":")[-1] if ":" in env_id else env_id
    return tree / "envs" / h / "image.sqfs"


def repoint(tree: Path, pack: str, version: str | None, newest: bool) -> dict:
    """-> {'pack','from','to','env_id'}. Raises ValueError on anything unsafe."""
    cat_p = _catalog_path(tree)
    if not cat_p.is_file():
        raise ValueError(f"no catalog at {cat_p}")
    catalog = json.loads(cat_p.read_text())
    entry = (catalog.get("envs") or {}).get(pack)
    if entry is None:
        raise ValueError(f"pack {pack!r} is not in {cat_p} "
                         f"[have: {', '.join(sorted((catalog.get('envs') or {})))}]")
    versions = entry.get("versions") or {}
    if not versions:
        raise ValueError(f"pack {pack!r} has no published versions")

    if newest:
        version = sorted(versions)[-1]      # version strings are date-prefixed
    if not version:
        raise ValueError("need --version or --newest")
    if version not in versions:
        raise ValueError(f"{pack}@{version} is not published "
                         f"[have: {', '.join(sorted(versions))}]")

    env_id = (versions[version] or {}).get("env_id") or ""
    img = _image_for(tree, env_id)
    if not img.is_file():
        raise ValueError(
            f"{pack}@{version} has no image at {img} — refusing to point "
            f"`latest` at a version that cannot be adopted")

    before = entry.get("latest")
    if before == version:
        return {"pack": pack, "from": before, "to": version,
                "env_id": env_id, "changed": False}
    entry["latest"] = version

    # weft's own shape: atomic temp + replace, indent=1, sort_keys.
    tmp = cat_p.with_suffix(f".json.tmp.{pack}")
    tmp.write_text(json.dumps(catalog, indent=1, sort_keys=True) + "\n")
    shutil.move(str(tmp), str(cat_p))
    return {"pack": pack, "from": before, "to": version,
            "env_id": env_id, "changed": True}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", required=True, help="the published pack store")
    ap.add_argument("--pack", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--version", help="the version to make latest")
    g.add_argument("--newest", action="store_true",
                   help="the highest version string present")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    try:
        if a.dry_run:
            cat = json.loads(_catalog_path(Path(a.tree)).read_text())
            e = (cat.get("envs") or {}).get(a.pack) or {}
            want = sorted(e.get("versions") or {})[-1] if a.newest else a.version
            print(f"{a.pack}: latest {e.get('latest')} -> {want} (dry run)")
            return 0
        r = repoint(Path(a.tree), a.pack, a.version, a.newest)
    except ValueError as e:
        print(f"set_pack_latest: {e}", file=sys.stderr)
        return 2
    if not r["changed"]:
        print(f"   {r['pack']}: latest already {r['to']}")
    else:
        print(f"   {r['pack']}: latest {r['from']} -> {r['to']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
