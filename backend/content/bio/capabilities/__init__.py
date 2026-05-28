"""Bio capability seed: registers the seed-catalog loader with core.catalog.

Capabilities are per-project entities, so they can't be loaded at import time
(no project DB is active yet). Instead we register a *seed provider* callback;
core.catalog invokes it lazily the first time a project's catalog is queried
and found empty (dependency inversion — content registers into core, not the
reverse).
"""
from __future__ import annotations
from pathlib import Path

from core.catalog import register_capability, register_seed_provider, register_collection_dir

_SEED_DIR = Path(__file__).parent

# Extracted reference catalogues (collections.md). These are file-backed and
# process-global (not per-project entities), so register the dir at import time.
# biomni is an EXTRACTED catalogue (mined offline) — not a runtime dependency.
_COLLECTIONS_DIR = _SEED_DIR.parent / "collections"
for _cdir in sorted(p for p in _COLLECTIONS_DIR.glob("*") if p.is_dir()):
    register_collection_dir(_cdir)


def load_seed() -> int:
    """Upsert seed capabilities from *.yaml into the active project's catalog.
    Idempotent: skips any (name, version) already present. Returns count added."""
    import yaml
    from core.catalog import resolve_capability

    added = 0
    for yf in sorted(_SEED_DIR.glob("*.yaml")):
        try:
            doc = yaml.safe_load(yf.read_text()) or {}
        except Exception:  # noqa: BLE001
            continue
        for spec in doc.get("capabilities", []):
            name, ver = spec.get("name"), str(spec.get("version", ""))
            existing = resolve_capability(name)
            if existing and str(existing.get("version", "")) == ver:
                continue
            register_capability(spec)
            added += 1
    return added


register_seed_provider(load_seed)
