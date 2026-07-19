"""Bio capability seed: projects the composed bundle's catalog into core.catalog.

The capability catalog is soft-config like skills and policy: it lives in each
scope's `catalog/` dir (system → installation → lab → user) and is composed by
`core.bundle.loader` into `EffectiveBundle.catalog` (capability specs,
narrowest-wins by name), `EffectiveBundle.r_base_specs` (the curated shared
R-base conda list), and `EffectiveBundle.collection_dirs` (file-backed
reference collections like biomni). This module just turns those projections
into live catalog state — the exact mirror of `content.bio.skills`
(`register_from_bundle`). No second discovery, no local seed dir.

Capabilities are per-project entities, so the seed can't run at import (no
project DB is active yet): `load_seed` is registered as a *seed provider* and
invoked lazily the first time a project's catalog is queried and found empty
(dependency inversion — content registers into core). Collections are
file-backed + process-global, so they register at import.

The system scope's catalog content is materialized under
`system_bundle/catalog/` (bio_seed.yaml + r_base.yaml + biomni/), the same way
`system_bundle/skills/` holds the materialized skills.
"""
from __future__ import annotations

from core.catalog import register_capability, register_seed_provider, register_collection_dir


def register_collections_from_bundle() -> int:
    """Register the bundle's file-backed collection dirs (biomni, …) for search.
    Idempotent (register_collection_dir dedupes). Returns the count registered."""
    from core.bundle.active import get_bundle
    n = 0
    for cdir in get_bundle().collection_dirs:
        register_collection_dir(cdir)
        n += 1
    return n


def load_seed() -> int:
    """Upsert the bundle's composed capability catalog into the active project's
    catalog. Idempotent: skips any (name, version) already present. Returns the
    count added. Registered as a core.catalog seed provider (runs lazily, once
    per project DB)."""
    from core.bundle.active import get_bundle
    from core.catalog import resolve_capability

    added = 0
    for entry in get_bundle().catalog:
        spec = entry.spec
        name, ver = spec.get("name"), str(spec.get("version", ""))
        existing = resolve_capability(name)
        if existing and str(existing.get("version", "")) == ver:
            continue
        register_capability(spec)
        added += 1
    return added


# Collections are file-backed + process-global → register at import.
register_collections_from_bundle()
# Capabilities are per-project → seed lazily on first catalog query.
register_seed_provider(load_seed)
