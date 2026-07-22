#!/usr/bin/env python
"""regtest sweep — the one-command regression/science driver.

Runs every v2 scenario in a FRESH PROCESS (the resource-accumulation lesson: a
long-lived in-process runner piles up kernels/zmq sockets), scores each, writes a
timestamped scorecard, and diffs against the accepted baseline to flag regressions.

  python regtest/harness/sweep.py --smoke --workers 4     # routine tier (~10 min)
  python regtest/harness/sweep.py --workers 4     # full Haiku breadth (nightly)
  python regtest/harness/sweep.py --opus          # Opus science (rubric judge on)
  python regtest/harness/sweep.py --only tpm,survival     # subset
  python regtest/harness/sweep.py --regen         # regenerate scenario data first
  python regtest/harness/sweep.py --accept        # promote THIS run to the baseline
  python regtest/harness/sweep.py --diagnose      # run the forensic agent on regressed FAILs

Cost tiers (see README): routine = --smoke; weekly = Haiku; monthly/on-demand =
--opus; forensics only on regressions. Credentials come from ABA_LIVE_ENV
(default /tmp/aba_8000.env). Exit code is nonzero if any regression vs baseline.

Three verdicts, never conflated: MEASURED (ran + scored), UNMEASURED (had a
baseline but produced no measurement — lost coverage, NOT a regression), and
baseline BLIND SPOTS (the reference itself is "errored, no report"). Pre-flight
refuses a run that could not measure at all — an unprovisioned eval home aborts,
and scenarios with absent declared inputs are skipped before any budget is spent.
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


def inert_smoke_tags() -> list[str]:
    """Scenarios tagged `smoke: true` that discovery can never return (no v2
    `steps`). The tag reads as coverage while selecting nothing — a silently
    inert tag is how a tier shrinks without anyone noticing."""
    import yaml
    out = []
    for f in sorted(SCEN.glob("*/scenario.yaml")):
        try:
            spec = yaml.safe_load(f.read_text()) or {}
        except Exception:
            continue
        if spec.get("smoke") and not spec.get("steps"):
            out.append(f.parent.name)
    return out


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


MIN_INSTALLED_SKILLS = int(os.environ.get("ABA_REGTEST_MIN_SKILLS", "50"))


def check_eval_home() -> list[str]:
    """Is the eval home PROVISIONED? Refuse to measure with an empty toolbox.

    An ABA_HOME with no deployed installation still runs: the agent simply has
    a near-empty skill catalog and refuses work it cannot ground, so all 31
    scenarios fail for one reason that has nothing to do with the product. A
    full sweep once burned hours that way (a dozen skills visible instead of
    ~300, and scores of capability refusals) and the output looked like a
    catastrophic product regression. A run that CANNOT measure must fail loudly
    up front, not produce a confident zero."""
    problems = []
    home = os.environ.get("ABA_HOME")
    if not home:
        # Mirror the runner's resolution EXACTLY: when ABA_HOME is unset, each
        # runner sources it from the ABA_LIVE_ENV creds file (NUL-separated
        # k=v; see runner.bootstrap_env, ABA_HOME ∈ CRED_KEYS). Pre-flight must
        # validate the home the runners will USE — checking ~/.aba while they
        # run under the env-file home can green-light the exact confident-zero
        # run this guard exists to prevent, or falsely abort a good one.
        ef = Path(os.environ.get("ABA_LIVE_ENV", "/tmp/aba_8000.env"))
        if ef.exists():
            for kv in ef.read_bytes().split(b"\0"):
                if b"=" in kv:
                    k, v = kv.split(b"=", 1)
                    if k == b"ABA_HOME":
                        try:
                            home = v.decode()
                        except UnicodeDecodeError:
                            pass
                        break
    home = Path(home or (Path.home() / ".aba"))
    inst = home / "installation"
    if not inst.exists():
        problems.append(f"ABA_HOME={home} has no installation/ — the eval home is "
                        f"UNPROVISIONED (symlink or deploy one before sweeping)")
        return problems
    n_skills = len(list((inst / "skills").rglob("*.md"))) if (inst / "skills").is_dir() else 0
    if n_skills < MIN_INSTALLED_SKILLS:
        problems.append(f"only {n_skills} skill files under {inst}/skills "
                        f"(< {MIN_INSTALLED_SKILLS}) — catalog looks UNPROVISIONED; "
                        f"every scenario would fail on capability refusals, not product bugs")
    if not (home / "config.env").is_file():
        problems.append(f"{home}/config.env missing — runtime config unprovisioned")
    return problems


def preflight_fixtures(scenarios) -> dict:
    """Predict the runner's seed-staging guard STATICALLY, in milliseconds.

    Staging copies `scenarios/<sid>/data/` into DATA_DIR, so a declared input
    absent from that tree is absent after staging — the runner then exits 3,
    but only after a full app boot, and in a sweep that verdict arrives hours
    in. Same predicate, paid up front."""
    import yaml
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fixtures import declared_inputs, missing_inputs
    gaps, examined = {}, 0
    for sid in scenarios:
        try:
            spec = yaml.safe_load((SCEN / sid / "scenario.yaml").read_text()) or {}
        except Exception:
            continue
        declared = declared_inputs(spec)
        if not declared:
            continue
        examined += 1
        missing = missing_inputs(declared, SCEN / sid / "data")
        if missing:
            gaps[sid] = missing
    # ARMED: a check that examined nothing proves nothing. If no selected
    # scenario declares inputs at all, say so rather than reporting a clean bill.
    return {"gaps": gaps, "examined": examined}


def _observed_model(rows) -> "str | None":
    """The model the runs ACTUALLY used, from their wire requests.

    Distinct values are joined: a sweep whose scenarios did not all run on the
    same model is a fact the scorecard must state, not average away."""
    seen = sorted({r.get("agent_model") for r in rows.values() if r.get("agent_model")})
    return "+".join(seen) if seen else None


def _model_truth_banner(scorecard) -> "str | None":
    """Warn when the tier's ASSUMED model is not the one that ran.

    This is not cosmetic. The mechanical tolerance is keyed to the tier name
    (`mech_tol` = 2 for 'haiku', 0 otherwise) on the reasoning that a small
    model is noisy. At a deployment whose default agent is large, the routine
    tier runs that large model and still gets the small model's slack — so a
    genuine 1-2 step regression is forgiven by a rule written for a model that
    never ran, and the run is billed at the large model's rate while the README
    calls it the cheap tier. Observed 2026-07-22: every routine scorecard said
    `claude-haiku-4-5`; every wire request said `claude-opus-4-7`."""
    meta = scorecard["meta"]
    got, assumed = meta.get("agent_model"), meta.get("agent_model_assumed")
    if not got or got == "unknown" or got == assumed:
        return None
    tol = os.environ.get("ABA_REGTEST_MECH_TOL")
    return (f"⚠ MODEL MISMATCH — this tier assumes `{assumed}` but `{got}` served the "
            f"turns. The mechanical tolerance"
            + (f" (ABA_REGTEST_MECH_TOL={tol})" if tol else
               " (2 steps for the 'haiku' tier, 0 otherwise)")
            + f" is keyed to the ASSUMED model, so regressions inside that window are "
              f"being forgiven by a rule written for a model that did not run — and the "
              f"cost is `{got}`'s, not `{assumed}`'s. Set ABA_SCENARIO_MODEL to pin the "
              f"agent, or ABA_REGTEST_MECH_TOL to match what actually runs.")


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
                "infra": 1 if rep.get("_setup_error") else rep.get("_infra", 0),
                # the CAUSE survives into the row: a fixture gap and an expired
                # token are both "infra" but want opposite remedies, and one
                # banner advising "re-run under fresh creds" for both sends you
                # chasing credentials over a missing seed file
                "setup_error": bool(rep.get("_setup_error"))}
    mech = rep.get("mechanical") or {}
    fails = [f"{r['step']}:{';'.join(r.get('fails') or [])}"
             for r in (rep.get("report") or []) if r.get("verdict") == "FAIL"]
    return {"mech_pass": mech.get("pass"), "mech_total": mech.get("total"),
            "rubric_overall": (rep.get("rubric_mean") or {}).get("overall"),
            "fails": fails, "bundle": rep.get("_bundle"), "infra": rep.get("_infra", 0),
            # what the runner saw on the wire — carried up so the scorecard can
            # state the model it MEASURED instead of the one the flag implies
            "agent_model": rep.get("agent_model")}


def git_commit():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        return "?"


def is_informative(row: dict) -> bool:
    """Did this row MEASURE anything about product quality?

    THE single predicate behind both the baking path and the diff path. When
    they disagree the harness lies in one direction or the other: a row we
    refuse to bake (it told us nothing) but happily score against a baseline
    becomes a phantom regression, because `score_of` gives an unrunnable
    scenario `mech_pass=0` — subtract that from a real baseline and every such
    row reads as a total product collapse. Four fixture-staging errors once
    drowned two genuine regressions in a headline of six."""
    return not row.get("infra") and row.get("mech_total") is not None


def bakeable_rows(rows: dict) -> tuple[dict, list]:
    """Rows safe to promote into the accepted baseline: informative ones only.
    Excludes infra failures AND errored-no-report rows (mech_total None) — a
    null-total reference makes the diff permanently blind for that scenario."""
    clean = {sid: r for sid, r in rows.items() if is_informative(r)}
    return clean, sorted(set(rows) - set(clean))


def ratchet(clean: dict, prior: dict, allow_lower: bool = False):
    """→ (rows_to_bake, lowered_descriptions).

    A baseline is the bar regressions are measured against, so it must not
    drift DOWN by accident. Accepting a run that dipped INSIDE the jitter
    tolerance quietly lowers the bar; after a few such accepts a genuine
    regression sits below a reference that walked down to meet it, and the
    instrument reports green while the product falls.

    Where the prior scored higher, its row is kept WHOLESALE — a baseline row
    stays a coherent snapshot of one run rather than a splice of two."""
    out, lowered = dict(clean), []
    if allow_lower:
        return out, lowered
    for sid, cur in clean.items():
        b = prior.get(sid) or {}
        if b.get("mech_total") is None or cur.get("mech_pass") is None:
            continue
        if b.get("mech_total") != cur.get("mech_total"):
            # The scenario changed SHAPE (steps added/removed) — the prior row
            # is a bar for a test that no longer exists. Keeping it pins a
            # permanent phantom regression ("mech 12→8 of 8"); re-baseline.
            continue
        if (b.get("mech_pass") or 0) > cur["mech_pass"]:
            lowered.append(f"{sid} ({b['mech_pass']}→{cur['mech_pass']})")
            out[sid] = b
    return out, lowered


def diff_vs_baseline(scorecard, mode):
    """→ (baseline, regressions, unmeasured).

    `unmeasured` = rows that HAD a baseline but measured nothing this run.
    They are neither regressions (nothing ran, so nothing regressed) nor
    silently dropped (lost coverage against a known reference is a real
    result) — they get their own loud category."""
    base_p = BASELINES / f"{mode}.json"
    if not base_p.exists():
        return None, [], []
    try:
        base = json.loads(base_p.read_text()).get("scenarios", {})
    except (json.JSONDecodeError, OSError) as e:
        # LOUD, not a crash after a multi-hour run — and not a silent fresh-run
        # either (that would hide every regression behind a corrupt file).
        print(f"[sweep] ⚠ baseline {base_p} UNREADABLE ({e}) — diff skipped; "
              f"restore it from git or re-accept a clean run")
        return None, [], []
    # Mode-aware mech tolerance: Haiku's mechanical must_mention gates jitter ±2 steps
    # run-to-run (phrasing varies, occasional kernel-hang), so a small dip is noise, not
    # a regression — Haiku is a COARSE robustness net. Opus is deterministic → strict (0).
    mech_tol = int(os.environ.get("ABA_REGTEST_MECH_TOL", "2" if mode == "haiku" else "0"))
    regressions, unmeasured = [], []
    for sid, cur in scorecard["scenarios"].items():
        b = base.get(sid)
        if not b:
            continue                                       # new scenario, not a regression
        if not is_informative(cur):
            # Same predicate as bakeable_rows: this run told us nothing here.
            # Report the LOST COVERAGE, do not manufacture a regression from a
            # synthetic 0 (and name the cause — a fixture gap and a dead token
            # want opposite remedies).
            why = (cur.get("fails") or ["ERROR:unknown"])[0]
            unmeasured.append((sid, why.split(" (")[0][:120]))
            continue
        if cur["mech_pass"] is not None and b.get("mech_pass") is not None \
                and (b["mech_pass"] - cur["mech_pass"]) > mech_tol:
            regressions.append((sid, f"mech {b['mech_pass']}→{cur['mech_pass']} (of {cur['mech_total']}, tol {mech_tol})"))
        cr, br = cur.get("rubric_overall"), b.get("rubric_overall")
        if isinstance(cr, (int, float)) and isinstance(br, (int, float)) and (br - cr) > RUBRIC_REGRESSION:
            regressions.append((sid, f"rubric {br}→{cr}"))
    return base, regressions, unmeasured


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


def write_report(scorecard, base, regressions, mode, ts, unmeasured=()):
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{mode}-{ts}.json").write_text(json.dumps(scorecard, indent=2))
    lines = [f"# regtest sweep — {mode} — {ts}", "",
             f"commit `{scorecard['meta']['commit']}` · {scorecard['meta']['n_scenarios']} scenarios · "
             f"agent `{scorecard['meta']['agent_model']}`", ""]
    _mb = _model_truth_banner(scorecard)
    if _mb:
        lines += [f"> {_mb}", ""]
    lines += ["| scenario | mech | rubric | Δ baseline |", "|---|---|---|---|"]
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
    # Lost coverage is its own result — a scenario with a baseline that measured
    # nothing this run is NOT a regression (nothing ran), but it is not a pass
    # either, and collapsing it into either number is a lie.
    if unmeasured:
        lines += ["", f"**⚠ Unmeasured vs baseline: {len(unmeasured)} scenario(s) had a "
                      f"reference but produced no measurement this run — coverage lost, "
                      f"not product regression:**"]
        for sid, why in unmeasured:
            lines.append(f"- ∅ {sid}: {why}")
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
    ap.add_argument("--accept-lower", action="store_true",
                    help="with --accept, allow a row to LOWER its baseline "
                         "(default ratchets: the higher prior reference is kept, "
                         "so the bar never drifts down by accident)")
    ap.add_argument("--allow-unprovisioned", action="store_true",
                    help="run even if the eval home looks unprovisioned "
                         "(pre-flight normally refuses — the scorecard would be "
                         "meaningless)")
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
    if args.smoke:
        _inert = inert_smoke_tags()
        if _inert:
            print(f"[sweep] ⚠ {len(_inert)} scenario(s) carry `smoke: true` but have no "
                  f"v2 steps — the tag selects NOTHING and the tier is smaller than it "
                  f"looks: {_inert}", flush=True)
    if args.smoke and len(scenarios) < 2:
        print("[sweep] SETUP-ERROR: --smoke found <2 tagged scenarios — the "
              "smoke tier is unarmed (tag scenarios with `smoke: true`).")
        return 2
    # ---- PRE-FLIGHT: never spend hours on a run that cannot measure ----------
    home_problems = check_eval_home()
    if home_problems and not args.allow_unprovisioned:
        print("[sweep] SETUP-ERROR: eval home is not provisioned —")
        for p in home_problems:
            print(f"          · {p}")
        print("        Every scenario would fail for this one reason and the "
              "scorecard would read as a product collapse. Provision the home "
              "(or pass --allow-unprovisioned if you truly mean it).")
        return 2

    pf = preflight_fixtures(scenarios)
    fixture_gaps = pf["gaps"]
    if fixture_gaps:
        print(f"[sweep] ⚠ PRE-FLIGHT: {len(fixture_gaps)} scenario(s) have declared "
              f"inputs absent from their data/ tree — UNRUNNABLE (fixture gap, not a "
              f"product failure). Skipping them up front instead of discovering it "
              f"one boot at a time:", flush=True)
        for sid, missing in sorted(fixture_gaps.items()):
            print(f"          · {sid}: {len(missing)} missing")
        print(f"        Fix: regenerate/commit the inputs "
              f"(`bash regtest/scenarios/_regen_all.sh`), or correct the "
              f"scenario's data_files declaration.", flush=True)
    elif pf["examined"] == 0:
        print("[sweep] note: pre-flight examined 0 scenarios with declared "
              "data_files — the fixture check is VACUOUS for this selection.",
              flush=True)

    runnable = [s for s in scenarios if s not in fixture_gaps]
    ts = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    print(f"[sweep] mode={mode}  scenarios={len(scenarios)}"
          f"{f' ({len(runnable)} runnable)' if fixture_gaps else ''}  ts={ts}\n",
          flush=True)

    # Pre-flight-skipped scenarios still get their row — identical to what the
    # runner's exit-3 would have produced — so the accounting never quietly
    # shrinks (a sweep that reports 27/27 because 4 vanished is a lie).
    rows = {sid: score_of({"_error": f"SETUP-ERROR: {len(fixture_gaps[sid])} declared "
                                     f"data_files absent from the scenario's data/ tree "
                                     f"(pre-flight; scenario never started)",
                           "_setup_error": True, "_infra": 1})
            for sid in fixture_gaps}

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
            futs = {pool.submit(_one, sid): sid for sid in runnable}
            for fut in as_completed(futs):
                sid, row = fut.result()
                rows[sid] = row
                done += 1
                mech = f"{row['mech_pass']}/{row['mech_total']}" if row["mech_total"] is not None else "ERR"
                tag = f"  ⚠INFRA({row['infra']})" if row.get("infra") else ""
                print(f"[{done}/{len(runnable)}] {sid}  {mech}  "
                      f"rubric={row['rubric_overall']}  fails={len(row['fails'])}{tag}",
                      flush=True)
    else:
        for i, sid in enumerate(runnable, 1):
            print(f"[{i}/{len(runnable)}] {sid} …", flush=True)
            sid, row = _one(sid)
            rows[sid] = row
            mech = f"{row['mech_pass']}/{row['mech_total']}" if row["mech_total"] is not None else "ERR"
            tag = f"  ⚠INFRA({row['infra']})" if row.get("infra") else ""
            print(f"      {mech}  rubric={row['rubric_overall']}  fails={len(row['fails'])}{tag}", flush=True)

    scorecard = {
        "meta": {"date": ts, "mode": mode, "commit": git_commit(),
                 # From the RUNS, not from the flag. Hardcoding it by mode said
                 # "claude-haiku-4-5" on every routine scorecard while the
                 # deployment's default agent (claude-opus-4-7) served every
                 # turn — the label was derived from the CLI switch and never
                 # compared with what went on the wire.
                 "agent_model": _observed_model(rows) or "unknown",
                 "agent_model_assumed": ("claude-opus-4-8" if mode == "opus"
                                         else "claude-haiku-4-5"),
                 "n_scenarios": len(scenarios)},
        "scenarios": rows,
        "totals": {"mech_pass": sum((r["mech_pass"] or 0) for r in rows.values()),
                   "mech_total": sum((r["mech_total"] or 0) for r in rows.values()),
                   "full_pass": sum(1 for r in rows.values()
                                    if r["mech_total"] and r["mech_pass"] == r["mech_total"])},
    }
    base, regressions, unmeasured = diff_vs_baseline(scorecard, mode)
    md = write_report(scorecard, base, regressions, mode, ts, unmeasured)

    if not args.no_prune:
        print(f"\n[sweep] pruned {prune_runs()} old run bundles (keep {KEEP_RUNS_PER_SCENARIO}/scenario, <{MAX_RUN_AGE_DAYS}d)")

    if args.diagnose and regressions:
        for sid, _ in regressions:
            print(f"[sweep] forensic → {sid}")
            env = dict(os.environ); env["ABA_SCENARIO"] = sid
            subprocess.run([PY, "-u", str(FORENSIC)], env=env, cwd=str(ROOT))

    # Split the INFRA bucket by CAUSE — the remedies are different and a wrong
    # one costs a debugging session: a staged-fixture gap is not fixed by fresh
    # credentials, and no amount of re-running under a new token stages a
    # missing seed file.
    setup_scen = [sid for sid, r in rows.items() if r.get("setup_error")]
    creds_scen = [sid for sid, r in rows.items()
                  if r.get("infra") and not r.get("setup_error")]
    if creds_scen:
        print(f"\n[sweep] ⚠ {len(creds_scen)} scenario(s) hit CREDENTIAL/RATE-LIMIT errors, "
              f"NOT product failures — re-run under fresh creds: {creds_scen}")
    if setup_scen:
        print(f"\n[sweep] ⚠ {len(setup_scen)} scenario(s) never ran: SETUP/FIXTURE gap "
              f"(declared inputs absent after staging). Fresh creds will NOT help — "
              f"fix the fixture/staging: {setup_scen}")

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
        clean, lowered = ratchet(clean, prior, allow_lower=args.accept_lower)
        if lowered:
            print(f"[sweep] ⚠ ratchet: kept the HIGHER prior reference for "
                  f"{len(lowered)} row(s) rather than lowering the bar "
                  f"(--accept-lower to override): {lowered}")
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
        skip = (f"  (skipped {len(skipped)} uninformative, kept prior/absent)"
                if skipped else "")
        print(f"[sweep] baseline updated: baselines/{mode}.json{skip}")

    t = scorecard["totals"]
    unmeas = f" · unmeasured={len(unmeasured)}" if unmeasured else ""
    print(f"\n=== sweep {mode}: {t['full_pass']}/{len(scenarios)} scenarios full-pass, "
          f"{t['mech_pass']}/{t['mech_total']} steps · "
          f"regressions={len(regressions)}{unmeas} ===")
    # On the console too: "regressions=0" is the line people act on, and it is
    # exactly the number the tolerance-vs-model mismatch inflates.
    _mb = _model_truth_banner(scorecard)
    if _mb:
        print(f"    {_mb}")
    print(f"    report: {md}")
    return 1 if regressions else 0


if __name__ == "__main__":
    sys.exit(main())
