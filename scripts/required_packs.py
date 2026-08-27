#!/usr/bin/env python
"""Which env packs does THIS deployment require? — the declared set.

The deploy gate used to compare the two published trees against each other:
for every pack in staging's catalog, is it in production's? A pack that was
never built sits in NEITHER, so the loop body never ran for it and the gate
printed "packs ok". That is the empty-subject-set failure — a
universally-quantified pass ("every staging pack is present") is satisfied by
having no packs to check.

Live consequence (2026-08-27): `python-bio-cuda` was derived into the bundle on
every stage and named by `site.yaml jobs.gpu_env_pack`, but its IMAGE was never
published to any tree. Two guards missed it from opposite sides — this one
compared tree to tree, and `base_env.gpu_pack_env_id()`'s `gpu_env_pack.unknown`
refusal reads the bundle SPEC, which deploy.sh always writes, so it can only
catch a config typo. The consequence would have surfaced as the first GPU job
solving a ~3.4 GB CUDA env on a node instead of adopting a published image.

So the gate needs the set the deployment DECLARES, computed by the app that
reads the bundle and site.yaml — not inferred from what happens to be on disk.
Run it inside the release image, where both are the deployed ones:

    apptainer exec <sif> /opt/aba-venv/bin/python \\
        /opt/aba/scripts/required_packs.py            # one "name role" per line

Exit 0 with at least one row, or exit 2 having explained why it could not tell.
NEVER exits 0 with an empty list: "nothing required" and "could not determine"
must not look alike to a shell caller, which is the whole bug being closed.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "backend"))


def _site_declared() -> list[tuple[str, str]]:
    """Packs named by the deployment's own site.yaml (ABA_SITE_CONFIG).

    Raises rather than returning [] when the file is named but unreadable: a
    deploy gate that cannot read the site file must not conclude the site
    declares nothing."""
    from core import config
    raw = (config.settings.site_config.get() or "").strip()
    if not raw:
        return []
    import pathlib as _pl

    import yaml
    sp = _pl.Path(raw).expanduser()
    if not sp.is_file():
        raise FileNotFoundError(f"site config {raw} is declared but missing")
    doc = yaml.safe_load(sp.read_text()) or {}
    out = []
    gpu = ((doc.get("jobs") or {}).get("gpu_env_pack") or "").strip()
    if gpu:
        out.append((gpu, "site.yaml jobs.gpu_env_pack"))
    return out


def required() -> list[tuple[str, str]]:
    """[(pack_name, why)] — every pack this deployment will ask for at runtime.

    Two sources, and both are runtime facts, not disk facts:
      * the base pack per language the bundle declares (`role: base`) — what
        every interactive session and CPU job adopts;
      * `jobs.gpu_env_pack`, which GPU-estimated background jobs ride.
    """
    out: list[tuple[str, str]] = []
    from core.compute import base_env, env_packs

    langs: set[str] = set()
    for row in env_packs.list_packs():
        for lang in (row.get("languages") or []):
            langs.add(str(lang).lower())
    for lang in sorted(langs):
        name = base_env.pack_name(lang)
        if name:
            out.append((name, f"base:{lang}"))

    # The SITE's declaration, read from the site file — not from the setting.
    # `jobs.gpu_env_pack` becomes ABA_JOBS_GPU_ENV_PACK only inside
    # install/ood/aba_preflight.py, i.e. only when a session is launched
    # through the OOD card. A deploy-time reader asking
    # `config.settings.gpu_env_pack` therefore sees NOTHING and concludes the
    # deployment declares no GPU pack — the same empty-set trap, one layer
    # down. Ask the file, then let an explicitly-set env var add to it.
    for name, why in _site_declared():
        out.append((name, why))
    from core import config
    try:
        env_gpu = (config.settings.gpu_env_pack.get() or "").strip() or None
    except Exception:  # noqa: BLE001 — an unreadable setting is not "no pack"
        raise
    if env_gpu:
        out.append((env_gpu, "ABA_JOBS_GPU_ENV_PACK"))

    # de-dupe, keep the first reason
    seen: dict[str, str] = {}
    for name, why in out:
        seen.setdefault(name, why)
    return sorted(seen.items())


def main() -> int:
    try:
        rows = required()
    except Exception as e:  # noqa: BLE001
        print(f"required_packs: could not determine the declared packs: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return 2
    if not rows:
        print("required_packs: this deployment declares NO env packs — that is "
              "either a bundle that failed to load or a site.yaml with no "
              "packs at all. Refusing to report an empty set as 'nothing to "
              "check'.", file=sys.stderr)
        return 2
    for name, why in rows:
        print(f"{name} {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
