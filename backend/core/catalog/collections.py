"""Capability collections — file-backed bundles of capabilities (collections.md).

A collection is a directory with a `collection.yaml` (name, scope, runtime +
provisioning recipe) and a generated `index.json` (one entry per capability).
Its capabilities are loaded once per process into a searchable layer that
`search_capabilities` / `resolve_capability` / `read_capability` consult
alongside DB entities — not seeded as graph entities (so a large bundle like
biomni doesn't bloat every project DB).

Adding a collection = register its directory; no code changes per collection.
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Optional

import yaml

# Registered collection directories (absolute paths). Content packs register
# theirs at import time; the merged capability layer is cached and rebuilt when
# the set changes.
_DIRS: list[Path] = []
_CACHE: Optional[list[dict]] = None


def register_collection_dir(path: str | Path) -> None:
    """Register a collection directory (idempotent). Invalidates the cache."""
    p = Path(path).resolve()
    if p not in _DIRS:
        _DIRS.append(p)
        _invalidate()


def _invalidate() -> None:
    global _CACHE
    _CACHE = None


def _load_one(d: Path) -> list[dict]:
    """Parse one collection dir into capability dicts. Robust: a missing/broken
    manifest or index yields [] rather than raising (a content bug shouldn't
    take down search)."""
    meta_path = d / "collection.yaml"
    if not meta_path.is_file():
        return []
    try:
        meta = yaml.safe_load(meta_path.read_text()) or {}
    except yaml.YAMLError:
        return []
    cname = meta.get("name") or d.name
    scope = meta.get("scope") or "institution"
    origin = meta.get("origin") or cname
    source = meta.get("source") or origin
    # kind "reference" = extracted knowledge, not runnable here (biomni today):
    # no provisioning, flagged so ensure_capability/read_capability explain it.
    # kind "runtime" (future) = a real collection with a provisioning recipe.
    kind = meta.get("kind") or "runtime"
    runtime = meta.get("runtime") or {}
    provisioning = {} if kind == "reference" else (runtime.get("provisioning") or {})
    index_path = d / (meta.get("index") or "index.json")
    if not index_path.is_file():
        return []
    try:
        entries = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []

    caps: list[dict] = []
    for e in entries:
        name = (e.get("name") or "").strip()
        if not name:
            continue
        domain = e.get("domain")
        tags = e.get("domain_tags") or ([domain, cname] if domain else [cname])
        cap = {
            "id": f"{cname}:{domain}:{name}" if domain else f"{cname}:{name}",
            "name": name,
            "archetype": e.get("archetype") or "library",
            "summary": e.get("summary") or "",
            "domain_tags": tags,
            "function": e.get("function") or name,
            "required_params": e.get("required_params") or [],
            "optional_params": e.get("optional_params") or [],
            "provisioning": dict(provisioning),
            "scope": scope,
            "collection": cname,
            "kind": kind,
            "origin": origin,
            "source": source,
            "status": "published",
        }
        if kind == "reference":
            cap["reference"] = True
            cap["source_ref"] = e.get("source_ref")
        else:
            cap["import_path"] = e.get("import_path")
        caps.append(cap)
    return caps


def collection_capabilities() -> list[dict]:
    """All capabilities contributed by registered collections (cached)."""
    global _CACHE
    if _CACHE is None:
        merged: list[dict] = []
        for d in _DIRS:
            merged.extend(_load_one(d))
        _CACHE = merged
    return list(_CACHE)


def find_collection_capability(name: str) -> Optional[dict]:
    """Resolve a collection capability by exact name (first match across
    collections), or None."""
    name = (name or "").strip()
    if not name:
        return None
    for cap in collection_capabilities():
        if cap.get("name") == name:
            return cap
    return None


def collection_domains() -> dict[str, list[str]]:
    """{collection_name: sorted distinct domains} — a cheap 'map' for surfacing
    what universes exist without listing every capability."""
    out: dict[str, set] = {}
    for cap in collection_capabilities():
        col = cap.get("collection") or "?"
        for t in (cap.get("domain_tags") or []):
            if t and t != col:
                out.setdefault(col, set()).add(t)
    return {k: sorted(v) for k, v in out.items()}
