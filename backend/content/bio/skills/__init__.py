"""Bio skills registrar — materializes the live skill catalog as a PROJECTION
of the composed bundle (core.bundle), ABA's single soft-config system.

`core.bundle.loader` is the one place skills are discovered + composed: it walks
each present scope's `skills/` tree (system → installation → lab → user),
assigns the visibility tier (skills/core/* → 'always', else 'local') + recipe
domain, applies narrowest-wins precedence, the `agents:` filter, and
`disable_recipes` — producing `EffectiveBundle.skills`. This module just turns
each composed `Skill` into a searchable `SkillSpec` in the in-process registry
(core.skills): no second discovery, no content-layers/deployment.yaml.

Skill CONTENT (.md) lives in each scope's `skills/` dir; the system scope's is
`system_bundle/skills` → `content/bio/library` (core/ + recipes/<domain>/ +
vendor_skills/<pkg>/).
"""
import os
from pathlib import Path


def register_from_bundle(*, clear: bool = False) -> dict[str, int]:
    """Project EffectiveBundle.skills into the live catalog. Returns a
    {scope: count} dict for diagnostics."""
    from core.bundle.active import get_bundle, get_resolution
    from core.skills.loader import _spec_from_parsed, register_skill_spec, clear_registry
    if clear:
        clear_registry()
    # Project broadest→narrowest (system → installation → lab → user) so a
    # narrower scope's alias-override (a skill declaring aliases:[base-name])
    # registers AFTER the base it hijacks.
    order = {s.name: i for i, s in enumerate(get_resolution().scope_chain)}
    by_scope: dict[str, int] = {}
    for sk in sorted(get_bundle().skills, key=lambda s: (order.get(s.source_scope, 99), s.name)):
        try:
            spec = _spec_from_parsed(
                sk.frontmatter, sk.body,
                source_path=str(sk.path.parent if sk.is_folder else sk.path),
                default_domain=sk.domain, visibility=sk.visibility,
                layer=sk.source_scope, kind=sk.kind)
        except ValueError as e:
            print(f"[skills] skip {sk.path}: {e}", flush=True)
            continue
        resources: tuple = ()
        if sk.is_folder:                       # bundle resources = folder siblings
            folder = sk.path.parent
            resources = tuple(sorted(
                str(p.resolve())
                for dp, _dn, fns in os.walk(folder, followlinks=True)
                for p in (Path(dp) / fn for fn in fns)
                if p.is_file() and p.name != "SKILL.md"))
        register_skill_spec(spec, resources)
        by_scope[sk.source_scope] = by_scope.get(sk.source_scope, 0) + 1
    print(f"[skills] from bundle: {sum(by_scope.values())} skills {dict(by_scope)}",
          flush=True)
    return by_scope


# Register on import: project the composed bundle into the live catalog.
register_from_bundle()
