"""Grade replay trajectories against the compiled corpus assertions.

    python3 -m runner.grade <pool-dir> <scenario> --policy <name>
    python3 -m runner.grade --all

Output columns are stable: pool/scenario  policy  passed/total  per-kind
breakdown — S4 builds the differential matrix on top of `grade()`.
"""

from __future__ import annotations

import argparse
import os
from collections import Counter
from dataclasses import dataclass

from .cli import POLICIES, default_pools_root, _resolve_pool_dir
from .compiled import registry
from .engine import GateViolation, ReplayEngine
from .events import load_pool, load_scenario
from .predicates import PREDICATES, Trajectory
from .scripted_good import POOL_ID as SG_POOL, SCENARIO_ID as SG_SCENARIO


@dataclass
class GradedAssertion:
    index: int
    kind: str
    predicate: str
    passed: bool
    detail: str
    reading_note: str


def grade(pool_dir: str, scenario_id: str, policy_name: str):
    """Replay one (pool, scenario, policy) and grade every compiled assertion.

    Returns (list[GradedAssertion], run summary dict).
    """
    pool = load_pool(pool_dir)
    scen_path = os.path.join(pool_dir, "scenarios", scenario_id + ".json")
    scenario = load_scenario(scen_path, pool)
    policy = POLICIES[policy_name]()
    compiled = registry().get((pool.id, scenario_id), [])
    try:
        result = ReplayEngine(pool, scenario, policy, snapshots=True).run()
    except GateViolation as exc:
        # a policy that trips the gate is GRADED, not crashed: every
        # assertion fails, with the consent organ carrying the diagnosis
        # (C2 fix — makes the consent predicates live for illegal policies)
        graded = [GradedAssertion(ca.index, ca.kind, ca.predicate, False,
                                  f"gate violation during replay: {exc}",
                                  ca.reading_note)
                  for ca in compiled]
        return graded, {"pool": pool.id, "scenario": scenario_id,
                        "policy": policy_name, "gate_violation": str(exc)}
    traj = Trajectory(result.trace)
    graded = []
    for ca in compiled:
        pred = PREDICATES[ca.predicate]
        try:
            v = pred(traj, pool, scenario, window=ca.window, **ca.params)
        except Exception as exc:  # a predicate crash is a grading bug
            v = type("V", (), {"passed": False,
                               "detail": f"PREDICATE ERROR: {exc!r}"})()
        graded.append(GradedAssertion(ca.index, ca.kind, ca.predicate,
                                      v.passed, v.detail, ca.reading_note))
    return graded, result.summary


def _fmt_line(pool_id, scenario_id, policy_name, graded):
    n = sum(1 for g in graded if g.passed)
    kinds = Counter()
    for g in graded:
        if g.passed:
            kinds[g.kind] += 1
    kd = ",".join(f"{k}:{v}" for k, v in sorted(kinds.items()))
    return (f"{pool_id}/{scenario_id:<17} {policy_name:<25} "
            f"{n}/{len(graded)}  {kd}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="runner.grade")
    ap.add_argument("pool", nargs="?")
    ap.add_argument("scenario", nargs="?")
    ap.add_argument("--policy", default="never_restructure")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--pools-root", default=None)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    pools_root = args.pools_root or default_pools_root()

    if args.all:
        for pool_name in sorted(os.listdir(pools_root)):
            pool_dir = os.path.join(pools_root, pool_name)
            scen_dir = os.path.join(pool_dir, "scenarios")
            if not os.path.isdir(scen_dir):
                continue
            for fn in sorted(os.listdir(scen_dir)):
                if not fn.endswith(".json"):
                    continue
                sid = fn[:-5]
                pols = sorted(POLICIES)
                for pol in pols:
                    if pol == "scripted_good" and \
                            (pool_name, sid) != (SG_POOL, SG_SCENARIO):
                        continue
                    graded, _ = grade(pool_dir, sid, pol)
                    print(_fmt_line(pool_name, sid, pol, graded))
        return 0

    if not args.pool or not args.scenario:
        ap.error("pool and scenario required (or --all)")
    pool_dir = _resolve_pool_dir(args.pool, pools_root)
    graded, summary = grade(pool_dir, args.scenario, args.policy)
    pool_name = os.path.basename(pool_dir.rstrip("/"))
    print(_fmt_line(pool_name, args.scenario, args.policy, graded))
    for g in graded:
        mark = "PASS" if g.passed else "FAIL"
        print(f"  [{g.index}] {mark} {g.kind:<10} {g.predicate:<24} {g.detail}")
        if args.verbose and g.reading_note:
            print(f"        note: {g.reading_note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
