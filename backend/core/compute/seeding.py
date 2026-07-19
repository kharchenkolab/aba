"""Deploy-time seeding of the published base-env catalog — weft rewrite W3.1.

The OOD/cluster ownership model (misc/weft_rewrite.md §4b): an ADMIN publishes
each base pack into a versioned, immutable catalog on shared storage
(`env_publish` — squashfs where the site supports it); USERS adopt by name
with **no solve** (`env_adopt` reads the stored lock), and project deltas
overlay the adopted read-only base via `extends_env`. Upgrades publish
alongside and flip `latest` — running jobs keep their immutable parent.

This module is the aba-side seeding driver the installer / an operator calls:

    from core.compute import seeding
    seeding.publish_base_packs(site="hpc", tree="/shared/aba/envs")

and the consumer hook `adopt_env_id(pack)` that base_env uses when the
deployment configures a published tree (ABA_WEFT_PUBLISH_TREE): adopt by name,
falling back LOUDLY to a private solve when the catalog misses (weft doctrine —
broken/absent base → loud miss → private build, never a silent wrong env).

Sync, worker-thread callable. Domain stays content: packs come from the
bundle's envs/ facet; nothing here names a library.
"""
from __future__ import annotations

import time
from typing import Optional

from core import config
from core.compute import adapter as _adapter
from core.compute import env_packs, named_envs
from core.compute.errors import ComputeError


def _version_for(spec: dict) -> str:
    """Catalog version for a publish: date + a short spec digest — sortable,
    collision-safe when the same day publishes twice."""
    import hashlib
    import json
    d = hashlib.sha256(json.dumps(spec, sort_keys=True, default=str)
                       .encode()).hexdigest()[:8]
    return time.strftime("%Y.%m.%d") + "-" + d


def publish_base_packs(*, site: str, tree: str,
                       packs: Optional[list[str]] = None,
                       version: Optional[str] = None,
                       staging: Optional[str] = None) -> list[dict]:
    """Solve + publish every base-role pack (or the named subset) into the
    catalog `tree` on `site`. Idempotent per version (weft refuses duplicate
    version pointers). Returns one row per pack: {pack, env_id, version,
    published|error}.

    `staging` (build-churn location) defaults to the `weft_publish_staging`
    setting, else weft's 'auto' (under the site root) — the key lever when
    `tree` is slow netfs: the tree then receives ONE sequential image write
    instead of ~10^4 small-file ops (see config.weft_publish_staging)."""
    ad = _adapter.get_compute()
    staging = staging or config.settings.weft_publish_staging.get()
    rows: list[dict] = []
    names = packs if packs is not None else [
        r["name"] for r in env_packs.list_packs() if r.get("role") == "base"]
    for name in names:
        spec = env_packs.pack_spec(name)
        if spec is None:
            rows.append({"pack": name, "error": "unknown pack"})
            continue
        ver = version or _version_for(spec)
        try:
            res = named_envs._sync(ad.env_ensure(spec))
            eid = res["env_id"]
            pub = named_envs._sync(ad.env_publish(eid, site, tree, name,
                                                  version=ver, staging=staging))
            rows.append({"pack": name, "env_id": eid, "version": ver,
                         "published": True, "detail": pub})
        except ComputeError as e:
            rows.append({"pack": name, "error": e.to_payload()})
    return rows


def published_catalog(*, site: Optional[str] = None,
                      tree: Optional[str] = None) -> dict:
    """Render-complete `published:v1` rows for the configured (or given)
    catalog — the data behind a Modules-style UI."""
    ad = _adapter.get_compute()
    site = site or config.settings.weft_publish_site.get()
    tree = tree or config.settings.weft_publish_tree.get()
    if not tree:
        return {"tree": None, "rows": []}
    return named_envs._sync(ad.env_published(site, tree))


def adopt_env_id(pack_name: str) -> Optional[str]:
    """Adopt `pack_name` from the deployment's published catalog → EnvID (no
    solve). None when no catalog is configured (→ caller solves locally).
    Adoption failure is a LOUD miss returning None — the caller's private
    solve is the documented degradation, never a silent substitute identity
    (the print names what happened)."""
    tree = config.settings.weft_publish_tree.get()
    if not tree:
        return None
    site = config.settings.weft_publish_site.get()
    ad = _adapter.get_compute()
    try:
        res = named_envs._sync(ad.env_adopt(site, tree, pack_name))
        return res["env_id"]
    except ComputeError as e:
        print(f"[seeding] adopt of base pack {pack_name!r} from {tree} MISSED "
              f"({e.code}: {e.detail}) — falling back to a private solve")
        return None
