#!/usr/bin/env python3
"""Validate a Record eval pool directory: schema, DAG, scenario orderings, quality bar.

Usage: python3 validate_pool.py record-eval/pools/<pool-id>

Stdlib only. Exit 0 = clean, 1 = errors (warnings alone don't fail).
"""
import json
import sys
from pathlib import Path

GESTURE_VERBS = {"pin", "expand", "check", "fade", "corroborate",
                 "alternatives", "plan", "draft_claim", "hold"}
EVENT_TYPES = {"session_start", "finding", "gesture", "instruction",
               "ratify", "dismiss", "distill", "clock"}
ASSERT_KINDS = {"structure", "routing", "consent", "salience", "plan", "provenance"}
STRENGTHS = {"weak", "moderate", "strong"}
EVIDENCE_KINDS = {"figure", "table", "stat", "run"}

errors, warnings = [], []


def err(msg): errors.append(msg)
def warn(msg): warnings.append(msg)


def load(path):
    try:
        return json.loads(path.read_text())
    except Exception as e:
        err(f"{path.name}: unparseable JSON ({e})")
        return None


def check_pool(pool):
    qids = {q["id"] for q in pool.get("questions", [])}
    if len(qids) < 3:
        warn(f"only {len(qids)} questions (spec suggests 3-5)")
    findings = pool.get("findings", [])
    fids = [f.get("id") for f in findings]
    if len(fids) != len(set(fids)):
        err("duplicate finding ids")
    fset = set(fids)

    n_multi = n_none = n_overturn_edges = 0
    strength_counts = {s: 0 for s in STRENGTHS}
    for f in findings:
        fid = f.get("id", "?")
        for key in ("claim", "evidence", "strength"):
            if key not in f:
                err(f"{fid}: missing '{key}'")
        if f.get("strength") not in STRENGTHS:
            err(f"{fid}: bad strength {f.get('strength')!r}")
        else:
            strength_counts[f["strength"]] += 1
        ev = f.get("evidence", {})
        if ev.get("kind") not in EVIDENCE_KINDS:
            err(f"{fid}: bad evidence.kind {ev.get('kind')!r}")
        if not ev.get("caption"):
            err(f"{fid}: evidence missing caption")
        if not ev.get("values"):
            warn(f"{fid}: evidence has no values")
        qs = f.get("questions", [])
        for q in qs:
            if q not in qids:
                err(f"{fid}: unknown question {q!r}")
        if len(qs) >= 2:
            n_multi += 1
        if len(qs) == 0:
            n_none += 1
        for edge_key in ("depends_on", "overturns"):
            for ref in f.get(edge_key, []):
                if ref not in fset:
                    err(f"{fid}: {edge_key} references unknown {ref!r}")
        n_overturn_edges += len(f.get("overturns", []))

    # DAG acyclicity + longest chain (depends_on only)
    deps = {f["id"]: list(f.get("depends_on", [])) for f in findings if "id" in f}
    depth, state = {}, {}  # state: 1=visiting 2=done

    def visit(node, stack):
        if state.get(node) == 1:
            err(f"depends_on cycle: {' -> '.join(stack + [node])}")
            return 0
        if state.get(node) == 2:
            return depth[node]
        state[node] = 1
        d = 1 + max((visit(p, stack + [node]) for p in deps.get(node, [])), default=0)
        state[node] = 2
        depth[node] = d
        return d

    longest = max((visit(fid, []) for fid in deps), default=0)

    n = len(findings)
    if not (25 <= n <= 50):
        warn(f"{n} findings (spec targets 30-45)")
    if n and n_multi / n < 0.20:
        warn(f"only {n_multi}/{n} multi-question findings (spec: >=20%)")
    if not (2 <= n_none <= 4):
        warn(f"{n_none} no-question findings (spec: 2-4)")
    if not (2 <= n_overturn_edges <= 4):
        warn(f"{n_overturn_edges} overturns edges (spec: 2-4)")
    if longest < 5:
        warn(f"longest depends_on chain is {longest} (spec: >=5)")

    print(f"  {n} findings, {len(qids)} questions, "
          f"strengths {strength_counts}, multi-q {n_multi}, no-q {n_none}, "
          f"overturns {n_overturn_edges}, longest chain {longest}")
    return fset, deps


def check_scenario(path, scen, fset, deps):
    sid = scen.get("id", path.stem)
    if scen.get("id") != path.stem:
        warn(f"{path.name}: id {scen.get('id')!r} != filename")
    seen = set()
    for e in scen.get("events", []):
        et = e.get("type")
        if et not in EVENT_TYPES:
            err(f"{sid}: bad event type {et!r}")
        if et == "finding":
            ref = e.get("ref")
            if ref not in fset:
                err(f"{sid}: unknown finding {ref!r}")
            else:
                missing = [p for p in deps.get(ref, []) if p not in seen]
                if missing:
                    err(f"{sid}: {ref} arrives before its dependencies {missing}")
                seen.add(ref)
        if et == "gesture":
            if e.get("verb") not in GESTURE_VERBS:
                err(f"{sid}: bad gesture verb {e.get('verb')!r}")
            if e.get("target") not in fset:
                err(f"{sid}: gesture targets unknown {e.get('target')!r}")
        if et == "instruction" and not e.get("expect"):
            warn(f"{sid}: instruction without 'expect'")
        if et in ("ratify", "dismiss") and not e.get("target"):
            err(f"{sid}: {et} missing 'target'")
    if not scen.get("assertions"):
        err(f"{sid}: no assertions")
    for a in scen.get("assertions", []):
        if a.get("kind") not in ASSERT_KINDS:
            err(f"{sid}: bad assertion kind {a.get('kind')!r}")
        if not a.get("at") or not a.get("expect"):
            err(f"{sid}: assertion missing 'at' or 'expect'")
    if not scen.get("stresses"):
        err(f"{sid}: missing 'stresses'")
    print(f"  {sid}: {len(scen.get('events', []))} events, "
          f"{len(seen)} findings used, {len(scen.get('assertions', []))} assertions")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    pool_dir = Path(sys.argv[1])
    pool_file = pool_dir / "pool.json"
    if not pool_file.exists():
        sys.exit(f"no pool.json in {pool_dir}")
    print(f"pool: {pool_dir.name}")
    pool = load(pool_file)
    if pool is None:
        report(); return
    fset, deps = check_pool(pool)

    scen_files = sorted((pool_dir / "scenarios").glob("*.json"))
    if len(scen_files) < 6:
        warn(f"only {len(scen_files)} scenarios (spec: >=6)")
    scen_ids = {p.stem for p in scen_files}
    for mandatory in ("contradiction", "proactive-intent"):
        if mandatory not in scen_ids:
            err(f"mandatory scenario missing: {mandatory}")
    for p in scen_files:
        scen = load(p)
        if scen is not None:
            check_scenario(p, scen, fset, deps)
    report()


def report():
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print(f"{len(errors)} errors, {len(warnings)} warnings")
    sys.exit(1 if errors else 0)


main()
