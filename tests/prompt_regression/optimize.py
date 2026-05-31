"""Optimization sweep — the suite as an engine for a *succinct AND effective* prompt.

Two operations:
  --ablate   : drop each context block, measure Δbehavior + Δsize vs baseline.
               No contract moves -> CRUFT (cut, succinctness win). A contract drops
               -> LOAD-BEARING (keep + protect). The measured #310.
  --variants : run named rephrase/placement/interaction hypotheses (from variants.py)
               against the corpus, same Δbehavior + Δsize report + Pareto verdict.

Accept rule (Pareto): adopt iff all contracts hold within --noise AND a behavior rises
OR size drops. Hypotheses are authored in variants.py; ablation is generated automatically.

The whole sweep (baseline + every block/variant) runs as ONE parallel matrix (run_matrix),
not N serial passes.
"""
import os, sys, glob, json, argparse
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, "/workspace/aba/backend")
from harness import run_matrix, render_system
from variants import VARIANTS

HERE = os.path.dirname(__file__)


def _size(case, variant):
    return len(render_system(case, variant)[0])


def _load_cases(spec):
    cfs = (sorted(glob.glob(os.path.join(HERE, "cases", "*.json"))) if spec == "all"
           else [os.path.join(HERE, "cases", c + ".json") for c in spec.split(",")])
    return [json.load(open(f)) for f in cfs]


def _rates(matrix, label):
    return {cid: matrix[cid][label]["rates"] for cid in matrix}


def _report(label, base, cur, dsize, noise):
    deltas, worst = {}, 0.0
    for cid, rates in cur.items():
        for b, x in rates.items():
            d = round(x - base.get(cid, {}).get(b, 0.0), 3)
            if abs(d) >= 0.05:
                deltas[f"{cid}/{b}"] = d
            worst = min(worst, d)
    holds = worst >= -noise
    gain = (dsize < 0) or any(d > noise for d in deltas.values())
    verdict = "ADOPT" if (holds and gain) else ("regresses" if not holds else "no-gain")
    print(f"  {label:24} Δsize={dsize:+6}  worst_Δbehavior={worst:+.2f}  -> {verdict}   {deltas or ''}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["ablate", "variants"], default="ablate")
    ap.add_argument("--cases", default="all")
    ap.add_argument("--blocks", default=None, help="comma-list to restrict the ablation sweep (default: all primary blocks)")
    ap.add_argument("--reps", type=int, default=12)
    ap.add_argument("--noise", type=float, default=0.15)
    ap.add_argument("--workers", type=int, default=12, help="concurrent rollouts across the whole matrix")
    a = ap.parse_args()
    cases = _load_cases(a.cases)

    if a.mode == "ablate":
        import content.bio.prompts.build as B
        blocks = [b.name for b in B._BLOCKS if b.roles is None or "primary" in b.roles]
        if a.blocks:
            want = [b.strip() for b in a.blocks.split(",")]
            unknown = [b for b in want if b not in blocks]
            if unknown:
                ap.error(f"unknown block(s): {unknown}; known primary blocks: {blocks}")
            blocks = want
        variant_items = [("current", {})] + [(f"ablate:{b}", {"ablate": [b]}) for b in blocks]
    else:
        variant_items = [("current", {})]
        for name, v in VARIANTS.items():
            if name == "current":
                continue
            try:                                  # skip variants whose sys_sub anchor is stale
                render_system(cases[0], v)
                variant_items.append((name, v))
            except Exception as e:                # noqa: BLE001
                print(f"  {name:24} SKIP ({e})")

    matrix = run_matrix(cases, variant_items, reps=a.reps, workers=a.workers)
    base = _rates(matrix, "current")
    base_size = _size(cases[0], {})
    print(f"=== baseline (current): size={base_size} chars, n={a.reps} ===")
    for cid, r in base.items():
        print(f"  {cid}: {r}")
    header = "ABLATION sweep (cut a block; Δ vs baseline)" if a.mode == "ablate" else "VARIANT hypotheses (Δ vs baseline)"
    print(f"\n=== {header} ===")
    for label, v in variant_items[1:]:
        _report(label, base, _rates(matrix, label), _size(cases[0], v) - base_size, a.noise)


if __name__ == "__main__":
    main()
