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

Search path (first match wins, so overrides layer cleanly):
  1. $ABA_REFSOURCES_DIR              — operator / test override
  2. <built-in seed in the repo>      — content/bio/knowhow/refsources/
  (3. installation-scope refsources/  — wired in Phase 1 with the recipe pack)
"""
from __future__ import annotations
import os
from pathlib import Path
from typing import Optional

import yaml

# refsources.py is at backend/core/data/ → parents[2] is backend/.
_BUILTIN = (Path(__file__).resolve().parents[2]
            / "content" / "bio" / "knowhow" / "refsources")


def _search_dirs() -> list[Path]:
    dirs: list[Path] = []
    env = os.environ.get("ABA_REFSOURCES_DIR")
    if env:
        dirs.append(Path(env))
    dirs.append(_BUILTIN)
    return [d for d in dirs if d.is_dir()]


def load_providers() -> dict[str, dict]:
    """All provider manifests, keyed by `provider`. Earlier search dirs win."""
    out: dict[str, dict] = {}
    for d in _search_dirs():
        for f in sorted(d.glob("*.yaml")):
            try:
                m = yaml.safe_load(f.read_text()) or {}
            except (OSError, yaml.YAMLError):
                continue
            name = m.get("provider")
            if name and name not in out:
                out[name] = m
    return out


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

    Returns a dict with EITHER `url` (fetchable now) OR `command` (a CLI the
    agent runs), plus `unpack`/`version`/facets. Raises ValueError on an unknown
    provider, an unsupported role, or no matching asset."""
    provs = load_providers()
    m = provs.get(provider)
    if not m:
        raise ValueError(f"unknown refsource provider {provider!r} "
                         f"(have: {sorted(provs)})")
    kind = m.get("kind", "manifest")

    if kind == "manifest":
        # Match normalized facets (refs.md) so the agent's natural inputs hit:
        # role/assembly fold case+separators; organism also accepts aliases AND a
        # substring match ('phiX174' ⊃ 'phix'; 'drosophila' ⊂ 'drosophila_melanogaster').
        # The assembly accession is the strong key; organism is fuzzy.
        from core.data.refstore import _norm_organism, _norm_facet
        nq_role, nq_org, nq_asm = _norm_facet(role), _norm_organism(organism), _norm_facet(assembly)

        def _org_ok(av):
            if not nq_org:
                return True
            na = _norm_organism(av)
            return bool(na) and (na == nq_org or na in nq_org or nq_org in na)

        for a in m.get("assets") or []:
            if nq_role and _norm_facet(a.get("role")) != nq_role:
                continue
            if not _org_ok(a.get("organism")):
                continue
            if nq_asm and _norm_facet(a.get("assembly")) != nq_asm:
                continue
            return {"provider": provider, "kind": "manifest",
                    "url": a.get("url"), "unpack": a.get("unpack"),
                    "version": a.get("version"), "role": a.get("role"),
                    "organism": a.get("organism"), "assembly": a.get("assembly")}
        raise ValueError(
            f"{provider}: no asset for role={role} organism={organism} "
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
