"""Prompt/recipe-strategy evaluation harness.

Runs each ARM × SCENARIO × REPEAT through the live agent (memory-clean, isolated
tree per arm), auto-scores every run, and aggregates to RATES — because the
behaviours we compare (recipe uptake, fabrication, scope-creep, bug rate) are
intermittent and a single run mislabels them.

An "arm" is a prompt-assembly strategy, selected via the ABA_PROMPT_ARM env var
that build_system reads. 'control' = current prompt. Arms that aren't wired yet
behave as control (so this validates the plumbing before the strategies land).

    python tests/e2e/eval_arms.py --arms control,inject_body \
        --scenarios scanpy_single,de_wrong_method --repeats 5

Writes per-run records (results.jsonl) + a markdown comparison (summary.md) under
/tmp/aba_eval/.
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "tests" / "e2e" / "run_scrna_suite.py"
PY = str(ROOT / ".venv" / "bin" / "python")
EVAL_ROOT = Path("/tmp/aba_eval")


def _load_env() -> dict:
    """Subprocess env = our env + the repo .env (for ANTHROPIC_API_KEY, etc.).
    ABA_MODEL stays inert for the Guide (spec-driven) — kept only for parity."""
    env = dict(os.environ)
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def _archive_session(base: Path, scenario: str, dest: Path) -> dict:
    """Copy this session's COMPLETE logs into a durable per-session folder so a
    human (me) can read+rate every session from full context afterward — repeats
    otherwise overwrite transcripts/<scen>.log. Returns {transcript, contexts,
    events} relative paths for the index."""
    import shutil
    dest.mkdir(parents=True, exist_ok=True)
    tdir = base / "transcripts"
    meta_p = tdir / f"{scenario}.meta.json"
    out = {"transcript": None, "contexts": [], "events": []}
    for src, name in ((tdir / f"{scenario}.log", "transcript.log"),
                      (meta_p, "meta.json")):
        if src.exists():
            shutil.copy2(src, dest / name)
            if name == "transcript.log":
                out["transcript"] = str(dest / name)
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
        tl = Path(meta.get("turnlog") or (base / "turnlog"))
        for rid in meta.get("run_ids", []):
            j = tl / f"{rid}.jsonl"
            if j.exists():
                shutil.copy2(j, dest / f"events_{rid}.jsonl")
                out["events"].append(str(dest / f"events_{rid}.jsonl"))
            for ctx in tl.glob(f"*_{rid}.md"):     # turn-context dump(s)
                shutil.copy2(ctx, dest / f"context_{rid}.md")
                out["contexts"].append(str(dest / f"context_{rid}.md"))
    return out


def run_one(arm: str, scenario: str, base: Path, env: dict, timeout: int,
            archive_dir: Path) -> dict:
    """One agent run; returns the scored metrics (or an error stub). Archives the
    full logs to archive_dir regardless, for human rating."""
    import score_run
    runenv = dict(env)
    runenv["SUITE_BASE"] = str(base)
    runenv["SUITE_FRESH"] = "1"            # fresh DB + DATA_DIR + memory scrub
    runenv["ABA_PROMPT_ARM"] = arm         # build_system reads this (control = no-op)
    t0 = time.time()
    try:
        subprocess.run([PY, "-u", str(HARNESS), scenario], env=runenv, cwd=str(ROOT),
                       timeout=timeout, capture_output=True)
    except subprocess.TimeoutExpired:
        arch = _archive_session(base, scenario, archive_dir)
        return {"arm": arm, "scenario": scenario, "error": f"timeout({timeout}s)",
                "wall_s": round(time.time() - t0), "archive": str(archive_dir), **{"logs": arch}}
    try:
        m = score_run.score_scenario(str(base), scenario)
    except Exception as e:  # noqa: BLE001
        m = {"arm": arm, "scenario": scenario, "error": f"score-failed: {e}"}
    m["wall_s"] = round(time.time() - t0)
    m["archive"] = str(archive_dir)
    m["logs"] = _archive_session(base, scenario, archive_dir)
    return m


# Aggregations: (key, how) — bool/0-1 → rate (mean), numeric → mean.
_RATE = ["recipe_read", "recipe_hint_ignored", "correct_recipe_read",
         "hardcoded_celltype_dict", "produced_summary_doc", "fabrication_signature",
         "autocheck_pass"]
_MEAN = ["n_errors", "errors_before_first_success", "blank_fig_warnings",
         "fetch_warnings", "n_code_cells", "n_tool_calls", "figs_registered",
         "tables_registered", "elapsed_s"]


def _agg(records: list[dict]) -> dict:
    ok = [r for r in records if "error" not in r]
    out = {"n": len(records), "scored": len(ok), "errored": len(records) - len(ok)}
    for k in _RATE:
        # skip None (e.g. correct_recipe_read is None when not meaningful for the
        # scenario/arm) so it doesn't dilute the rate as a false 0.
        vals = [1.0 if r.get(k) else 0.0 for r in ok if r.get(k) is not None]
        if vals:
            out[k + "_rate"] = round(sum(vals) / len(vals), 3)
    for k in _MEAN:
        vals = [r[k] for r in ok if isinstance(r.get(k), (int, float))]
        if vals:
            out[k + "_mean"] = round(sum(vals) / len(vals), 2)
    # idiom adherence (scanpy scenarios): mean fraction of idiom checks passed
    fracs = []
    for r in ok:
        idi = r.get("idioms") or {}
        if idi:
            fracs.append(sum(1 for v in idi.values() if v) / len(idi))
    if fracs:
        out["idiom_adherence_mean"] = round(sum(fracs) / len(fracs), 3)
    # scope-creep = annotation dict OR summary doc
    sc = [1.0 if (r.get("hardcoded_celltype_dict") or r.get("produced_summary_doc")) else 0.0
          for r in ok]
    if sc:
        out["scope_creep_rate"] = round(sum(sc) / len(sc), 3)
    return out


def _md_table(per_arm: dict) -> str:
    cols = ["scored", "recipe_read_rate", "correct_recipe_read_rate", "idiom_adherence_mean",
            "n_errors_mean", "errors_before_first_success_mean", "blank_fig_warnings_mean",
            "scope_creep_rate", "fabrication_signature_rate", "autocheck_pass_rate",
            "figs_registered_mean", "n_code_cells_mean", "elapsed_s_mean"]
    short = {c: c.replace("_rate", "").replace("_mean", "").replace("correct_recipe_read", "corr_read")
             .replace("recipe_hint_ignored", "hint_ign")
             .replace("errors_before_first_success", "err_b4_ok").replace("blank_fig_warnings", "blankfig")
             .replace("fabrication_signature", "fabric").replace("figs_registered", "figs")
             .replace("autocheck_pass", "pass").replace("idiom_adherence", "idiom")
             .replace("n_code_cells", "cells").replace("n_errors", "errs") for c in cols}
    head = "| arm | " + " | ".join(short[c] for c in cols) + " |"
    sep = "|" + "---|" * (len(cols) + 1)
    rows = [head, sep]
    for arm, a in per_arm.items():
        rows.append("| " + arm + " | " + " | ".join(str(a.get(c, "·")) for c in cols) + " |")
    return "\n".join(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="control")
    ap.add_argument("--scenarios", default="scanpy_single")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=1500)
    ap.add_argument("--out", default=str(EVAL_ROOT))
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    scenarios = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    sys.path.insert(0, str(ROOT / "tests" / "e2e"))   # for score_run import
    env = _load_env()
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    results_p = outdir / "results.jsonl"
    records: list[dict] = []

    sessions_dir = outdir / "sessions"
    total = len(arms) * len(scenarios) * args.repeats
    i = 0
    for arm in arms:
        base = EVAL_ROOT / f"arm_{arm}"          # isolated tree per arm
        for scen in scenarios:
            for rep in range(args.repeats):
                i += 1
                print(f"[{i}/{total}] arm={arm} scenario={scen} rep={rep+1}", flush=True)
                archive_dir = sessions_dir / arm / scen / f"rep{rep}"
                rec = run_one(arm, scen, base, env, args.timeout, archive_dir)
                rec.update({"arm": arm, "scenario": scen, "rep": rep})
                records.append(rec)
                with open(results_p, "a") as f:
                    f.write(json.dumps(rec) + "\n")
                tag = rec.get("error") or (
                    f"recipe_read={rec.get('recipe_read')} errs={rec.get('n_errors')} "
                    f"blankfig={rec.get('blank_fig_warnings')} scope="
                    f"{bool(rec.get('hardcoded_celltype_dict') or rec.get('produced_summary_doc'))} "
                    f"pass={rec.get('autocheck_pass')}")
                print(f"    → {tag}", flush=True)

    # Review worklist: every session's archived full logs + its auto-score, so a
    # human can read & rate each one (the auto-scores are TRIAGE, not the verdict).
    idx = ["# Sessions to read & rate (full logs)", "",
           "Auto-scores are first-pass triage only — open each session's "
           "`context_*.md` (the exact prompt the agent saw) + `events_*.jsonl` "
           "(untruncated tool calls/results) and rate it yourself.", ""]
    for r in records:
        logs = r.get("logs") or {}
        a = r.get("archive", "?")
        auto = r.get("error") or (
            f"recipe_read={r.get('recipe_read')} idiom={r.get('idioms')} "
            f"errs={r.get('n_errors')} blankfig={r.get('blank_fig_warnings')} "
            f"fab={r.get('fabrication_signature')} scope="
            f"{bool(r.get('hardcoded_celltype_dict') or r.get('produced_summary_doc'))} "
            f"figs={r.get('figs_registered')} pass={r.get('autocheck_pass')}")
        idx.append(f"- **{r['arm']} / {r['scenario']} / rep{r['rep']}** — `{a}`")
        idx.append(f"    - auto: {auto}")
        idx.append(f"    - context: {', '.join(logs.get('contexts') or ['—'])}")
        idx.append(f"    - events: {', '.join(logs.get('events') or ['—'])}")
        idx.append("    - **my rating:** _(TODO: read full logs → score + notes)_")
    (outdir / "sessions.md").write_text("\n".join(idx))

    per_arm = {arm: _agg([r for r in records if r.get("arm") == arm]) for arm in arms}
    per_arm_scen = {f"{arm}/{scen}": _agg([r for r in records
                    if r.get("arm") == arm and r.get("scenario") == scen])
                    for arm in arms for scen in scenarios}
    summary = {"arms": arms, "scenarios": scenarios, "repeats": args.repeats,
               "per_arm": per_arm, "per_arm_scenario": per_arm_scen}
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))
    md = ["# Prompt-strategy eval", "",
          f"arms={arms}  scenarios={scenarios}  repeats={args.repeats}", "",
          "## Per-arm (averaged over all scenarios)", "", _md_table(per_arm), "",
          "## Per arm × scenario", "", _md_table(per_arm_scen)]
    (outdir / "summary.md").write_text("\n".join(md))
    print("\n" + "\n".join(md))
    print(f"\n[results] {results_p}\n[summary] {outdir/'summary.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
