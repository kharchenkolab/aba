"""Baseline honesty for the regtest sweep (regtest/harness/sweep.py).

Two invariants:
1. `--accept` never bakes an UNINFORMATIVE row into the baseline — neither an
   infra failure nor an errored-no-report row (mech_total None). A null-total
   reference normalizes "errored, no report" as that scenario's baseline and
   the diff can never flag a regression there again.
2. The sweep report SAYS OUT LOUD when the accepted baseline already carries
   such blind references (legacy bakes), instead of hiding them in the table.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "aba_sweep", ROOT / "regtest" / "harness" / "sweep.py")
sweep = importlib.util.module_from_spec(_spec)
sys.modules["aba_sweep"] = sweep
_spec.loader.exec_module(sweep)

pytestmark = pytest.mark.platform


def _row(mech_pass=3, mech_total=4, infra=0, fails=(), setup_error=False):
    return {"mech_pass": mech_pass, "mech_total": mech_total,
            "rubric_overall": None, "fails": list(fails),
            "bundle": None, "infra": infra, "setup_error": setup_error}


def _diff(rows, base, tmp_path, monkeypatch, mode="haiku"):
    """Run diff_vs_baseline against an on-disk baseline."""
    monkeypatch.setattr(sweep, "BASELINES", tmp_path)
    (tmp_path / f"{mode}.json").write_text(json.dumps({"scenarios": base}))
    return sweep.diff_vs_baseline({"scenarios": rows}, mode)


def test_accept_excludes_infra_and_null_total_rows():
    rows = {
        "good":      _row(),
        "infra_hit": _row(infra=1),
        "errored":   _row(mech_pass=0, mech_total=None, fails=["ERROR:boom"]),
    }
    clean, skipped = sweep.bakeable_rows(rows)
    assert set(clean) == {"good"}
    assert skipped == ["errored", "infra_hit"]


def test_all_clean_rows_bake():
    rows = {"a": _row(), "b": _row(mech_pass=0, mech_total=2)}   # failing but INFORMATIVE
    clean, skipped = sweep.bakeable_rows(rows)
    assert set(clean) == {"a", "b"} and skipped == []


def test_report_names_baseline_blind_spots(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "REPORTS", tmp_path)
    scorecard = {"meta": {"commit": "abc", "n_scenarios": 2, "agent_model": "m"},
                 "scenarios": {"good": _row(), "blind_ref": _row()}}
    base = {"good": _row(), "blind_ref": _row(mech_pass=0, mech_total=None)}
    md_path = sweep.write_report(scorecard, base, [], "haiku", "20260101-000000")
    md = md_path.read_text()
    assert "Baseline blind spots: 1" in md
    assert "blind_ref" in md.split("Baseline blind spots")[1]


def test_report_silent_when_baseline_clean(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "REPORTS", tmp_path)
    scorecard = {"meta": {"commit": "abc", "n_scenarios": 1, "agent_model": "m"},
                 "scenarios": {"good": _row()}}
    md = sweep.write_report(scorecard, {"good": _row()}, [], "haiku",
                            "20260101-000001").read_text()
    assert "blind spots" not in md.lower()


def test_unrunnable_row_is_unmeasured_not_a_regression(tmp_path, monkeypatch):
    """The phantom-regression class: `score_of` hands an unrunnable scenario
    `mech_pass=0, mech_total=None`. Diffed naively against a real baseline that
    0 reads as a total product collapse — four fixture-staging errors once
    drowned two genuine regressions in a headline of six. Nothing ran, so
    nothing regressed: it is LOST COVERAGE, reported under its own heading."""
    rows = {"broke": _row(mech_pass=0, mech_total=None, infra=1,
                          setup_error=True,
                          fails=["ERROR:SETUP-ERROR: declared inputs missing"])}
    base = {"broke": _row(mech_pass=8, mech_total=8)}
    _, regressions, unmeasured = _diff(rows, base, tmp_path, monkeypatch)
    assert regressions == [], f"unrunnable row scored as a regression: {regressions}"
    assert [sid for sid, _ in unmeasured] == ["broke"]
    assert "SETUP-ERROR" in unmeasured[0][1], "cause not carried into the report"


def test_real_regression_still_reported(tmp_path, monkeypatch):
    """The other side: suppressing phantom regressions must not suppress REAL
    ones. A check that only ever asserts 'fewer regressions' is satisfied by a
    harness that reports none at all — so pair it with this floor."""
    rows = {"real": _row(mech_pass=1, mech_total=8)}       # informative, and down 7
    base = {"real": _row(mech_pass=8, mech_total=8)}
    _, regressions, unmeasured = _diff(rows, base, tmp_path, monkeypatch)
    assert [sid for sid, _ in regressions] == ["real"], regressions
    assert unmeasured == []


def test_credential_failure_mid_run_is_also_unmeasured(tmp_path, monkeypatch):
    """WIDE — the other axis of the predicate. A rate-limit/token failure can
    strike AFTER a report exists, so mech_total is present but infra>0. That
    row measured product quality just as little as one that never started."""
    rows = {"tokendead": _row(mech_pass=2, mech_total=8, infra=3)}
    base = {"tokendead": _row(mech_pass=8, mech_total=8)}
    _, regressions, unmeasured = _diff(rows, base, tmp_path, monkeypatch)
    assert regressions == [], "credential failure scored as product regression"
    assert [sid for sid, _ in unmeasured] == ["tokendead"]


def test_bake_and_diff_share_one_predicate(tmp_path, monkeypatch):
    """The invariant that prevents the whole class from coming back: a row the
    baking path refuses as uninformative is exactly a row the diff path refuses
    to score. When these two drift apart, the harness lies in one direction or
    the other."""
    rows = {"good": _row(), "setup": _row(mech_pass=0, mech_total=None, infra=1,
                                          setup_error=True, fails=["ERROR:x"]),
            "creds": _row(mech_pass=2, mech_total=8, infra=1),
            "downbad": _row(mech_pass=0, mech_total=8)}
    base = {sid: _row(mech_pass=8, mech_total=8) for sid in rows}
    _, regressions, unmeasured = _diff(rows, base, tmp_path, monkeypatch)
    _, not_bakeable = sweep.bakeable_rows(rows)
    assert sorted(sid for sid, _ in unmeasured) == not_bakeable
    # and the informative-but-failing row is scored, not excused
    assert "downbad" in [sid for sid, _ in regressions]


def test_no_baseline_file_is_not_a_crash(tmp_path, monkeypatch):
    """Degenerate: first run of a mode, no baseline on disk."""
    monkeypatch.setattr(sweep, "BASELINES", tmp_path)
    base, regressions, unmeasured = sweep.diff_vs_baseline(
        {"scenarios": {"a": _row()}}, "haiku")
    assert base is None and regressions == [] and unmeasured == []


def test_unmeasured_rows_named_in_report(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep, "REPORTS", tmp_path)
    scorecard = {"meta": {"commit": "abc", "n_scenarios": 1, "agent_model": "m"},
                 "scenarios": {"broke": _row(mech_pass=0, mech_total=None)}}
    md = sweep.write_report(scorecard, {"broke": _row()}, [], "haiku",
                            "20260101-000002",
                            [("broke", "ERROR:SETUP-ERROR: inputs missing")]).read_text()
    assert "Unmeasured vs baseline: 1" in md
    assert "broke" in md.split("Unmeasured vs baseline")[1]
    assert "not product regression" in md


def test_setup_error_cause_survives_into_the_row():
    """A fixture gap and a dead token are both `infra`, but one banner advising
    'run under fresh creds' for both sends you chasing credentials over a
    missing seed file. The cause must reach the reporting layer."""
    setup = sweep.score_of({"_error": "SETUP-ERROR: declared data_files missing",
                            "_setup_error": True, "_infra": 1})
    creds = sweep.score_of({"_error": "no run dir produced", "_infra": 1})
    assert setup["setup_error"] is True and setup["infra"] == 1
    assert creds["setup_error"] is False


def test_unprovisioned_eval_home_is_refused(monkeypatch, tmp_path):
    """ARMED — the disaster this exists for: a sweep against an ABA_HOME with no
    deployed installation runs happily, the agent refuses work for lack of a
    catalog, and 31 scenarios fail for one reason that is not the product. A run
    that CANNOT measure must refuse, not report a confident zero."""
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    assert sweep.check_eval_home(), "unprovisioned home passed pre-flight"
    # …and the near-miss shape: installation present but the catalog is a stub
    inst = tmp_path / "installation" / "skills"
    inst.mkdir(parents=True)
    for i in range(5):
        (inst / f"s{i}.md").write_text("x")
    (tmp_path / "config.env").write_text("x")
    problems = sweep.check_eval_home()
    assert problems and "UNPROVISIONED" in " ".join(problems), problems


def test_provisioned_eval_home_passes(monkeypatch, tmp_path):
    """The other side: a real home must not be refused, or the guard gets
    disabled the first time it cries wolf."""
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    inst = tmp_path / "installation" / "skills"
    inst.mkdir(parents=True)
    for i in range(sweep.MIN_INSTALLED_SKILLS + 1):
        (inst / f"s{i}.md").write_text("x")
    (tmp_path / "config.env").write_text("x")
    assert sweep.check_eval_home() == []


def test_preflight_predicts_missing_fixture(monkeypatch, tmp_path):
    """Static predictor of the runner's exit-3 staging guard: a declared input
    absent from the scenario's data/ tree cannot survive staging. Catching it
    here costs milliseconds; the runner catches it after a full app boot, and in
    a sweep that verdict lands hours later."""
    monkeypatch.setattr(sweep, "SCEN", tmp_path)
    ok, bad = tmp_path / "ok", tmp_path / "bad"
    (ok / "data").mkdir(parents=True)
    (ok / "data" / "in.csv").write_text("x")
    (ok / "scenario.yaml").write_text("data_files: [in.csv]\nsteps: [a]\n")
    (bad / "data").mkdir(parents=True)
    (bad / "scenario.yaml").write_text("data_files: [in.csv, gone.csv]\nsteps: [a]\n")
    pf = sweep.preflight_fixtures(["ok", "bad"])
    assert set(pf["gaps"]) == {"bad"}
    assert pf["gaps"]["bad"] == ["in.csv", "gone.csv"]
    assert pf["examined"] == 2


def test_preflight_handles_nested_declarations(monkeypatch, tmp_path):
    """WIDE — the degenerate shape that broke the original guard. A declaration
    may carry a subdirectory ("sub/in.csv"); staging copies the subdir in
    wholesale. A top-level-only listing sees just "sub" and calls every nested
    input missing — a false SETUP-ERROR that silently deleted two fully-working
    scenarios from a sweep. Nested must resolve; genuinely-absent must not."""
    monkeypatch.setattr(sweep, "SCEN", tmp_path)
    s = tmp_path / "nested"
    (s / "data" / "sub").mkdir(parents=True)
    (s / "data" / "sub" / "in.csv").write_text("x")
    (s / "scenario.yaml").write_text(
        "data_files: ['sub/in.csv']\nsteps: [a]\n")
    pf = sweep.preflight_fixtures(["nested"])
    assert pf["gaps"] == {}, f"nested declaration falsely flagged: {pf['gaps']}"
    assert pf["examined"] == 1
    # …and the guard still bites when a nested input is truly absent
    (s / "scenario.yaml").write_text(
        "data_files: ['sub/in.csv', 'sub/gone.csv']\nsteps: [a]\n")
    assert sweep.preflight_fixtures(["nested"])["gaps"] == {"nested": ["sub/gone.csv"]}


def test_preflight_reports_when_it_examined_nothing(monkeypatch, tmp_path):
    """ARMED, degenerate: scenarios declaring no inputs make the check vacuous.
    A clean bill from a check that inspected nothing is the failure mode this
    convention exists to stop."""
    monkeypatch.setattr(sweep, "SCEN", tmp_path)
    (tmp_path / "s1").mkdir()
    (tmp_path / "s1" / "scenario.yaml").write_text("steps: [a]\n")
    pf = sweep.preflight_fixtures(["s1"])
    assert pf["gaps"] == {} and pf["examined"] == 0


def test_preflight_skipped_scenario_still_gets_a_row(monkeypatch, tmp_path):
    """Accounting must not quietly shrink: a pre-flight skip produces the SAME
    row the runner's exit-3 would have. A sweep reporting 27/27 because four
    scenarios vanished from the denominator is a lie."""
    row = sweep.score_of({"_error": "SETUP-ERROR: 2 declared data_files absent",
                          "_setup_error": True, "_infra": 1})
    assert row["mech_total"] is None and row["infra"] == 1
    assert row["setup_error"] is True
    _, skipped = sweep.bakeable_rows({"x": row})
    assert skipped == ["x"], "pre-flight row would have been baked into a baseline"


def test_fixture_presence_has_exactly_one_definition():
    """The drift invariant. The sweep's pre-flight and the runner's post-staging
    guard answer the SAME question and must answer it identically — when they
    diverged, the sweep skipped scenarios the runner would have run and the
    runner killed scenarios that were staged correctly. Both must route through
    harness/fixtures.py rather than growing a private copy of the rule."""
    import re
    for f in ("regtest/harness/sweep.py", "regtest/harness/runner.py"):
        src = (ROOT / f).read_text()
        assert "from fixtures import" in src, f"{f}: not using the shared predicate"
        # a private re-derivation of "which declared inputs are present"
        assert not re.search(r"data_files.*\n.*for d in", src), \
            f"{f}: re-deriving declared inputs locally — use declared_inputs()"


def test_shared_predicate_covers_declaration_shapes(tmp_path):
    """WIDE, on the helper itself: flat, nested, mapping-spelled, and absent."""
    sys.path.insert(0, str(ROOT / "regtest" / "harness"))
    from fixtures import declared_inputs, missing_inputs
    (tmp_path / "sub").mkdir()
    (tmp_path / "flat.csv").write_text("x")
    (tmp_path / "sub" / "deep.csv").write_text("x")
    assert missing_inputs(["flat.csv"], tmp_path) == []
    assert missing_inputs(["sub/deep.csv"], tmp_path) == []
    assert missing_inputs(["deep.csv"], tmp_path) == []          # basename match
    assert missing_inputs(["absent.csv"], tmp_path) == ["absent.csv"]
    assert missing_inputs([], tmp_path) == []
    assert missing_inputs(["flat.csv"], tmp_path / "nope") == ["flat.csv"]
    # both spellings a scenario.yaml may use
    assert declared_inputs({"data_files": ["a.csv", {"name": "b.csv"},
                                           {"path": "sub/c.csv"}]}) == \
        ["a.csv", "b.csv", "sub/c.csv"]
    assert declared_inputs({}) == []


def test_accept_ratchets_and_never_lowers_the_bar():
    """Baseline erosion: a dip INSIDE the jitter tolerance is not a regression,
    but accepting it lowers the reference — and after a few such accepts a real
    regression sits below a bar that walked down to meet it. The higher prior
    row is kept, wholesale, so the baseline stays a coherent snapshot."""
    prior = {"dip": _row(mech_pass=10, mech_total=14),
             "rise": _row(mech_pass=10, mech_total=14),
             "fresh": _row(mech_pass=5, mech_total=5)}
    clean = {"dip": _row(mech_pass=9, mech_total=14),      # −1, within tol
             "rise": _row(mech_pass=14, mech_total=14),    # +4, real improvement
             "new": _row(mech_pass=3, mech_total=4)}       # no prior at all
    out, lowered = sweep.ratchet(clean, prior)
    assert out["dip"]["mech_pass"] == 10, "baseline was allowed to drift down"
    assert out["rise"]["mech_pass"] == 14, "improvement was not ratcheted up"
    assert out["new"]["mech_pass"] == 3, "a brand-new row must bake as measured"
    assert len(lowered) == 1 and "dip" in lowered[0]
    assert "10" in lowered[0] and "9" in lowered[0], "the drop is not named"


def test_accept_lower_override_is_honoured():
    """The other side: the ratchet must be defeatable, or a deliberate,
    understood re-baselining becomes impossible and someone edits JSON by hand."""
    prior = {"dip": _row(mech_pass=10, mech_total=14)}
    clean = {"dip": _row(mech_pass=9, mech_total=14)}
    out, lowered = sweep.ratchet(clean, prior, allow_lower=True)
    assert out["dip"]["mech_pass"] == 9 and lowered == []


def test_ratchet_ignores_blind_prior_references():
    """Degenerate: a prior row that errored (mech_total None) is no bar at all —
    it must not block a real measurement from becoming the reference."""
    prior = {"blind": _row(mech_pass=0, mech_total=None)}
    clean = {"blind": _row(mech_pass=7, mech_total=9)}
    out, lowered = sweep.ratchet(clean, prior)
    assert out["blind"]["mech_pass"] == 7 and out["blind"]["mech_total"] == 9
    assert lowered == [], "a blind reference was treated as a bar"


def test_ratchet_rebaselines_when_scenario_shape_changed():
    """PROVEN: the ratchet keys on mech_pass alone; a scenario that legitimately
    SHRINKS (prior 12/14, current fully-passing 8/8) must re-baseline to 8/8 —
    keeping the stale 12/14 row pins a permanent phantom regression ('mech 12→8
    of 8') that only --accept-lower could clear."""
    prior = {"shrunk": _row(mech_pass=12, mech_total=14)}
    clean = {"shrunk": _row(mech_pass=8, mech_total=8)}
    out, lowered = sweep.ratchet(clean, prior)
    assert out["shrunk"]["mech_total"] == 8, "stale total survived the ratchet"
    assert out["shrunk"]["mech_pass"] == 8
    assert lowered == [], "a reshape is not a lowering"
    # same-total dips still ratchet (the original invariant holds)
    out2, low2 = sweep.ratchet({"dip": _row(mech_pass=9, mech_total=14)},
                               {"dip": _row(mech_pass=10, mech_total=14)})
    assert out2["dip"]["mech_pass"] == 10 and len(low2) == 1


def test_corrupt_baseline_is_loud_not_a_crash(monkeypatch, tmp_path, capsys):
    """PROVEN: a truncated/corrupt baseline surfaces as a WARNING with the diff
    skipped — not a JSONDecodeError after a multi-hour run, and not a silent
    fresh-run that hides every regression."""
    monkeypatch.setattr(sweep, "BASELINES", tmp_path)
    (tmp_path / "haiku.json").write_text('{"scenarios": {"s1"')   # truncated
    base, regressions, unmeasured = sweep.diff_vs_baseline(
        {"scenarios": {"s1": _row()}}, "haiku")
    assert base is None and regressions == [] and unmeasured == []
    assert "unreadable" in capsys.readouterr().out.lower()


def test_preflight_home_matches_runner_resolution(monkeypatch, tmp_path):
    """PROVEN seam guard: pre-flight must validate the SAME home the runner
    subprocesses will use. The runner sources ABA_HOME from the ABA_LIVE_ENV
    creds file when unset in the environment — pre-flight checking ~/.aba while
    runners use the env-file home can green-light the exact 'confident zero'
    run it exists to prevent (or falsely abort a good one)."""
    envfile_home = tmp_path / "envfile-home"          # unprovisioned on purpose
    envfile = tmp_path / "live.env"
    envfile.write_bytes(f"ABA_HOME={envfile_home}\0OTHER=x".encode())
    monkeypatch.delenv("ABA_HOME", raising=False)
    monkeypatch.setenv("ABA_LIVE_ENV", str(envfile))
    # a provisioned ~/.aba must NOT mask the env-file home's emptiness
    problems = sweep.check_eval_home()
    assert problems, "pre-flight validated a different home than runners use"
    assert str(envfile_home) in " ".join(problems)


def test_inert_smoke_tags_are_surfaced(monkeypatch, tmp_path):
    """A `smoke: true` tag on a scenario discovery can never return selects
    NOTHING — the tier is smaller than the tag count suggests. Phantom coverage
    is the failure mode this whole file exists to prevent, so it must be named."""
    monkeypatch.setattr(sweep, "SCEN", tmp_path)
    for name, body in (("live", "smoke: true\nsteps: [a]\n"),
                       ("inert", "smoke: true\n"),          # tagged, but v1-only
                       ("plain", "steps: [a]\n")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "scenario.yaml").write_text(body)
    assert sweep.inert_smoke_tags() == ["inert"]
    assert sweep.discover(set(), set(), smoke=True) == ["live"]


def test_live_tree_smoke_tags_are_all_effective():
    """…and on the REAL tree: every smoke tag must actually select its scenario.
    (One tag sat on a v1-only scenario, quietly making a '6-scenario' tier 5.)"""
    inert = sweep.inert_smoke_tags()
    assert not inert, (f"smoke tags that select nothing: {inert} — give the "
                       f"scenario v2 steps or move the tag")


def _run_main(argv, monkeypatch, tmp_path, rows_by_sid):
    """Drive sweep.main() end-to-end with stubbed scenario execution — the
    ORCHESTRATION guard. 25 helper-level tests stayed green while --accept
    crashed on a leftover variable name: the suite proved the parts and never
    the wiring. This exercises the wiring."""
    monkeypatch.setattr(sweep, "BASELINES", tmp_path / "baselines")
    monkeypatch.setattr(sweep, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(sweep, "discover",
                        lambda only, exclude, smoke=False: sorted(rows_by_sid))
    monkeypatch.setattr(sweep, "check_eval_home", lambda: [])
    monkeypatch.setattr(sweep, "preflight_fixtures",
                        lambda scenarios: {"gaps": {}, "examined": len(scenarios)})
    monkeypatch.setattr(sweep, "run_scenario",
                        lambda sid, mode: rows_by_sid[sid])
    monkeypatch.setattr(sweep, "prune_runs", lambda: 0)
    monkeypatch.setattr(sys, "argv", ["sweep.py"] + argv)
    return sweep.main()


def test_accept_path_completes_and_reports(monkeypatch, tmp_path, capsys):
    """PROVEN (NameError on pre-fix code): --accept must run to completion —
    write the baseline, print the confirmation, and return an exit code. The
    pre-fix crash happened AFTER the baseline write, so the operator saw a
    traceback for an accept that had already happened."""
    rc = _run_main(["--accept", "--no-prune"], monkeypatch, tmp_path, {
        "s_ok": {"mechanical": {"pass": 3, "total": 3}, "report": []},
        "s_bad": {"_error": "SETUP-ERROR: x", "_setup_error": True, "_infra": 1},
    })
    out = capsys.readouterr().out
    assert rc == 0, f"accept run should exit 0, got {rc}"
    assert "baseline updated" in out, "accept confirmation never printed"
    bp = tmp_path / "baselines" / "haiku.json"
    assert bp.exists()
    baked = json.loads(bp.read_text())["scenarios"]
    assert "s_ok" in baked and "s_bad" not in baked


def test_plain_run_and_regression_exit_codes(monkeypatch, tmp_path):
    """WIDE on the wiring: exit 0 on a clean run vs 1 on a real regression —
    end-to-end through main(), not through diff_vs_baseline in isolation."""
    rows = {"s1": {"mechanical": {"pass": 4, "total": 4}, "report": []}}
    assert _run_main(["--accept", "--no-prune"], monkeypatch, tmp_path, rows) == 0
    worse = {"s1": {"mechanical": {"pass": 0, "total": 4}, "report": []}}
    assert _run_main(["--no-prune"], monkeypatch, tmp_path, worse) == 1


def test_smoke_tier_filters_and_is_armed():
    """--smoke selects only tagged scenarios; the tier must be ARMED (≥2 tagged
    in the tree) and a strict subset of the full discovery — an empty or
    near-empty smoke tier reads as a fast green sweep that measured nothing."""
    full = set(sweep.discover(set(), set()))
    smoke = set(sweep.discover(set(), set(), smoke=True))
    assert len(smoke) >= 2, "smoke tier unarmed — tag scenarios with smoke: true"
    assert smoke < full, "smoke tier must be a strict subset of full discovery"
    assert len(smoke) <= len(full) // 2, "smoke tier is not a fast subset"


# ── model reporting: the scorecard states what RAN, not what the flag implies ─

def test_observed_model_joins_distinct_and_defaults_none():
    assert sweep._observed_model({}) is None
    assert sweep._observed_model({"a": {"agent_model": None}, "b": {}}) is None
    assert sweep._observed_model({"a": {"agent_model": "m1"}}) == "m1"
    assert sweep._observed_model(
        {"a": {"agent_model": "m2"}, "b": {"agent_model": "m1"},
         "c": {"agent_model": "m1"}}) == "m1+m2", \
        "distinct models must be STATED, never averaged away"


def test_model_truth_banner_fires_only_on_genuine_mismatch(monkeypatch):
    mk = lambda got, assumed: {"meta": {"agent_model": got,
                                        "agent_model_assumed": assumed}}
    assert sweep._model_truth_banner(mk("m", "m")) is None          # match
    assert sweep._model_truth_banner(mk("unknown", "m")) is None    # no read
    assert sweep._model_truth_banner(mk(None, "m")) is None         # absent
    monkeypatch.delenv("ABA_REGTEST_MECH_TOL", raising=False)
    b = sweep._model_truth_banner(mk("big-model", "small-model"))
    assert b and "MODEL MISMATCH" in b
    assert "big-model" in b and "small-model" in b
    monkeypatch.setenv("ABA_REGTEST_MECH_TOL", "3")
    assert "ABA_REGTEST_MECH_TOL=3" in sweep._model_truth_banner(mk("a", "b"))


def test_bundle_and_report_agree_on_the_observed_model():
    """bundle.json kept the flag-derived model after report.json moved to the
    wire read — two forensic files disagreeing about which model ran."""
    src = (ROOT / "regtest" / "harness" / "runner.py").read_text()
    bundle_write = src.split('"bundle.json").write_text')[1][:600]
    assert 'wire_model or os.environ.get("ABA_MODEL")' in bundle_write, (
        "bundle.json's agent_model is not sourced from the wire read that "
        "report.json uses")


# ── tool-argument capture + ceiling constructs (the mech layer grows teeth) ──

import importlib.util as _ilu
import os as _os
import types as _types

_os.environ.setdefault("ABA_SCENARIO", "_selftest_session")
_rspec = _ilu.spec_from_file_location(
    "aba_runner", ROOT / "regtest" / "harness" / "runner.py")
runner = _ilu.module_from_spec(_rspec)
sys.modules["aba_runner"] = runner
_rspec.loader.exec_module(runner)


def _sse(events):
    lines = ["data: " + json.dumps(e) for e in events]
    return _types.SimpleNamespace(iter_lines=lambda: iter(lines))


def test_consume_captures_tool_arguments_capped():
    cap = {"run_id": None, "text": [], "tools": [], "tool_calls": [],
           "entities": [], "usage": {}, "kinds": {}, "tool_errors": [],
           "errors": [], "resume_hops": 0, "jobs": []}
    runner.consume(_sse([
        {"type": "tool_start", "name": "run_r",
         "input": {"env": "sandbox", "code": "X" * 5000}},
        {"type": "tool_start", "name": "run_python", "input": {}},
    ]), cap)
    assert cap["tools"] == ["run_r", "run_python"]
    assert cap["tool_calls"][0]["args"]["env"] == "sandbox"
    assert len(cap["tool_calls"][0]["args"]["code"]) == 200   # capped, not raw
    assert cap["tool_calls"][1]["args"] == {}                 # absent shape


class _NullClient:
    """run_checks fetches the thread manifest unconditionally; the argument
    checks under test never read it."""
    def get(self, *a, **k):
        return _types.SimpleNamespace(json=lambda: {})


def _mech(exp, tools=(), calls=()):
    step = {"expect": exp}
    cap = {"text": "", "tools": list(tools), "tool_calls": list(calls)}
    return runner.run_checks(step, cap, {}, [], _NullClient(), "", "", [], [])


def test_tool_max_calls_is_a_real_ceiling():
    fails = _mech({"tool_max_calls": {"make_isolated_env": 1}},
                  tools=["make_isolated_env"] * 3)
    assert any(f.startswith("tool_called_too_often:make_isolated_env")
               for f in fails), fails
    assert _mech({"tool_max_calls": {"make_isolated_env": 1}},
                 tools=["make_isolated_env"]) == []


def test_tool_arg_absent_checks_every_call():
    exp = {"tool_arg_absent": [{"tool": "run_python", "arg": "env"}]}
    ok = [{"name": "run_python", "args": {"code": "1"}}]
    bad = ok + [{"name": "run_python", "args": {"code": "2", "env": "sandbox"}}]
    assert _mech(exp, calls=ok) == []
    fails = _mech(exp, calls=bad)
    assert any(f.startswith("tool_arg_present:run_python.env") for f in fails)
    assert _mech(exp, calls=[]) == []      # vacuous — floors are tools_used's job


def test_tool_arg_equals_matches_some_call_or_stays_vacuous():
    exp = {"tool_arg_equals": [{"tool": "set_active_env",
                                "arg": "language", "value": "r"}]}
    hit = [{"name": "set_active_env", "args": {"name": "e", "language": "r"}}]
    miss = [{"name": "set_active_env", "args": {"name": "e"}}]
    assert _mech(exp, calls=hit) == []
    assert any(f.startswith("tool_arg_mismatch:set_active_env.language")
               for f in _mech(exp, calls=miss))
    assert _mech(exp, calls=[]) == []      # never called → the floor check owns it
