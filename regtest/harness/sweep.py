#!/usr/bin/env python
"""regtest sweep — the one-command regression/science driver.

Runs every v2 scenario in a FRESH PROCESS (the resource-accumulation lesson: a
long-lived in-process runner piles up kernels/zmq sockets), scores each, writes a
timestamped scorecard, and diffs against the accepted baseline to flag regressions.

  python regtest/harness/sweep.py                 # Haiku breadth (cheap; mechanical only)
  python regtest/harness/sweep.py --opus          # Opus science (rubric judge on)
  python regtest/harness/sweep.py --only tpm,survival     # subset
  python regtest/harness/sweep.py --regen         # regenerate scenario data first
  python regtest/harness/sweep.py --accept        # promote THIS run to the baseline
  python regtest/harness/sweep.py --diagnose      # run the forensic agent on regressed FAILs

Cost tiers (see README): weekly = Haiku; monthly/on-demand = --opus; forensics only
on regressions. Credentials come from ABA_LIVE_ENV (default /tmp/aba_8000.env).
Exit code is nonzero if any regression vs the baseline.
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REG = ROOT / "regtest"
SCEN = REG / "scenarios"
RUNS = SCEN / "_runs"
REPORTS = REG / "reports"
BASELINES = REG / "baselines"
RUNNER = REG / "harness" / "runner.py"
FORENSIC = REG / "harness" / "forensic.py"
REGEN = SCEN / "_regen_all.sh"
PY = sys.executable

KEEP_RUNS_PER_SCENARIO = int(os.environ.get("ABA_REGTEST_KEEP", "3"))   # retention
MAX_RUN_AGE_DAYS = int(os.environ.get("ABA_REGTEST_MAX_AGE_DAYS", "30"))
PER_SCENARIO_TIMEOUT_S = int(os.environ.get("ABA_REGTEST_SCENARIO_TIMEOUT_S", "2400"))  # 40 min
RUBRIC_REGRESSION = 0.3        # a rubric_overall drop beyond this counts as a regression


def discover(only, exclude, smoke=False):
    import yaml
    out = []
    for f in sorted(SCEN.glob("*/scenario.yaml")):
        sid = f.parent.name
        try:
            spec = yaml.safe_load(f.read_text()) or {}
            if not spec.get("steps"):
                continue                                   # v1-only → not part of the v2 sweep
            if smoke and not spec.get("smoke"):
                continue           # smoke tier: only scenarios tagged smoke: true
        except Exception:
            continue
        if only and sid not in only:
            continue
        if exclude and sid in exclude:
            continue
        out.append(sid)
    return out


def run_scenario(sid, mode):
    """Run one scenario in a fresh process; return its report.json dict (or an error rec)."""
    env = dict(os.environ)
    env.setdefault("ABA_LIVE_ENV", "/tmp/aba_8000.env")
    env["ABA_SCENARIO"] = sid
    if mode == "opus":
        env["ABA_SCENARIO_MODEL"] = "claude-opus-4-8"
        env["ABA_JUDGE_MODEL"] = "claude-opus-4-8"
        env.pop("ABA_NO_JUDGE", None)
    else:
        env["ABA_NO_JUDGE"] = "1"                          # Haiku breadth: mechanical only
        env.pop("ABA_SCENARIO_MODEL", None)
    t0 = time.time()
    # Capture the runner's output per scenario instead of discarding it: an
    # ERR row whose cause was DEVNULLed is undiagnosable (a whole sweep of
    # them once had to be re-run by hand to see the first traceback).
    _errlog = RUNS / f"_stderr-{sid}.log"
    RUNS.mkdir(parents=True, exist_ok=True)
    try:
        with open(_errlog, "w") as _ef:
            rc = subprocess.run([PY, "-u", str(RUNNER)], env=env,
                                timeout=PER_SCENARIO_TIMEOUT_S,
                                stdout=_ef, stderr=subprocess.STDOUT,
                                cwd=str(ROOT)).returncode
    except subprocess.TimeoutExpired:
        return {"_error": f"scenario process timed out after {PER_SCENARIO_TIMEOUT_S}s"}
    # exit 3 = SETUP-ERROR from the runner's seed-staging guard: a declared input
    # is not staged, so the scenario is UNRUNNABLE. Treat it like infra (never
    # scored, never baked into a baseline) rather than a 0-score regression — a
    # missing seed is a fixture bug, not a product one.
    if rc == 3:
        return {"_error": "SETUP-ERROR: declared data_files missing from DATA_DIR "
                          "(scenario fixture/staging gap — not a product failure)",
                "_setup_error": True, "_infra": 1}
    # the runner wrote report.json into the latest _runs/<sid>-<ts>/ dir
    runs = sorted(RUNS.glob(f"{sid}-*"), key=lambda p: p.stat().st_mtime)
    fresh = [r for r in runs if r.stat().st_mtime >= t0 - 5]
    rep = (fresh[-1] if fresh else (runs[-1] if runs else None))
    if not rep:
        return {"_error": "no run dir produced"}
    try:
        d = json.loads((rep / "report.json").read_text())
        d["_bundle"] = rep.name
        # Detect INFRA failures (OAuth token expiry / rate limits / overload) so a long
        # sweep that outlives the token or hits 429s doesn't bake garbage into a baseline.
        try:
            bj = json.loads((rep / "bundle.json").read_text())
            errs = " ".join(json.dumps(st.get("errors") or []) for st in bj.get("steps", []))
            d["_infra"] = sum(errs.count(p) for p in
                              ("OAuthTokenUnavailable", "RateLimitError", "rate_limit",
                               "overloaded_error",
                               # a mid-session credential rejection is infra too —
                               # this signature slipped past the detector in a live
                               # sweep and would have been baked into an --accept
                               "AuthenticationError", "OAuth token rejected",
                               "authentication_error"))
        except Exception:
            d["_infra"] = 0
        return d
    except Exception as e:
        return {"_error": f"report.json unreadable: {e}", "_bundle": rep.name}


def score_of(rep):
    """Collapse a report.json into the scorecard row."""
    if rep.get("_error"):
        # a setup-error carries infra=1 so --accept never bakes it in (same
        # treatment as an OAuth/rate-limit failure — the run told us nothing
        # about product quality)
        return {"mech_pass": 0, "mech_total": None, "rubric_overall": None,
                "fails": [f"ERROR:{rep['_error']}"], "bundle": rep.get("_bundle"),
                "infra": 1 if rep.get("_setup_error") else rep.get("_infra", 0)}
    mech = rep.get("mechanical") or {}
    fails = [f"{r['step']}:{';'.join(r.get('fails') or [])}"
             for r in (rep.get("report") or []) if r.get("verdict") == "FAIL"]
    return {"mech_pass": mech.get("pass"), "mech_total": mech.get("total"),
            "rubric_overall": (rep.get("rubric_mean") or {}).get("overall"),
            "fails": fails, "bundle": rep.get("_bundle"), "infra": rep.get("_infra", 0)}


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "?"


def bakeable_rows(rows: dict) -> tuple[dict, list]:
    """Rows safe to promote into the accepted baseline: informative ones only.
    Excludes infra failures AND errored-no-report rows (mech_total None) — a
    null-total reference makes the diff permanently blind for that scenario."""
    clean = {sid: r for sid, r in rows.items()
             if not r.get("infra") and r.get("mech_total") is not None}
    return clean, sorted(set(rows) - set(clean))


def diff_vs_baseline(scorecard, mode):
    base_p = BASELINES / f"{mode}.json"
    if not base_p.exists():
        return None, []
    base = json.loads(base_p.read_text()).get("scenarios", {})
    # Mode-aware mech tolerance: Haiku's mechanical must_mention gates jitter ±2 steps
    # run-to-run (phrasing varies, occasional kernel-hang), so a small dip is noise, not
    # a regression — Haiku is a COARSE robustness net. Opus is deterministic → strict (0).
    mech_tol = int(os.environ.get("ABA_REGTEST_MECH_TOL", "2" if mode == "haiku" else "0"))
    regressions = []
    for sid, cur in scorecard["scenarios"].items():
        b = base.get(sid)
        if not b:
            continue                                       # new scenario, not a regression
        if cur["mech_pass"] is not None and b.get("mech_pass") is not None \
                and (b["mech_pass"] - cur["mech_pass"]) > mech_tol:
            regressions.append((sid, f"mech {b['mech_pass']}→{cur['mech_pass']} (of {cur['mech_total']}, tol {mech_tol})"))
        cr, br = cur.get("rubric_overall"), b.get("rubric_overall")
        if isinstance(cr, (int, float)) and isinstance(br, (int, float)) and (br - cr) > RUBRIC_REGRESSION:
            regressions.append((sid, f"rubric {br}→{cr}"))
    return base, regressions


def prune_runs():
    """Retention: keep the last K runs per scenario + drop anything older than N days."""
    now = time.time(); removed = 0
    byscen: dict = {}
    for r in RUNS.glob("*-*"):
        if not r.is_dir():
            continue
        sid = r.name.rsplit("-", 2)[0]
        byscen.setdefault(sid, []).append(r)
    for sid, rs in byscen.items():
        rs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for i, r in enumerate(rs):
            too_old = (now - r.stat().st_mtime) > MAX_RUN_AGE_DAYS * 86400
            if i >= KEEP_RUNS_PER_SCENARIO or too_old:
                shutil.rmtree(r, ignore_errors=True); removed += 1
    return removed


def write_report(scorecard, base, regressions, mode, ts):
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{mode}-{ts}.json").write_text(json.dumps(scorecard, indent=2))
    lines = [f"# regtest sweep — {mode} — {ts}", "",
             f"commit `{scorecard['meta']['commit']}` · {scorecard['meta']['n_scenarios']} scenarios · "
             f"agent `{scorecard['meta']['agent_model']}`", "",
             "| scenario | mech | rubric | Δ baseline |", "|---|---|---|---|"]
    for sid, s in sorted(scorecard["scenarios"].items()):
        b = (base or {}).get(sid, {})
        delta = ""
        if b.get("mech_pass") is not None and s["mech_pass"] is not None:
            d = s["mech_pass"] - b["mech_pass"]
            delta = "—" if d == 0 else (f"▲+{d}" if d > 0 else f"▼{d}")
        mech = f"{s['mech_pass']}/{s['mech_total']}" if s["mech_total"] is not None else "ERR"
        rub = s["rubric_overall"] if s["rubric_overall"] is not None else "·"
        lines.append(f"| {sid} | {mech} | {rub} | {delta} |")
    lines += ["", f"**Regressions vs baseline: {len(regressions)}**"]
    for sid, why in regressions:
        lines.append(f"- ⚠ {sid}: {why}")
    # A baseline row with null totals is an errored/no-report reference — the
    # diff can NEVER flag a regression for that scenario. Say so in the headline
    # instead of letting the blindness hide in the per-row table.
    blind = sorted(sid for sid, b in (base or {}).items()
                   if b.get("mech_total") is None)
    if blind:
        lines += ["", f"**⚠ Baseline blind spots: {len(blind)} scenario(s) whose "
                      f"reference is 'errored, no report' — regressions there are "
                      f"invisible until a clean run is accepted:**"]
        for sid in blind:
            lines.append(f"- 👁 {sid}")
    (REPORTS / f"{mode}-{ts}.md").write_text("\n".join(lines) + "\n")
    return REPORTS / f"{mode}-{ts}.md"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--opus", action="store_true", help="Opus science mode (rubric judge on)")
    ap.add_argument("--only", default="", help="comma-separated scenario ids")
    ap.add_argument("--exclude", default="", help="comma-separated scenario ids to skip")
    ap.add_argument("--regen", action="store_true", help="regenerate scenario data first")
    ap.add_argument("--accept", action="store_true", help="promote this run to the baseline")
    ap.add_argument("--diagnose", action="store_true", help="forensic-diagnose regressed FAILs")
    ap.add_argument("--no-prune", action="store_true", help="skip _runs retention pruning")
    ap.add_argument("--smoke", action="store_true",
                    help="run only scenarios tagged `smoke: true` — the ~15-min "
                         "routine tier; the full set is the nightly instrument")
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("ABA_REGTEST_WORKERS", "1")),
                    help="parallel scenario processes (each is already an "
                         "isolated subprocess; shared API rate limits are the "
                         "constraint — the infra detector flags collisions)")
    args = ap.parse_args()
    mode = "opus" if args.opus else "haiku"
    only = set(x for x in args.only.split(",") if x)
    exclude = set(x for x in args.exclude.split(",") if x)

    if args.regen:
        print("[sweep] regenerating scenario data…", flush=True)
        subprocess.run(["bash", str(REGEN)], cwd=str(ROOT))

    scenarios = discover(only, exclude, smoke=args.smoke)
    if args.smoke and len(scenarios) < 2:
        print("[sweep] SETUP-ERROR: --smoke found <2 tagged scenarios — the "
              "smoke tier is unarmed (tag scenarios with `smoke: true`).")
        return 2
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    print(f"[sweep] mode={mode}  scenarios={len(scenarios)}  ts={ts}\n", flush=True)

    rows = {}

    def _one(sid):
        rep = run_scenario(sid, mode)
        return sid, score_of(rep)

    if args.workers > 1:
        # Each scenario is an isolated subprocess (own run dir, own runtime
        # state); the shared eval home is read-only (installation/config).
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print(f"[sweep] {args.workers} parallel workers", flush=True)
        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(_one, sid): sid for sid in scenarios}
            for fut in as_completed(futs):
                sid, row = fut.result()
                rows[sid] = row
                done += 1
                mech = f"{row['mech_pass']}/{row['mech_total']}" if row["mech_total"] is not None else "ERR"
                tag = f"  ⚠INFRA({row['infra']})" if row.get("infra") else ""
                print(f"[{done}/{len(scenarios)}] {sid}  {mech}  "
                      f"rubric={row['rubric_overall']}  fails={len(row['fails'])}{tag}",
                      flush=True)
    else:
        for i, sid in enumerate(scenarios, 1):
            print(f"[{i}/{len(scenarios)}] {sid} …", flush=True)
            sid, row = _one(sid)
            rows[sid] = row
            mech = f"{row['mech_pass']}/{row['mech_total']}" if row["mech_total"] is not None else "ERR"
            tag = f"  ⚠INFRA({row['infra']})" if row.get("infra") else ""
            print(f"      {mech}  rubric={row['rubric_overall']}  fails={len(row['fails'])}{tag}", flush=True)

    scorecard = {
        "meta": {"date": ts, "mode": mode, "commit": git_commit(),
                 "agent_model": ("claude-opus-4-8" if mode == "opus" else "claude-haiku-4-5"),
                 "n_scenarios": len(scenarios)},
        "scenarios": rows,
        "totals": {"mech_pass": sum((r["mech_pass"] or 0) for r in rows.values()),
                   "mech_total": sum((r["mech_total"] or 0) for r in rows.values()),
                   "full_pass": sum(1 for r in rows.values()
                                    if r["mech_total"] and r["mech_pass"] == r["mech_total"])},
    }
    base, regressions = diff_vs_baseline(scorecard, mode)
    md = write_report(scorecard, base, regressions, mode, ts)

    if not args.no_prune:
        print(f"\n[sweep] pruned {prune_runs()} old run bundles (keep {KEEP_RUNS_PER_SCENARIO}/scenario, <{MAX_RUN_AGE_DAYS}d)")

    if args.diagnose and regressions:
        for sid, _ in regressions:
            print(f"[sweep] forensic → {sid}")
            env = dict(os.environ); env["ABA_SCENARIO"] = sid
            subprocess.run([PY, "-u", str(FORENSIC)], env=env, cwd=str(ROOT))

    infra_scen = [sid for sid, r in rows.items() if r.get("infra")]
    if infra_scen:
        print(f"\n[sweep] ⚠ {len(infra_scen)} scenario(s) hit INFRA errors (OAuth expiry / rate limit), "
              f"NOT science failures — re-run under fresh creds: {infra_scen}")

    if args.accept:
        BASELINES.mkdir(parents=True, exist_ok=True)
        bp = BASELINES / f"{mode}.json"
        prior = json.loads(bp.read_text()).get("scenarios", {}) if bp.exists() else {}
        # Never bake in a row that told us nothing about product quality:
        # infra failures AND errored-no-report rows (mech_total None). A
        # null-total row in the baseline normalizes "errored, no report" as
        # that scenario's reference — the diff then can't regress against it,
        # ever, and the blindness is invisible in headline numbers.
        clean, skipped = bakeable_rows(rows)
        if skipped:
            print(f"[sweep] ⚠ NOT baking {len(skipped)} uninformative row(s) into the "
                  f"baseline (infra or errored/no-report): {skipped}")
        merged = dict(prior); merged.update(clean)
        legacy_blind = sorted(sid for sid, r in merged.items()
                              if r.get("mech_total") is None)
        if legacy_blind:
            print(f"[sweep] ⚠ baseline still carries {len(legacy_blind)} LEGACY "
                  f"errored/no-report reference(s) — the diff is blind there until a "
                  f"clean run of each is accepted: {legacy_blind}")
        out = dict(scorecard); out["scenarios"] = merged
        out["totals"] = {"mech_pass": sum((r.get("mech_pass") or 0) for r in merged.values()),
                         "mech_total": sum((r.get("mech_total") or 0) for r in merged.values()),
                         "full_pass": sum(1 for r in merged.values()
                                          if r.get("mech_total") and r["mech_pass"] == r["mech_total"])}
        bp.write_text(json.dumps(out, indent=2))
        skip = f"  (skipped {len(infra_scen)} infra-failed, kept prior/absent)" if infra_scen else ""
        print(f"[sweep] baseline updated: baselines/{mode}.json{skip}")

    t = scorecard["totals"]
    print(f"\n=== sweep {mode}: {t['full_pass']}/{len(scenarios)} scenarios full-pass, "
          f"{t['mech_pass']}/{t['mech_total']} steps · regressions={len(regressions)} ===")
    print(f"    report: {md}")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
