"""CLI — replay scenarios under policies.

Run from `record-eval/` (so `runner` is importable):

    python3 -m runner.cli replay pools/checkout-p99-lb-idle-timeout contradiction --policy never_restructure
    python3 -m runner.cli replay --all
    python3 -m runner.cli replay --all --twice     # determinism check

`--all` replays every scenario of every pool under all five baselines, plus
scripted_good on checkout-p99-lb-idle-timeout/contradiction, printing a
one-line summary per run.  `--twice` replays each run twice and verifies the
trace digests are identical.
"""

from __future__ import annotations

import argparse
import os
import sys

from .baselines import BASELINES
from .engine import ReplayEngine
from .events import load_all_scenarios, load_pool, load_scenario
from .scripted_good import POOL_ID as SG_POOL, SCENARIO_ID as SG_SCENARIO, ScriptedGood

POLICIES = dict(BASELINES)
POLICIES[ScriptedGood.name] = ScriptedGood


def default_pools_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(os.path.dirname(here), "pools")


def _resolve_pool_dir(arg: str, pools_root: str) -> str:
    if os.path.isdir(arg):
        return arg
    cand = os.path.join(pools_root, arg)
    if os.path.isdir(cand):
        return cand
    raise SystemExit(f"pool directory not found: {arg}")


def _fmt(summary: dict) -> str:
    p = summary["proposals"]
    rows = summary["rows"]
    return (f"{summary['pool']}/{summary['scenario']:<16} "
            f"{summary['policy']:<25} "
            f"ev={summary['events']:<3} ops={summary['ops_applied']:<3} "
            f"prop(o/a/x/d)={p['opened']}/{p['accepted']}/{p['expired']}/"
            f"{p['dismissed']} "
            f"sect={summary['sections_created']} "
            f"rows={rows['settled']}+{rows['pending']}p "
            f"plan={summary['plan_items']} "
            f"digest={summary['digest'][:12]}")


def _run(pool, scenario, policy_cls, snapshots=False) -> dict:
    engine = ReplayEngine(pool, scenario, policy_cls(), snapshots=snapshots)
    return engine.run().summary


def cmd_replay(args: argparse.Namespace) -> int:
    pools_root = args.pools_root or default_pools_root()
    failures = 0
    if args.all:
        runs = []
        for pool_name in sorted(os.listdir(pools_root)):
            pool_dir = os.path.join(pools_root, pool_name)
            if not os.path.isdir(pool_dir):
                continue
            pool = load_pool(pool_dir)
            for scenario in load_all_scenarios(pool_dir, pool):
                for name in sorted(BASELINES):
                    runs.append((pool, scenario, name))
                if pool.id == SG_POOL and scenario.id == SG_SCENARIO:
                    runs.append((pool, scenario, ScriptedGood.name))
        for pool, scenario, name in runs:
            summary = _run(pool, scenario, POLICIES[name])
            line = _fmt(summary)
            if args.twice:
                second = _run(pool, scenario, POLICIES[name])
                ok = second["digest"] == summary["digest"]
                line += "  det=OK" if ok else "  det=MISMATCH"
                failures += 0 if ok else 1
            print(line)
        print(f"-- {len(runs)} runs"
              + (f" · determinism failures: {failures}" if args.twice else ""))
        return 1 if failures else 0

    if not args.pool or not args.scenario:
        raise SystemExit("replay needs <pool-dir> <scenario> (or --all)")
    pool_dir = _resolve_pool_dir(args.pool, pools_root)
    pool = load_pool(pool_dir)
    spath = args.scenario
    if not os.path.isfile(spath):
        spath = os.path.join(pool_dir, "scenarios", args.scenario + ".json")
    scenario = load_scenario(spath, pool)
    summary = _run(pool, scenario, POLICIES[args.policy], snapshots=True)
    print(_fmt(summary))
    if args.twice:
        second = _run(pool, scenario, POLICIES[args.policy], snapshots=True)
        ok = second["digest"] == summary["digest"]
        print(f"determinism: {'OK' if ok else 'MISMATCH'}")
        return 0 if ok else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="runner.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rp = sub.add_parser("replay", help="replay scenario(s) under a policy")
    rp.add_argument("pool", nargs="?", help="pool dir or pool id")
    rp.add_argument("scenario", nargs="?", help="scenario id or path")
    rp.add_argument("--policy", default="never_restructure",
                    choices=sorted(POLICIES))
    rp.add_argument("--all", action="store_true",
                    help="replay all scenarios x all baselines (+ scripted_good "
                         "on its keyed scenario)")
    rp.add_argument("--twice", action="store_true",
                    help="replay each run twice and compare trace digests")
    rp.add_argument("--pools-root", default=None)
    rp.set_defaults(fn=cmd_replay)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
