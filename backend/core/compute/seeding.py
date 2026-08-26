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
                       latest: bool = True,
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
                                                  version=ver, staging=staging,
                                                  latest=latest))
            rows.append({"pack": name, "env_id": eid, "version": ver,
                         "published": True, "detail": pub})
        except ComputeError as e:
            rows.append({"pack": name, "error": e.to_payload()})
    return rows


def mirror_base_packs(*, site: str, src_tree: str, dest_tree: str,
                      packs: Optional[list[str]] = None,
                      latest: bool = True,
                      staging: Optional[str] = None) -> list[dict]:
    """Move ALREADY-PUBLISHED packs from one catalog tree to another, by
    IDENTITY — the promote-the-tested-artifact path.

    `publish_base_packs` is spec-first: spec → env_ensure → SOLVE → env_id. It
    mints a NEW environment every time, so it cannot move a tested one. The
    EnvID it produces depends on the solving weft (an older one emits `env:v1`
    where a newer emits `env:v2`) and on the day's resolution. Publishing a
    staging pack to production that way ships something that merely shares a
    NAME with what was tested — observed 2026-08-26, caught by comparing
    EnvIDs before the catalog pointer moved.

    This is artifact-first. `env_adopt` reads the source tree's lock sidecar
    and registers that exact EnvID locally ("from the stored lock — NO
    solving, no index access"); `env_publish` then builds it into the
    destination FROM THAT LOCK. Same identity by construction. The source's
    version string travels too, so both trees name the artifact identically.

    Pass `latest=False` when the destination is serving users: consumers adopt
    `latest`, so moving the pointer before the app that was tested with it is
    a live change to a deployment nobody has re-tested.
    """
    ad = _adapter.get_compute()
    staging = staging or config.settings.weft_publish_staging.get()
    try:
        cat = named_envs._sync(ad.env_published(site, src_tree))
    except ComputeError as e:
        return [{"error": f"source catalog unreadable: {e.to_payload()}"}]
    # `env_published` returns {envs: {name: {latest, versions: {ver: {...}}}}}
    # — the catalog shape, enriched per version (is_latest, state_here,
    # runnable_here). The LATEST version per name is what the source
    # deployment actually serves, and therefore what was tested against it.
    src: dict = {}
    for nm, entry in (cat.get("envs") or {}).items():
        ver = (entry or {}).get("latest")
        if nm and ver:
            src[nm] = ver
    names = packs if packs is not None else sorted(src)
    rows: list[dict] = []
    for name in names:
        ver = src.get(name)
        if not ver:
            rows.append({"pack": name, "error": f"not published in {src_tree}"})
            continue
        try:
            # identity first: adopt registers the SOURCE's env_id + lock
            eid = named_envs._sync(ad.env_adopt(site, src_tree, name))["env_id"]
            # Carry the IMAGE across before publishing. weft's publish reuses an
            # image already present at {tree}/envs/<env-id-hash> ("image is
            # content-addressed and stays") and only rewrites the sidecars —
            # which is what we want anyway, since sidecars must not carry the
            # source tree's paths. Without this it re-runs mksquashfs, which
            # costs ~700 MB of repackaging AND is simply absent in some
            # environments (the release image has no /usr/sbin/mksquashfs).
            # Local trees only: a remote destination goes through weft's own
            # transfer plane, so leave that to publish.
            _copied = _copy_image(src_tree, dest_tree, eid)
            pub = named_envs._sync(ad.env_publish(eid, site, dest_tree, name,
                                                  version=ver, staging=staging,
                                                  latest=latest))
            rows.append({"pack": name, "env_id": eid, "version": ver,
                         "published": True, "image_copied": _copied,
                         "detail": pub})
        except ComputeError as e:
            rows.append({"pack": name, "error": e.to_payload()})
    return rows


def _copy_image(src_tree: str, dest_tree: str, env_id: str) -> bool:
    """Copy a published image directory between two LOCAL catalog trees.

    Returns True if it copied, False if the destination already had it or
    either tree is not a local directory. Never raises: a failed copy just
    means publish does the work the slow way."""
    import shutil
    from pathlib import Path as _P
    try:
        h = env_id.rsplit(":", 1)[-1]
        src = _P(src_tree) / "envs" / h
        dst = _P(dest_tree) / "envs" / h
        if not src.is_dir() or dst.exists():
            return False
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        shutil.copytree(src, tmp, symlinks=True)
        tmp.rename(dst)          # atomic: a half-copied image never looks ready
        return True
    except Exception:  # noqa: BLE001
        return False


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
