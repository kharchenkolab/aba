"""S5 — permutation fuzzing: random dependency-closed orderings.

    python3 -m runner.fuzz [--n 50] [--seed 7] [--policies a,b,...]

The authored scenarios are the behaviorally-targeted core; the pools' DAGs
admit far more legal orderings.  This harness generates N random
dependency-closed event streams per pool (respecting `depends_on` — the
topological constraint is reimplemented here, standalone from
validate_pool.py per RUNNER_HANDOFF §4), replays them under the baselines,
and checks the ORDER-INDEPENDENT families:

- gate: the replay completes without GateViolation (the two invariants hold
  on arbitrary orderings, not just the authored ones);
- consent arithmetic: no Class-2/3/X op applied without consent; decisions
  never auto-accept (synthetic streams contain no ratify events, so every
  decision-tier proposal must still be pending/expired at end);
- routing: every finding lands exactly once, citations sit in valid strata,
  and touched-set consistency holds (a finding cited under a question's story
  makes its sitting findable from that question);
- provenance: superseded findings remain findable (never deleted), and
  supersession marks only pair findings that actually landed.

Determinism: a seeded random.Random drives generation; the same seed yields
byte-identical streams and trace digests.
"""

from __future__ import annotations

import argparse
import os
import random

from .baselines import BASELINES
from .cli import default_pools_root
from .engine import GateViolation, ReplayEngine
from .events import Event, Scenario, load_pool
from .predicates import Trajectory, consent_conservation

FAMILIES = ("gate", "consent", "routing", "provenance")


def random_ordering(pool, rng):
    """A uniform-ish random topological order of all findings (Kahn's
    algorithm with random tie-breaking) — standalone dependency-closure
    logic, not imported from validate_pool.py."""
    deps = {f.id: set(f.depends_on) for f in pool.findings}
    dependents = {}
    for f in pool.findings:
        for d in f.depends_on:
            dependents.setdefault(d, set()).add(f.id)
    ready = sorted(fid for fid, ds in deps.items() if not ds)
    order = []
    while ready:
        fid = ready.pop(rng.randrange(len(ready)))
        order.append(fid)
        for child in sorted(dependents.get(fid, ())):
            deps[child].discard(fid)
            if not deps[child]:
                ready.append(child)
    assert len(order) == len(pool.findings), "cycle in pool DAG?"
    return order


def synth_scenario(pool, rng, sid):
    """Wrap a random ordering into a plausible event stream: sittings of 2-8
    findings anchored at a random tagged question, with distills and clock
    gaps between them, and occasional mid-sitting distills."""
    order = random_ordering(pool, rng)
    events = []
    t = 1
    i = 0
    qids = list(pool.question_ids)

    def emit(etype, **kw):
        nonlocal t
        events.append(Event(index=len(events), t=t, type=etype,
                            anchor=kw.get("anchor"), ref=kw.get("ref"),
                            background=kw.get("background", False),
                            verb=None, target=None, text=None, note=None,
                            expect=None, advance_days=kw.get("advance_days", 0)))
        t += 1

    while i < len(order):
        chunk = order[i:i + rng.randint(2, 8)]
        i += len(chunk)
        first = pool.finding(chunk[0])
        anchor = rng.choice(list(first.questions) or qids)
        emit("session_start", anchor=anchor)
        for j, fid in enumerate(chunk):
            emit("finding", ref=fid)
            if j == 2 and rng.random() < 0.3:
                emit("distill")          # mid-sitting "distill so far"
        if rng.random() < 0.8:
            emit("distill")
        emit("clock", advance_days=rng.randint(1, 3))
    return Scenario(id=sid, pool_id=pool.id, stresses="fuzz",
                    description="synthetic dependency-closed ordering",
                    events=tuple(events), assertions=())


def check_order_independent(result, pool):
    """Returns {family: [violation strings]} for one replayed trajectory."""
    v = {f: [] for f in FAMILIES}
    traj = Trajectory(result.trace)
    snap = result.trace.entries[-1].get("snapshot") or {}

    # consent arithmetic
    cc = consent_conservation(traj, pool, None)
    if not cc.passed:
        v["consent"].append(cc.detail)
    for p in snap.get("proposals", {}).values():
        if p.get("status") == "accepted" and p.get("cls") in ("3", "X"):
            v["consent"].append(
                f"decision-tier proposal {p['id']} accepted in a stream "
                f"with no ratify events")

    # routing
    landed = snap.get("findings", {})
    if len(landed) != len(pool.findings):
        v["routing"].append(
            f"{len(landed)}/{len(pool.findings)} findings landed")
    for f in landed.values():
        for c in f.get("citations", []):
            if c.get("stratum") not in ("story", "notes", "sediment"):
                v["routing"].append(f"{f['id']} in bad stratum "
                                    f"{c.get('stratum')}")
    # touched-set CONSISTENCY (policy-agnostic): if a sitting's finding was
    # actually cited under a question's story, the sitting must be findable
    # from that question.  (Superset-of-tags is an honest-routing property,
    # not a substrate invariant — inert legitimately touches nothing.)
    sittings = snap.get("sittings", {})
    for f in landed.values():
        sid = f.get("sitting_id")
        if not sid or sid not in sittings:
            continue
        touched = set(sittings[sid].get("touched", []))
        for c in f.get("citations", []):
            q = c.get("question")
            if c.get("stratum") == "story" and q and q not in touched:
                v["routing"].append(
                    f"{f['id']} cited under {q} but sitting {sid} "
                    f"not findable from it")

    # provenance
    for f in landed.values():
        by = f.get("superseded_by")
        if by:
            if by not in landed:
                v["provenance"].append(
                    f"{f['id']} superseded by unlanded {by}")
            if not f.get("citations"):
                v["provenance"].append(
                    f"superseded {f['id']} lost all citations (deleted)")
    return v


def fuzz_pool(pool_dir, n, seed, policies):
    pool = load_pool(pool_dir)
    counts = {pol: {f: 0 for f in FAMILIES} for pol in policies}
    digests = {}
    for k in range(n):
        rng = random.Random((seed, pool.id, k).__repr__())
        scen = synth_scenario(pool, rng, f"fuzz-{k:03d}")
        for pol in policies:
            try:
                result = ReplayEngine(pool, scen, BASELINES[pol](),
                                      snapshots=True).run()
            except GateViolation as exc:
                counts[pol]["gate"] += 1
                digests[(k, pol)] = f"GATE: {exc}"
                continue
            for fam, viols in check_order_independent(result, pool).items():
                if viols:
                    counts[pol][fam] += 1
            digests[(k, pol)] = result.trace.run_digest()
    return counts, digests


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="runner.fuzz")
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--policies", default=None,
                    help="comma list (default: all five baselines)")
    ap.add_argument("--pools-root", default=None)
    args = ap.parse_args(argv)
    pools_root = args.pools_root or default_pools_root()
    policies = (args.policies.split(",") if args.policies
                else sorted(BASELINES))
    total_viol = 0
    for pool_name in sorted(os.listdir(pools_root)):
        pool_dir = os.path.join(pools_root, pool_name)
        if not os.path.isdir(os.path.join(pool_dir, "scenarios")):
            continue
        counts, _ = fuzz_pool(pool_dir, args.n, args.seed, policies)
        print(f"== {pool_name}: {args.n} random orderings, seed={args.seed}")
        for pol in policies:
            row = "  ".join(f"{fam}:{counts[pol][fam]}" for fam in FAMILIES)
            bad = sum(counts[pol].values())
            total_viol += bad
            flag = "" if bad == 0 else "  <-- VIOLATIONS"
            print(f"   {pol:<26} {row}{flag}")
    print(f"\ntotal runs with violations: {total_viol}")
    return 1 if total_viol else 0


if __name__ == "__main__":
    raise SystemExit(main())
