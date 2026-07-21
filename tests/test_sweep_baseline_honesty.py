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


def test_smoke_tier_filters_and_is_armed():
    """--smoke selects only tagged scenarios; the tier must be ARMED (≥2 tagged
    in the tree) and a strict subset of the full discovery — an empty or
    near-empty smoke tier reads as a fast green sweep that measured nothing."""
    full = set(sweep.discover(set(), set()))
    smoke = set(sweep.discover(set(), set(), smoke=True))
    assert len(smoke) >= 2, "smoke tier unarmed — tag scenarios with smoke: true"
    assert smoke < full, "smoke tier must be a strict subset of full discovery"
    assert len(smoke) <= len(full) // 2, "smoke tier is not a fast subset"
