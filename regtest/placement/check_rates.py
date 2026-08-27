#!/usr/bin/env python
"""Gate a placement RATE against a floor — the acceptance criterion for any
change to how the agent is told about resources.

WHY A RATE AND NOT A PASS. Placement is a model judgement, so its output is a
distribution. On 2026-08-27 `est_gpu` came back true on three consecutive runs
of the same request and false on the fourth, and the CPU run reported plain
success — three greens hid it entirely. A single trial samples a distribution;
it cannot describe one, and a prompt tuned against one sample is tuned against
noise.

WHY THIS EXISTS SEPARATELY FROM THE STUDY. Changing a tool-parameter
description is a change to a SHARED AGENT INPUT: it reaches every decision the
agent makes, and the project's rules require such a change to ship with a
behavioural guard rather than an assertion that the new wording reads better.
This is that guard — run the study before and after, and compare rates.

    python regtest/placement/check_rates.py results.json \\
        --scenario cluster_gpu_unhinted_training --field est_gpu --min-rate 0.9

Exit 0 if the rate meets the floor, 1 if it does not, 2 if the run cannot
support the claim at all (wrong scenario, too few trials) — because "no data"
and "bad data" must not look alike to a caller.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OK, BELOW, UNUSABLE = 0, 1, 2


def rate(results: list, scenario: str, field: str) -> tuple[int, int]:
    """-> (hits, trials) for `scenario`: a trial HITS if any of its exec
    decisions set `field` truthy. Per-trial, not per-decision: the agent makes
    several calls per turn (it explores, then submits), and counting decisions
    would let a chatty trial outvote a decisive one."""
    rows = [r for r in results if r.get("name") == scenario]
    hits = sum(1 for r in rows
               if any((d.get("input") or {}).get(field)
                      for d in (r.get("decisions") or [])))
    return hits, len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results", help="results.json from placement/study.py")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--field", default="est_gpu")
    ap.add_argument("--min-rate", type=float, default=0.9)
    ap.add_argument("--min-trials", type=int, default=5,
                    help="fewer than this cannot support a rate claim")
    a = ap.parse_args()

    try:
        results = json.loads(Path(a.results).read_text())
    except Exception as e:  # noqa: BLE001
        print(f"check_rates: cannot read {a.results}: {e}", file=sys.stderr)
        return UNUSABLE

    hits, n = rate(results, a.scenario, a.field)
    if n == 0:
        print(f"check_rates: no trials for scenario {a.scenario!r} in this run "
              f"— the study did not measure what is being gated.", file=sys.stderr)
        return UNUSABLE
    if n < a.min_trials:
        print(f"check_rates: only {n} trial(s) for {a.scenario!r}; "
              f"{a.min_trials} are needed before a rate means anything. "
              f"Re-run the study with --repeat {a.min_trials}.", file=sys.stderr)
        return UNUSABLE

    r = hits / n
    verdict = "OK" if r >= a.min_rate else "BELOW FLOOR"
    print(f"{a.scenario}  {a.field}={hits}/{n} = {r:.0%}  "
          f"(floor {a.min_rate:.0%})  {verdict}")
    return OK if r >= a.min_rate else BELOW


if __name__ == "__main__":
    raise SystemExit(main())
