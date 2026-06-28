"""Reference-source catalog (misc/refs.md §5.1).

The *data* half of `fetch_reference`: provider manifests describing where
standard reference data + pre-built indices live, and how to resolve a request
(organism/assembly/role, or an accession) to a concrete asset. Kept as YAML
(recipe-pack-style) so the churny, ever-growing source knowledge is NOT
hardcoded — the agent and the recipe pack can extend it without a code change.

Two manifest kinds:
  - `manifest`  — an explicit asset list (role × organism × assembly → url).
                  Used by pre-built-index providers (aws-indexes, 10x, …).
  - `template`  — parametric resolution from an accession (a CLI/URL template).
                  Used by NCBI Datasets / Ensembl-FTP-style providers.

Discovery + layering (production): the provider catalog is a first-class BUNDLE
category. The loader composes each scope's `knowhow/refsources/*.yaml` into
`EffectiveBundle.refsources` — override by provider name, narrowest scope wins,
exactly like `catalog` (see core/bundle/loader._compose_refsources). So the
layering is:
  system seed (system_bundle/knowhow/refsources/)
    → recipe pack / institution overlay (extends + overrides providers)
    → group → project
  + $ABA_REFSOURCES_DIR — operator/test escape hatch, applied on top here.
This module does NO scope layering of its own — it consumes the composed map.
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

import yaml

def _env_override_providers() -> dict[str, dict]:
    """Operator escape hatch: ``$ABA_REFSOURCES_DIR`` overrides/extends any
    provider ad-hoc, without touching the scope chain. A provider declared here
    wins over the composed bundle (used by tests + one-off operator overrides)."""
    out: dict[str, dict] = {}
    env = os.environ.get("ABA_REFSOURCES_DIR")
    if not env:
        return out
    d = Path(env)
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.yaml")):
        try:
            m = yaml.safe_load(f.read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        name = m.get("provider")
        if name:
            out[name] = m
    return out


def load_providers() -> dict[str, dict]:
    """All provider manifests, keyed by ``provider``.

    The scope-layered catalog is composed ONCE by the bundle loader
    (``EffectiveBundle.refsources`` — system seed → recipe pack → institution
    → …, override-by-provider-name, narrowest wins). This consumer just reads
    that map and applies the ``$ABA_REFSOURCES_DIR`` operator override on top.
    No layering logic lives here — see core/bundle/loader._compose_refsources."""
    from core.bundle.active import get_bundle
    out = dict(get_bundle().refsources)
    out.update(_env_override_providers())          # operator override wins
    return out


def _match_facet_asset(assets, role, organism, assembly):
    """First asset matching normalized facets (refs.md): role/assembly fold
    case+separators; organism also accepts aliases AND a substring match
    ('phiX174' ⊃ 'phix'; 'drosophila' ⊂ 'drosophila_melanogaster'); the assembly
    accession is the strong key, organism is fuzzy. None if no match. Shared by
    the `manifest` (url) and `local` (path) kinds."""
    from core.data.refstore import _norm_organism, _norm_facet
    nq_role, nq_org, nq_asm = _norm_facet(role), _norm_organism(organism), _norm_facet(assembly)

    def _org_ok(av):
        if not nq_org:
            return True
        na = _norm_organism(av)
        return bool(na) and (na == nq_org or na in nq_org or nq_org in na)

    for a in assets or []:
        if nq_role and _norm_facet(a.get("role")) != nq_role:
            continue
        if not _org_ok(a.get("organism")):
            continue
        if nq_asm and _norm_facet(a.get("assembly")) != nq_asm:
            continue
        return a
    return None


def resolve_asset(
    provider: str,
    *,
    organism: Optional[str] = None,
    assembly: Optional[str] = None,
    role: Optional[str] = None,
    accession: Optional[str] = None,
    filename: Optional[str] = None,
) -> dict:
    """Resolve a request to a concrete asset for `provider`.

    Returns a dict with `url` (fetchable now), `command` (a CLI the agent runs),
    or `path` (a pre-existing on-cluster file → adopt via link), plus
    `unpack`/`version`/facets. Raises ValueError on an unknown provider, an
    unsupported role, or no matching asset."""
    provs = load_providers()
    m = provs.get(provider)
    if not m:
        raise ValueError(f"unknown refsource provider {provider!r} "
                         f"(have: {sorted(provs)})")
    kind = m.get("kind", "manifest")

    if kind == "manifest":
        a = _match_facet_asset(m.get("assets"), role, organism, assembly)
        if a:
            return {"provider": provider, "kind": "manifest",
                    "url": a.get("url"), "unpack": a.get("unpack"),
                    "version": a.get("version"), "role": a.get("role"),
                    "organism": a.get("organism"), "assembly": a.get("assembly")}
        raise ValueError(
            f"{provider}: no asset for role={role} organism={organism} "
            f"assembly={assembly}")

    if kind == "local":
        # Pre-existing on-cluster reference store (refs.md §5.1): assets carry a
        # filesystem `path` instead of a url, so fetch_reference adopts it in
        # place (register mode=link) — no download, no copy. Lets an institution
        # overlay expose a site's mirrored references as first-class providers.
        a = _match_facet_asset(m.get("assets"), role, organism, assembly)
        if a:
            return {"provider": provider, "kind": "local",
                    "path": a.get("path"), "version": a.get("version"),
                    "role": a.get("role"), "organism": a.get("organism"),
                    "assembly": a.get("assembly")}
        raise ValueError(
            f"{provider}: no local asset for role={role} organism={organism} "
            f"assembly={assembly}")

    if kind == "template":
        roles = m.get("roles") or {}
        if role and role not in roles:
            raise ValueError(f"{provider} cannot supply role {role!r} "
                             f"(has: {sorted(roles)})")
        if not accession:
            raise ValueError(f"{provider} (template) needs an `accession`")
        params = {"accession": accession,
                  "filename": filename or f"{accession}.zip",
                  **(roles.get(role) or {})}
        spec = {"provider": provider, "kind": "template",
                "unpack": m.get("unpack"), "version": accession,
                "role": role, "accession": accession}
        cmd = m.get("command")
        if cmd:
            spec["command"] = cmd.format(**params)
        url = m.get("url_template")
        if url:
            spec["url"] = url.format(**params)
        return spec

    raise ValueError(f"{provider}: unknown manifest kind {kind!r}")
