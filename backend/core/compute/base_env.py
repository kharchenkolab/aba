"""Default (base) language environments from env packs — weft rewrite W3.0.

The deployment's DEFAULT environment per language stops being "the venv the
backend runs in" and becomes a bundle-declared **base pack** (envs/ facet,
`role: base`, `languages: [...]`) solved/realized by the compute substrate.
This module is the generic resolver the default lanes consult:

    base_env.active("python")        → is a base pack declared + substrate up?
    base_env.interpreter("python")   → realized <prefix>/bin/python (or Rscript)

DATA-DRIVEN cutover, no feature flag: a deployment whose bundle declares base
packs runs them; one that declares none keeps the served base (the backend's
own env — today's behavior, and the controller-only deploys of the future
simply always declare packs). Domain stays content: the platform knows
"role: base pack for language L"; only the pack YAML names actual libraries.

Requirements on pack content (loud, documented errors — never silent):
  * a python base pack MUST include `ipykernel` (the kernel runs as the pack's
    python; weft envs are frozen — nothing is ever installed into one);
  * an R base pack MUST include `r-irkernel` for interactive use.

Sync by design (called from tool worker threads / the one-shot run path);
resolution + solve are cached in-process per pack spec, realization is weft's
(memoized; a GC-reclaimed prefix rebuilds transparently). Callers on the
kernel path must realize BEFORE taking the pool lock (see run_exec) — a
first-use realize under the lock would wedge every kernel acquisition.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from core.compute import adapter as _adapter
from core.compute import env_packs, named_envs
from core.compute.errors import ComputeError

_ROLE = "base"

# (language, pack_name, spec_digest) → env_id. Spec-keyed so a bundle reload
# that changes the pack re-solves; process-lifetime otherwise.
_env_ids: dict[tuple[str, str, str], str] = {}
_warned: set[str] = set()


def _base_packs(language: str) -> list[tuple[str, dict]]:
    from core.bundle.active import get_bundle
    out = []
    for p in get_bundle().env_packs:
        doc = p.spec
        role = str(doc.get("role") or "").strip().lower()
        langs = [str(x).lower() for x in (doc.get("languages") or [])]
        if role == _ROLE and language.lower() in langs:
            out.append((p.name, doc))
    return sorted(out, key=lambda t: t[0])


def pack_name(language: str) -> Optional[str]:
    """The declared base pack for a language, or None (→ served base). More
    than one declared base for the same language is a content bug — we pick
    deterministically (sorted-first) and warn once."""
    packs = _base_packs(language)
    if not packs:
        return None
    if len(packs) > 1 and language not in _warned:
        _warned.add(language)
        print(f"[base_env] {len(packs)} base packs declare language "
              f"{language!r} ({[n for n, _ in packs]}) — using {packs[0][0]!r}; "
              f"scopes should override by NAME, not add parallel bases")
    return packs[0][0]


def active(language: str) -> bool:
    """True iff a base pack is declared for `language` AND the substrate is up.
    A declared pack with the substrate down is a DEPLOYMENT fault — surfaced
    where the interpreter is asked for (ComputeError), not silently downgraded
    to the served base (which may not even hold the science stack)."""
    return pack_name(language) is not None


def env_id(language: str) -> Optional[str]:
    """The base pack's EnvID: ADOPT from the deployment's published catalog
    when one is configured (no solve — the managed-cluster model, W3.1), else
    solve locally (cached). None when no pack is declared. Raises ComputeError
    on substrate-offline / solve failure — the structured cause reaches the
    agent; nothing degrades silently (a catalog MISS prints loudly and solves
    privately, per the weft doctrine)."""
    name = pack_name(language)
    if name is None:
        return None
    spec = env_packs.pack_spec(name) or {}
    digest = json.dumps(spec, sort_keys=True, default=str)
    key = (language, name, str(hash(digest)))
    if key in _env_ids:
        return _env_ids[key]
    from core.compute import seeding
    eid = seeding.adopt_env_id(name)
    if eid is None:
        res = named_envs._sync(_adapter.get_compute().env_ensure(spec))
        eid = res["env_id"]
    _env_ids[key] = eid
    return eid


def prefix(language: str, *, timeout_s: int = 1800) -> Optional[Path]:
    """The realized base prefix on the local site (realizing on first use), or
    None when no pack is declared."""
    eid = env_id(language)
    if eid is None:
        return None
    return named_envs.ensure_realized(eid, timeout_s=timeout_s, language=language)


def interpreter(language: str, *, timeout_s: int = 1800) -> Optional[Path]:
    """The base interpreter for `language` (python | Rscript), or None when the
    deployment declares no base pack (→ caller uses the served base)."""
    p = prefix(language, timeout_s=timeout_s)
    if p is None:
        return None
    exe = p / "bin" / ("Rscript" if language.lower() == "r" else "python")
    if not exe.exists():
        raise ComputeError(
            "env.realize_failed",
            f"base pack {pack_name(language)!r} realized without "
            f"{exe.name} at {exe}", stage="realize",
            hints={"fix": f"the {language} base pack must include the "
                          f"{'r-base' if language.lower() == 'r' else 'python'} runtime"})
    return exe


def reset_cache() -> None:
    """Test/reload hook: drop cached EnvIDs (e.g. after a bundle reload)."""
    _env_ids.clear()
    _warned.clear()
