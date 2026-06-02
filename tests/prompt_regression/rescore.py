"""Re-apply BEHAVIORS predicates to ALREADY-CAPTURED trajectories — no API calls.

Use this after adding/fixing a scorer to update rates without re-running the
matrix. Trajectories live under results/raw/<ts>/<case>__<variant>/repNN.json,
each carrying the full turn sequence (`turns`) — enough to reconstruct
`code`/`outcome`/`reads` and run any BEHAVIORS predicate.

  python rescore.py <results_dir>
  python rescore.py results/raw/20260531_083712 --cases methodvalidity__cluster_de_deseq
"""
import argparse, glob, json, os, re, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, "/workspace/aba/backend")
from harness import BEHAVIORS, _RECIPE_APIS, _declared_recipes_from_plan  # noqa: E402


def _reconstruct_trace(rec: dict) -> dict:
    """From a captured record, rebuild the dict shape that BEHAVIORS predicates expect."""
    turns = rec.get("turns") or []
    reads = []
    code_chunks: list[str] = []
    declared = list(rec.get("declared_recipes") or [])
    outcome = rec.get("outcome", "")
    for t in turns:
        if t.get("role") != "assistant": continue
        for b in t.get("blocks") or []:
            if b.get("type") != "tool_use": continue
            name = b.get("name", "")
            inp = b.get("input") or {}
            if name in ("Skill", "read_skill", "search_skills"):
                reads.append(inp.get("skill") or inp.get("name") or inp.get("query") or "")
            if name in ("run_python", "run_r"):
                cc = inp.get("code", "") or ""
                if cc: code_chunks.append(cc)
            if name == "present_plan" and not declared:
                declared = _declared_recipes_from_plan(inp)
    code = "\n".join(code_chunks)
    # plan/code/read step indices: walk steps (assistant turns) and find which carries which
    steps_kinds = []
    for t in turns:
        if t.get("role") != "assistant": continue
        steps_kinds.append([b.get("name") for b in t.get("blocks") or [] if b.get("type") == "tool_use"])
    def first_idx(pred):
        for i, s in enumerate(steps_kinds):
            if pred(s): return i
        return None
    return {
        "outcome": outcome, "code": code, "reads": reads, "steps": steps_kinds,
        "declared_recipes": declared,
        "read_step": first_idx(lambda s: any(n in ("Skill", "read_skill", "search_skills") for n in s)),
        "plan_step": first_idx(lambda s: "present_plan" in s),
        "code_step": first_idx(lambda s: any(n in ("run_python", "run_r") for n in s)),
        # for non-deterministic-tracked behaviors we'd need the case below; supplied per-case
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="results/raw/<ts>/ directory with case__variant/ subdirs")
    ap.add_argument("--cases", default="all", help="comma-list of case ids, or 'all'")
    ap.add_argument("--variants", default="all", help="comma-list of variant labels, or 'all'")
    ap.add_argument("--behaviors", default=None, help="comma-list (default: every behavior in the harness)")
    a = ap.parse_args()
    base = a.dir if os.path.isabs(a.dir) else os.path.join(os.path.dirname(__file__), a.dir)
    if not os.path.isdir(base):
        sys.exit(f"not a directory: {base}")
    case_ids = None if a.cases == "all" else set(a.cases.split(","))
    var_labels = None if a.variants == "all" else set(a.variants.split(","))
    behaviors = list(BEHAVIORS) if not a.behaviors else a.behaviors.split(",")

    # Group reps by (case_id, variant_label)
    cells: dict = defaultdict(list)
    for cell_dir in sorted(glob.glob(os.path.join(base, "*__*"))):
        name = os.path.basename(cell_dir)
        # split "case_id__variant_label" — case ids may contain underscores, so split
        # on the LAST `__` (variant labels never contain it by convention).
        idx = name.rfind("__")
        if idx == -1: continue
        cid, vlabel = name[:idx], name[idx + 2:]
        if case_ids and cid not in case_ids: continue
        if var_labels and vlabel not in var_labels: continue
        for rep_file in sorted(glob.glob(os.path.join(cell_dir, "rep*.json"))):
            cells[(cid, vlabel)].append(rep_file)

    if not cells:
        sys.exit(f"no matching trajectories under {base}")

    # Re-score every cell
    print(f"# Rescoring {sum(len(v) for v in cells.values())} reps across {len(cells)} cells\n")
    cases_by_id: dict = {}
    HERE = os.path.dirname(__file__)
    for cf in glob.glob(os.path.join(HERE, "cases", "*.json")):
        c = json.load(open(cf))
        cases_by_id[c["id"]] = c

    by_case = defaultdict(dict)
    for (cid, vlabel), files in sorted(cells.items()):
        case = cases_by_id.get(cid) or {"id": cid, "target_recipe": None}
        # case.behaviors restricts which to compute, like in run_case
        names = case.get("behaviors") or behaviors
        # filter against what's actually in BEHAVIORS now
        names = [b for b in names if b in BEHAVIORS]
        rates = {b: 0 for b in names}
        outcomes = defaultdict(int)
        n = 0
        for rep_file in files:
            rec = json.load(open(rep_file))
            t = _reconstruct_trace(rec)
            outcomes[t["outcome"]] += 1
            for b in names:
                if BEHAVIORS[b](t, case): rates[b] += 1
            n += 1
        rates = {b: round(v / max(1, n), 3) for b, v in rates.items()}
        by_case[cid][vlabel] = {"rates": rates, "n": n, "outcomes": dict(outcomes)}

    for cid, vs in by_case.items():
        print(f"=== {cid} ===")
        for v, r in vs.items():
            rate_str = "  ".join(f"{b}={x}" for b, x in r["rates"].items())
            print(f"  {v:32} {rate_str}   outcomes={r['outcomes']}")
        print()


if __name__ == "__main__":
    main()
