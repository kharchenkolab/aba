"""S4 — differential matrix, per-organ report, acceptance checks.

    python3 -m runner.matrix            # human-readable matrix + organ report
    python3 -m runner.matrix --json F   # machine-readable dump

Differential matrix: scenario x policy -> per-assertion pass/fail.
Acceptance (RUNNER_HANDOFF §4): every scenario must be FAILED by at least one
baseline; a scenario passed clean by all five baselines is non-discriminating
and gets flagged (the flag list feeds RUNNER_REPORT.md).

Per-organ report: assertion `kind` maps to an organ of editorial behavior —
routing, consent ceremony, plan lifecycle, salience floors, provenance,
structure — aggregated across scenarios so a failure reads "the routing organ
fails under policy X", not "scenario 7 red".
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict

from .baselines import BASELINES
from .cli import default_pools_root
from .grade import grade
from .scripted_good import POOL_ID as SG_POOL, SCENARIO_ID as SG_SCENARIO

ORGAN_OF_KIND = {
    "routing": "routing",
    "consent": "consent ceremony",
    "plan": "plan lifecycle",
    "salience": "salience floors",
    "provenance": "provenance",
    "structure": "structure",
}


def build_matrix(pools_root: str | None = None):
    """Replay + grade every (scenario, policy) pair.

    Returns {(pool, scenario): {policy: [GradedAssertion, ...]}} with the five
    baselines everywhere and scripted_good on its keyed scenario.
    """
    pools_root = pools_root or default_pools_root()
    matrix = {}
    for pool_name in sorted(os.listdir(pools_root)):
        pool_dir = os.path.join(pools_root, pool_name)
        scen_dir = os.path.join(pool_dir, "scenarios")
        if not os.path.isdir(scen_dir):
            continue
        for fn in sorted(os.listdir(scen_dir)):
            if not fn.endswith(".json"):
                continue
            sid = fn[:-5]
            cell = {}
            for pol in sorted(BASELINES):
                cell[pol] = grade(pool_dir, sid, pol)[0]
            if (pool_name, sid) == (SG_POOL, SG_SCENARIO):
                cell["scripted_good"] = grade(pool_dir, sid, "scripted_good")[0]
            matrix[(pool_name, sid)] = cell
    return matrix


def acceptance(matrix):
    """Non-discriminating scenarios: passed clean by ALL five baselines."""
    flags = []
    for (pool, sid), cell in sorted(matrix.items()):
        clean = all(all(g.passed for g in cell[pol]) for pol in BASELINES)
        if clean:
            flags.append((pool, sid))
    return flags


def per_organ(matrix):
    """{organ: {policy: (passed, total)}} across all scenarios."""
    agg = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for cell in matrix.values():
        for pol, graded in cell.items():
            for g in graded:
                organ = ORGAN_OF_KIND.get(g.kind, g.kind)
                agg[organ][pol][1] += 1
                if g.passed:
                    agg[organ][pol][0] += 1
    return {o: {p: tuple(v) for p, v in pols.items()}
            for o, pols in agg.items()}


def scenario_discrimination(matrix):
    """Per scenario: which baselines fail it (>=1 failed assertion)."""
    out = {}
    for key, cell in sorted(matrix.items()):
        out[key] = sorted(pol for pol in BASELINES
                          if not all(g.passed for g in cell[pol]))
    return out


def render(matrix):
    lines = []
    pols = sorted(BASELINES) + ["scripted_good"]
    lines.append("== differential matrix (passed/total per policy) ==")
    header = f"{'scenario':<42}" + "".join(f"{p[:14]:>16}" for p in pols)
    lines.append(header)
    for (pool, sid), cell in sorted(matrix.items()):
        row = f"{pool.split('-')[0]}/{sid:<32}"
        for pol in pols:
            graded = cell.get(pol)
            if graded is None:
                row += f"{'-':>16}"
            else:
                n = sum(1 for g in graded if g.passed)
                row += f"{f'{n}/{len(graded)}':>16}"
        lines.append(row)
    lines.append("")
    lines.append("== per-organ report (passed/total across scenarios) ==")
    organs = per_organ(matrix)
    for organ in sorted(organs):
        lines.append(f"-- {organ}")
        for pol in pols:
            if pol in organs[organ]:
                p, t = organs[organ][pol]
                lines.append(f"     {pol:<26} {p}/{t}")
    lines.append("")
    flags = acceptance(matrix)
    if flags:
        lines.append("!! NON-DISCRIMINATING scenarios (passed clean by all "
                     "five baselines):")
        for pool, sid in flags:
            lines.append(f"     {pool}/{sid}")
    else:
        lines.append("acceptance OK: every scenario is failed by >=1 baseline")
    disc = scenario_discrimination(matrix)
    weakest = min(disc.items(), key=lambda kv: (len(kv[1]), kv[0]))
    lines.append(f"scenario failed by fewest baselines: "
                 f"{weakest[0][0]}/{weakest[0][1]} "
                 f"({len(weakest[1])}/5 — 5/5 everywhere means maximal "
                 f"discrimination)")
    return "\n".join(lines)


def to_json(matrix):
    out = {}
    for (pool, sid), cell in sorted(matrix.items()):
        out[f"{pool}/{sid}"] = {
            pol: [{"index": g.index, "kind": g.kind, "passed": g.passed,
                   "predicate": g.predicate, "detail": g.detail}
                  for g in graded]
            for pol, graded in cell.items()}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="runner.matrix")
    ap.add_argument("--pools-root", default=None)
    ap.add_argument("--json", default=None,
                    help="also write the full matrix to this JSON file")
    args = ap.parse_args(argv)
    matrix = build_matrix(args.pools_root)
    print(render(matrix))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(to_json(matrix), fh, indent=1, sort_keys=True)
        print(f"\nwrote {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
