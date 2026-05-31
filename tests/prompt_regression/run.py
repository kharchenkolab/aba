"""Run the prompt-regression corpus (parallel across the full case x variant x rep matrix).

  python run.py --cases all --variants current --reps 16
  python run.py --cases recipe_uptake__scanpy_plan --variants current,planfirst_old,planfirst_new --reps 16
  python run.py --variants current --baseline baselines/current.json     # regression gate
  python run.py --variants current --save-baseline baselines/current.json --reps 16
  python run.py --cases pseudoreplication__de_single --capture results/raw --reps 8   # persist trajectories for the judge layer
"""
import os, sys, json, glob, argparse
sys.path.insert(0, os.path.dirname(__file__))
from harness import run_matrix
from variants import VARIANTS

HERE = os.path.dirname(__file__)
ap = argparse.ArgumentParser()
ap.add_argument("--cases", default="all")
ap.add_argument("--variants", default="current")
ap.add_argument("--reps", type=int, default=16)
ap.add_argument("--workers", type=int, default=12, help="concurrent rollouts across the whole matrix")
ap.add_argument("--capture", default=None, help="dir to persist full trajectories for the judge layer")
ap.add_argument("--baseline", default=None)         # compare to stored baseline, flag regressions
ap.add_argument("--save-baseline", default=None)
ap.add_argument("--noise", type=float, default=0.2)  # |delta| below this = within-noise
a = ap.parse_args()

case_files = (sorted(glob.glob(os.path.join(HERE, "cases", "*.json"))) if a.cases == "all"
              else [os.path.join(HERE, "cases", c + ".json") for c in a.cases.split(",")])
cases = [json.load(open(cf)) for cf in case_files]
variant_items = [(v, VARIANTS[v]) for v in a.variants.split(",")]
capture = (a.capture if not a.capture or os.path.isabs(a.capture) else os.path.join(HERE, a.capture))

matrix = run_matrix(cases, variant_items, reps=a.reps, workers=a.workers, capture_dir=capture)

results = {}
for cid, byv in matrix.items():
    print(f"\n=== {cid}  (n={a.reps}) ===")
    for v, _ in variant_items:
        r = byv[v]
        results.setdefault(cid, {})[v] = r["rates"]
        rate_str = "  ".join(f"{b}={x}" for b, x in r["rates"].items())
        print(f"  {v:18} {rate_str}   outcomes={r['outcomes']}")
if capture:
    print(f"\ntrajectories captured under {capture}")

if a.save_baseline:
    bp = os.path.join(HERE, a.save_baseline)
    os.makedirs(os.path.dirname(bp), exist_ok=True)
    json.dump(results, open(bp, "w"), indent=1)
    print(f"\nsaved baseline -> {a.save_baseline}")

if a.baseline:
    base = json.load(open(os.path.join(HERE, a.baseline)))
    print("\n=== REGRESSION CHECK vs baseline ===")
    flagged = 0
    for cid, vs in results.items():
        for v, rates in vs.items():
            for b, x in rates.items():
                bx = base.get(cid, {}).get(v, {}).get(b)
                if bx is not None and (x - bx) < -a.noise:
                    print(f"  ⚠ REGRESSION {cid}/{v}/{b}: {bx} -> {x}")
                    flagged += 1
    print("  none" if not flagged else f"  {flagged} regression(s)")
    sys.exit(1 if flagged else 0)
