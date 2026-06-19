"""aba bundle CLI: introspect the active EffectiveBundle.

Subcommands:
  inspect              Pretty-print the resolved scope chain + composition
                       summary (default subcommand if none given).
  inspect --json       Machine-readable JSON for tooling / dashboards.
  inspect --reload     Drop the cache, re-resolve, then print.

Run: python -m core.bundle.cli inspect
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
from typing import Any

from core.bundle.active import (
    get_bundle, get_resolution, reload_bundle,
)
from core.bundle.loader import EffectiveBundle, format_effective_bundle
from core.bundle.scope_resolver import (
    ScopeResolution, format_resolution,
)


def _state_dict(r: ScopeResolution, eb: EffectiveBundle) -> dict[str, Any]:
    """Build the JSON-serializable state dict consumed by the CLI's
    --json mode and the /api/bundle/state route."""
    scope_chain = [
        {
            "name": s.name,
            "label": s.label,
            "path": str(s.path) if s.path else None,
            "present": s.present,
            "optional": s.optional,
        }
        for s in r.scope_chain
    ]
    required_counts: dict[str, int] = {}
    for fname, scopes in eb.provenance.required_files.items():
        required_counts[fname] = len(scopes)
    n_shadowed = sum(1 for v in eb.provenance.overrideable_files.values()
                      if v.get("shadowed_in"))
    n_disabled = sum(1 for v in eb.provenance.skills.values()
                      if v.get("disabled"))
    n_agent_filtered = sum(1 for v in eb.provenance.skills.values()
                            if v.get("skipped_reason"))
    return {
        "user": r.user,
        "group": r.group,
        "scope_chain": scope_chain,
        "state_dir": str(r.state_dir),
        "scratch_dir": str(r.scratch_dir) if r.scratch_dir else None,
        "site_config": str(r.site_config) if r.site_config else None,
        "composed_bundle": str(r.composed_bundle) if r.composed_bundle else None,
        "summary": {
            "policy_scopes": list(eb.provenance.policy_scopes),
            "required_rules": required_counts,
            "overrideable_rules": {
                "total": len(eb.overrideable_rules),
                "shadowed": n_shadowed,
            },
            "skills": {
                "total": len(eb.skills),
                "disabled": n_disabled,
                "agent_filtered": n_agent_filtered,
            },
            "settings_top_level_keys": list(eb.settings.keys()),
        },
        "warnings": list(eb.provenance.warnings),
        "errors": list(eb.provenance.errors),
    }


def _print_pretty(r: ScopeResolution, eb: EffectiveBundle) -> None:
    """Human-friendly output: scope chain on top, composition below."""
    print(format_resolution(r))
    print()
    print(format_effective_bundle(eb))


def _cmd_inspect(args: argparse.Namespace) -> int:
    if args.reload:
        eb = reload_bundle()
        r = get_resolution()
    else:
        eb = get_bundle()
        r = get_resolution()
    if args.json:
        print(json.dumps(_state_dict(r, eb), indent=2))
    else:
        _print_pretty(r, eb)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="aba-bundle",
        description="Introspect the active ABA bundle.",
    )
    sub = p.add_subparsers(dest="cmd")

    p_inspect = sub.add_parser(
        "inspect",
        help="Pretty-print the resolved scope chain + composition.",
    )
    p_inspect.add_argument("--json", action="store_true",
                            help="Machine-readable JSON.")
    p_inspect.add_argument("--reload", action="store_true",
                            help="Force re-resolution (drop the cache).")
    p_inspect.set_defaults(func=_cmd_inspect)

    args = p.parse_args(argv)
    if not args.cmd:
        # Default: inspect with no flags.
        args.json = False
        args.reload = False
        return _cmd_inspect(args)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
