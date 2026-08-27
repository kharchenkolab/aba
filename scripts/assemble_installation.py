#!/usr/bin/env python
"""Assemble a deployment's installation bundle: image content + site overlay.

THE RULE. A deployment's installation bundle is assembled by whatever installs
that deployment, and it is COMPLETE. The image ships content; it never names
the path. A personal install already works this way — `~/.aba/installation`
carries the base env specs, the derived GPU spec, catalog, knowhow and skills
together, assembled by the installer. The SIF deployment was the odd one out.

WHAT WENT WRONG WITHOUT IT. `install/sif/build.sh` baked
`ABA_INSTITUTION_BUNDLE=/opt/aba/installation` into the image's %environment,
so the image's bundle (which holds ONLY `envs/` with the two base specs)
permanently occupied the institution slot and `site.yaml`'s
`scopes.institution.bundle_path` could never take effect. Everything staged to
the share was therefore written and never read: the derived GPU pack — which is
why it could not be published for months — and the site's reference-source
catalogues too. The build script's own comment said site.yaml was meant to
override it; nothing ever exported it.

WHAT THIS DOES. Rebuilds `dest` from scratch, image content first, then the
site's own tree overlaid on top (site wins on conflict), so one bundle carries
everything and the same code path serves staging and production — only $SHARE
differs.

IDEMPOTENT by construction: `dest` is replaced, never merged into, so a removed
file upstream disappears here too and the bundle cannot accrete stale content.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


class AssemblyError(RuntimeError):
    pass


def assemble(image_bundle: Path, site_overlay: Path | None, dest: Path) -> dict:
    """-> {'from_image': [rel…], 'from_site': [rel…], 'overridden': [rel…]}.

    Raises rather than producing a thin bundle: an image bundle that is missing
    or carries no files means the deployment would come up with no base env
    packs at all, and every session would silently solve a private base instead
    of adopting the published image. That is precisely the failure this whole
    mechanism exists to prevent, so it must be loud."""
    image_bundle = Path(image_bundle)
    if not image_bundle.is_dir():
        raise AssemblyError(f"image bundle not found at {image_bundle}")
    img_files = sorted(p for p in image_bundle.rglob("*") if p.is_file())
    if not img_files:
        raise AssemblyError(
            f"image bundle at {image_bundle} carries no files — refusing to "
            f"assemble an installation bundle with no base env packs")

    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(image_bundle, dest)
    from_image = [str(p.relative_to(image_bundle)) for p in img_files]

    from_site: list[str] = []
    overridden: list[str] = []
    if site_overlay and Path(site_overlay).is_dir():
        site_overlay = Path(site_overlay)
        for src in sorted(p for p in site_overlay.rglob("*") if p.is_file()):
            rel = src.relative_to(site_overlay)
            if rel.name == "README.md" and rel.parent == Path("."):
                continue                      # the overlay's own doc, not content
            tgt = dest / rel
            if tgt.exists():
                overridden.append(str(rel))
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, tgt)
            from_site.append(str(rel))
    return {"from_image": from_image, "from_site": from_site,
            "overridden": overridden, "dest": str(dest)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image-bundle", required=True,
                    help="the image's installation content (the SOURCE)")
    ap.add_argument("--site-overlay", default=None,
                    help="the deployment's own config/installation, overlaid on top")
    ap.add_argument("--dest", required=True, help="$SHARE/installation")
    a = ap.parse_args()
    try:
        r = assemble(Path(a.image_bundle),
                     Path(a.site_overlay) if a.site_overlay else None,
                     Path(a.dest))
    except AssemblyError as e:
        print(f"assemble_installation: {e}", file=sys.stderr)
        return 2
    print(f"   installation bundle → {r['dest']}")
    print(f"     {len(r['from_image'])} file(s) from the image, "
          f"{len(r['from_site'])} from the site"
          + (f", {len(r['overridden'])} overridden" if r["overridden"] else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
