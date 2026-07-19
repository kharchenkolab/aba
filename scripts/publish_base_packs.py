#!/usr/bin/env python3
"""Publish this deployment's base env packs (role: base) to a shared tree as
content-addressed squashfs images, so users ADOPT prebuilt images by name instead of
each solving + realizing their own (docs/arch — the weft publish/adopt path).

Operator step, run ONCE per pack version from a node that can build squashfs (login
node here works; run inside an allocation if the login node refuses FUSE/userns):

    ABA_HOME=<home> backend-python scripts/publish_base_packs.py \
        --tree /groups/<lab>/aba-envs [--site local] [--packs python-bio r-bio] \
        [--staging /dev/shm/pubstage]

Then set on the deployment so users adopt by name (no solve):
    ABA_WEFT_PUBLISH_TREE=<tree>  ABA_WEFT_PUBLISH_SITE=<site>
and list <tree> in each consumer site's `ro_roots` (weft-sites.yaml) so the image
mounts read-only in place. The tree MUST live outside any weft root.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))


def main() -> int:
    ap = argparse.ArgumentParser(description="Publish base env packs to a shared tree.")
    ap.add_argument("--tree", required=True, help="shared catalog tree (outside any weft root)")
    ap.add_argument("--site", default=os.environ.get("ABA_WEFT_PUBLISH_SITE", "local"),
                    help="site that builds + hosts the catalog (default: local / $ABA_WEFT_PUBLISH_SITE)")
    ap.add_argument("--packs", nargs="*", default=None,
                    help="pack names to publish (default: all role:base packs)")
    ap.add_argument("--staging", default=os.environ.get("ABA_WEFT_PUBLISH_STAGING"),
                    help="fast build-churn dir for netfs trees, e.g. /dev/shm/pubstage "
                         "(default: $ABA_WEFT_PUBLISH_STAGING)")
    ap.add_argument("--version", default=None, help="override the auto date+digest version")
    args = ap.parse_args()

    import core.compute as cc
    cc.configure()
    from core.compute import seeding

    rows = seeding.publish_base_packs(site=args.site, tree=args.tree, packs=args.packs,
                                      version=args.version, staging=args.staging)
    ok = 0
    for r in rows:
        pub = r.get("published")
        ok += bool(pub)
        d = r.get("detail") or {}
        mb = (d.get("image_bytes") or 0) / 1e6
        print(f"  {'✓' if pub else '✗'} {r.get('pack'):16} {r.get('version','')}  "
              f"{mb:.0f} MB  staging={ (d.get('staging') or {}).get('used') }"
              + ("" if pub else f"  ERROR: {r.get('error') or r.get('detail')}"))
    print(f"\n{ok}/{len(rows)} published to {args.tree}")
    return 0 if ok == len(rows) and rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
