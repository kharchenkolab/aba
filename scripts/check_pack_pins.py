#!/usr/bin/env python
"""Promote gate for the single env store: is every DECLARED pack pinned to a
version that actually exists?

WHAT IT REPLACES. The old gate compared two published trees: for every pack in
staging's catalog, is that version present in production's? It failed three
different ways in one day.

  * A pack in NEITHER tree was never iterated, so the loop body never ran and
    the gate printed "packs ok" — `python-bio-cuda` was named by site.yaml,
    derived into the bundle on every stage, and never published anywhere.
    A universally-quantified pass is satisfied by an empty subject set.
  * It compared version STRINGS. A pack can carry the right version and still
    be unusable: on 2026-08-27 the production copies were byte-copies of
    staging's squashfs images, which bake their own absolute prefix, so they
    activated only at the staging path. Same version, same EnvID, dead.
  * Once the two trees became one, it compared production against a leftover
    directory and refused a correct promote.

WHAT IT CHECKS INSTEAD. Three questions the single-store model actually has:

  1. What does this deployment DECLARE it needs? (scripts/required_packs.py —
     base pack per language plus site.yaml `jobs.gpu_env_pack`.) Empty answer
     is a FAILURE, never a pass.
  2. For each, does the version it PINS exist in the store? Unpinned means
     `latest`, which must also exist.
  3. Is production pinned to something staging actually exercised? Staging
     rides `latest`, so a production pin behind `latest` is drift — a
     judgement call for the operator, not an automatic refusal.

Exit codes match the old gate's contract, which deploy.sh depends on:
    0  fine
    1  drift — overridable with --yes (an operator may know why)
    2  cannot work — never overridable
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

OK, DRIFT, FATAL = 0, 1, 2


def load_catalog(tree: str) -> dict:
    p = Path(tree) / "catalog.json"
    return json.loads(p.read_text())["envs"]


def parse_pins(raw: str) -> dict:
    """`{a: 1, b: 2}` (inline YAML, as versions.env carries it) → dict."""
    raw = (raw or "").strip()
    if not raw or raw == "{}":
        return {}
    import yaml
    out = yaml.safe_load(raw)
    if not isinstance(out, dict):
        raise ValueError(f"pins must be a mapping, got {type(out).__name__}")
    return {str(k): str(v) for k, v in out.items()}


def check(tree: str, pins: dict, declared: list[tuple[str, str]]) -> tuple[int, list[str]]:
    """-> (exit_code, lines). Pure: the tests drive this directly."""
    lines: list[str] = []
    if not declared:
        return FATAL, ["PRE-FLIGHT FAIL: this deployment declares no env packs — "
                       "a bundle that failed to load looks exactly like this. "
                       "Refusing to read an empty set as 'nothing to check'."]
    try:
        envs = load_catalog(tree)
    except Exception as e:  # noqa: BLE001
        return FATAL, [f"PRE-FLIGHT FAIL: cannot read the env store at {tree} "
                       f"({type(e).__name__}: {e}) — nothing was verified."]

    rc = OK
    for name, why in declared:
        entry = envs.get(name)
        if entry is None:
            lines.append(f"PACK MISSING  {name} ({why}): nothing published in {tree}")
            rc = max(rc, FATAL)
            continue
        versions = entry.get("versions") or {}
        latest = entry.get("latest")
        want = pins.get(name, "latest")
        resolved = latest if want == "latest" else want
        if resolved not in versions:
            lines.append(f"PIN UNPUBLISHED  {name}: pinned to {want!r} "
                         f"({resolved!r}) which is not in the store "
                         f"[have: {', '.join(sorted(versions)) or 'nothing'}]")
            rc = max(rc, FATAL)
            continue
        if want == "latest":
            lines.append(f"   packs ok    {name}: latest -> {resolved}")
        elif resolved == latest:
            lines.append(f"   packs ok    {name}: pinned {resolved} (== latest)")
        else:
            lines.append(f"   packs DRIFT {name}: pinned {resolved}, store latest "
                         f"is {latest} — staging exercises latest, so this pin "
                         f"ships something staging did not run")
            rc = max(rc, DRIFT)
    return rc, lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--tree", required=True, help="the shared env store")
    ap.add_argument("--pins", default="{}", help="inline YAML pin mapping")
    args = ap.parse_args()
    try:
        pins = parse_pins(args.pins)
    except Exception as e:  # noqa: BLE001
        print(f"PRE-FLIGHT FAIL: unreadable pins {args.pins!r}: {e}", file=sys.stderr)
        return FATAL
    try:
        from required_packs import required
        declared = required()
    except Exception as e:  # noqa: BLE001
        print(f"PRE-FLIGHT FAIL: cannot determine declared packs "
              f"({type(e).__name__}: {e})", file=sys.stderr)
        return FATAL
    rc, lines = check(args.tree, pins, declared)
    for ln in lines:
        print(ln)
    if rc == FATAL:
        print("\n   Promote would leave the deployment adopting a pack that is "
              "not there. Publish it into the store first:\n"
              "     ./deploy.sh publish-packs --target prod --packs <names>")
    elif rc == DRIFT:
        print("\n   --yes to promote onto a pin staging did not exercise.")
    return rc


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
