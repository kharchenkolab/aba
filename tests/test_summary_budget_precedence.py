"""Tier-2 summary-budget precedence (release_test_plan queue item 6).

An EXPLICITLY SET ABA_HISTORY_SUMMARY_THRESHOLD_CHARS must override a
spec's pinned summary_budget_chars — before the fix the env knob was
silently inert for any spec that pinned a budget (grounded_guide pins
100k), which produced a vacuous compaction-study round: the operator set
the knob, nothing changed, and no surface said why.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault(
    "ABA_DB_PATH", os.path.join(tempfile.mkdtemp(prefix="aba_sbp_"), "t.db"))

from guide import _summary_budget  # noqa: E402

_PINNED = SimpleNamespace(summary_budget_chars=100_000)
_UNPINNED = SimpleNamespace(summary_budget_chars=None)


def _with_env(val, fn):
    old = os.environ.pop("ABA_HISTORY_SUMMARY_THRESHOLD_CHARS", None)
    try:
        if val is not None:
            os.environ["ABA_HISTORY_SUMMARY_THRESHOLD_CHARS"] = val
        return fn()
    finally:
        os.environ.pop("ABA_HISTORY_SUMMARY_THRESHOLD_CHARS", None)
        if old is not None:
            os.environ["ABA_HISTORY_SUMMARY_THRESHOLD_CHARS"] = old


def test_env_set_overrides_spec_pin():
    assert _with_env("6000", lambda: _summary_budget(_PINNED)) == 6000


def test_spec_pin_wins_when_env_unset():
    assert _with_env(None, lambda: _summary_budget(_PINNED)) == 100_000


def test_no_spec_no_env_falls_through_to_global():
    assert _with_env(None, lambda: _summary_budget(None)) is None
    assert _with_env(None, lambda: _summary_budget(_UNPINNED)) is None


def test_garbage_env_value_falls_back_to_spec():
    assert _with_env("not-a-number", lambda: _summary_budget(_PINNED)) == 100_000


def test_empty_env_value_is_unset():
    assert _with_env("", lambda: _summary_budget(_PINNED)) == 100_000
