"""The rate gate must not bless a run that cannot support the claim.

A rate is the acceptance criterion for changing a shared agent input, so the
ways it can lie matter more than the ways it can pass: a run with no trials for
the gated scenario, or with too few to mean anything, must be UNUSABLE — not a
quiet pass. That distinction is the whole reason the gate exists rather than a
line of shell computing a fraction.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "regtest" / "placement"))
from check_rates import rate  # noqa: E402


def _trial(name, *gpu_flags):
    return {"name": name,
            "decisions": [{"input": {"est_gpu": g}} for g in gpu_flags]}


def test_a_trial_hits_if_any_decision_asked():
    """The agent explores first and submits second — several exec calls per
    trial, only one of which carries the estimate. Counting DECISIONS would let
    a chatty trial outvote a decisive one."""
    rows = [_trial("s", None, None, None, True)]
    assert rate(rows, "s", "est_gpu") == (1, 1)


def test_a_trial_misses_when_no_decision_asked():
    assert rate([_trial("s", None, False)], "s", "est_gpu") == (0, 1)


def test_counts_trials_not_decisions():
    rows = [_trial("s", True, True, True), _trial("s", None)]
    assert rate(rows, "s", "est_gpu") == (1, 2)


def test_other_scenarios_are_not_counted():
    """The store of results holds every scenario in the run; a gate that swept
    them all together would average away the one being measured."""
    rows = [_trial("s", True), _trial("other", None), _trial("other", None)]
    assert rate(rows, "s", "est_gpu") == (1, 1)


def test_a_scenario_with_no_trials_reports_zero_of_zero():
    """…which the CLI turns into UNUSABLE rather than 0% — a missing scenario
    is not a failing one, and must not read as either a pass or a fail."""
    assert rate([_trial("other", True)], "s", "est_gpu") == (0, 0)


def test_a_trial_that_made_no_decisions_still_counts_as_a_trial():
    """ARMED against the opposite error: dropping decision-less trials would
    quietly raise every rate, because the runs where the agent did nothing are
    exactly the ones a placement gate must not forgive."""
    rows = [{"name": "s", "decisions": []}, _trial("s", True)]
    assert rate(rows, "s", "est_gpu") == (1, 2)
