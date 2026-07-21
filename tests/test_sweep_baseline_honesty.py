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


def _row(mech_pass=3, mech_total=4, infra=0, fails=()):
    return {"mech_pass": mech_pass, "mech_total": mech_total,
            "rubric_overall": None, "fails": list(fails),
            "bundle": None, "infra": infra}


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
