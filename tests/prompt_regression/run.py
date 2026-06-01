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
# Capture is MANDATORY: full trajectories always persisted under results/raw/<ts>/.
# Re-running the API to fix a scorer is the trap we keep hitting. Override path
# with --capture <dir>; cannot be disabled (rescore.py + judge passes + manual
# spot-check all depend on this).
ap.add_argument("--capture", default="auto", help="dir to persist trajectories ('auto' = results/raw/<ts>/, default)")
ap.add_argument("--baseline", default=None)         # compare to stored baseline, flag regressions
ap.add_argument("--save-baseline", default=None)
ap.add_argument("--noise", type=float, default=0.2)  # |delta| below this = within-noise
ap.add_argument("--cache-1h", action="store_true",
                help="use Anthropic 1h prompt-cache TTL instead of default 5min "
                     "(higher cache_creation cost, but amortizes across sessions "
                     "for long-running A/B campaigns)")
ap.add_argument("--no-warmup", action="store_true",
                help="skip warm-then-flood (each cell's first rep runs serially "
                     "before parallel reps). Disables the cache-write optimization")
a = ap.parse_args()
if a.cache_1h:
    os.environ["ABA_CACHE_TTL"] = "1h"
if a.no_warmup:
    os.environ["ABA_NO_WARMUP"] = "1"

case_files = (sorted(glob.glob(os.path.join(HERE, "cases", "*.json"))) if a.cases == "all"
              else [os.path.join(HERE, "cases", c + ".json") for c in a.cases.split(",")])
cases = [json.load(open(cf)) for cf in case_files]
variant_items = [(v, VARIANTS[v]) for v in a.variants.split(",")]
if a.capture == "auto":
    import datetime as _dt
    capture = os.path.join(HERE, "results", "raw", _dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
else:
    capture = a.capture if os.path.isabs(a.capture) else os.path.join(HERE, a.capture)

matrix = run_matrix(cases, variant_items, reps=a.reps, workers=a.workers, capture_dir=capture)

results = {}
totals = {"in": 0, "out": 0, "cache_read": 0, "cache_write": 0}
for cid, byv in matrix.items():
    print(f"\n=== {cid}  (n={a.reps}) ===")
    for v, _ in variant_items:
        r = byv[v]
        results.setdefault(cid, {})[v] = r["rates"]
        rate_str = "  ".join(f"{b}={x}" for b, x in r["rates"].items())
        print(f"  {v:18} {rate_str}   outcomes={r['outcomes']}")
        for k in totals:
            totals[k] += (r.get("usage") or {}).get(k, 0) or 0
# Cache-efficiency summary — high cache_read/(cache_read+cache_write) = good
# (the prefix-cache is being reused across reps). Low ratio = each rep is
# writing a fresh cache and not reading anyone else's, the contention bug
# the warm-then-flood is meant to fix.
denom = totals["cache_read"] + totals["cache_write"]
if denom > 0:
    hit_pct = round(100 * totals["cache_read"] / denom, 1)
    print(f"\ntokens: in={totals['in']:,}  out={totals['out']:,}  "
          f"cache_read={totals['cache_read']:,}  cache_write={totals['cache_write']:,}  "
          f"hit-ratio={hit_pct}%")
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
