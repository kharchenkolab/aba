"""History-transform prefix stability (caching bug #2 guard).

THE invariant: generation N+1's effective message list must START WITH exactly
generation N's (prefix-extension). Prompt caching is prefix-matched, so any
transform that rewrites already-sent content re-bills the whole retained
history every generation (measured live: 411k cache_write tokens in one turn).

Per the module's own convention, the shape guard (which stubs _synthesize)
rides WITH _TIER2_DIAG counter assertions, never instead of them — and asserts
its own precondition armed (transform engaged), so a mis-knobbed run can't
pass vacuously.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
_tmp = tempfile.mkdtemp(prefix="aba_prefix_stab_")
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_tmp, "t.db"))

from core.summarize import budget_summary as bs  # noqa: E402
from core.summarize.pruning import prune_transcript  # noqa: E402

pytestmark = pytest.mark.platform

BUDGET = 4_000          # small so the transform engages quickly
TAIL = 4


def _grow(history: list, i: int) -> None:
    """One generation's growth: assistant(tool_use) + user(tool_result)."""
    history.append({"role": "assistant", "content": [
        {"type": "text", "text": f"step {i}"},
        {"type": "tool_use", "id": f"t{i}", "name": "run_python",
         "input": {"code": "x" * 120}}]})
    history.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"t{i}",
         "content": f"result {i}: " + "y" * 160}]})


def _is_prefix(prev: list, cur: list) -> bool:
    return len(cur) >= len(prev) and cur[:len(prev)] == prev


def _stub_synth(monkeypatch):
    calls = {"n": 0}

    def fake(thread_id, old_block, prior_summary=None):
        bs._TIER2_DIAG["calls"] += 1
        calls["n"] += 1
        return f"<summary>\ncovers {len(old_block)} more msgs "\
               f"(prior={'y' if prior_summary else 'n'})\n</summary>"
    monkeypatch.setattr(bs, "_synthesize", fake)
    return calls


def test_tier2_output_is_prefix_extension_per_generation(monkeypatch, tmp_path):
    calls = _stub_synth(monkeypatch)
    hist: list = [{"role": "user", "content": [{"type": "text", "text": "go"}]}]
    prev = None
    reuse_gens = 0
    for i in range(30):
        _grow(hist, i)
        n_before = calls["n"]
        eff = bs.maybe_summarize("thrPS", list(hist),
                                 budget_chars=BUDGET, tail_keep=TAIL)
        synthesized = calls["n"] > n_before
        # A (re)synthesis is the sanctioned ONCE-PER-EPOCH rewrite (compaction
        # must rewrite the prefix exactly when it folds). Every OTHER
        # generation must be a strict prefix-extension of the previous one —
        # that is the property caching requires, and what the sliding-window
        # code violated on every generation.
        if prev is not None and not synthesized:
            assert _is_prefix(prev, eff), (
                f"gen {i}: NO synthesis ran, yet the effective history is not "
                f"a prefix-extension of gen {i-1}'s — the retained history "
                f"re-bills for nothing")
            reuse_gens += 1
        prev = eff
    # arming assertions: engagement, reuse, and epochs being RARE
    assert calls["n"] >= 1, "budget never crossed — test measured nothing"
    assert bs._TIER2_DIAG["reused"] > 0, "reuse path never exercised"
    assert reuse_gens >= 10, f"only {reuse_gens} reuse generations — epochs not rare"


def test_tier2_synthesizes_once_not_per_generation(monkeypatch):
    calls = _stub_synth(monkeypatch)
    hist: list = []
    for i in range(24):
        _grow(hist, i)
        bs.maybe_summarize("thrPC", list(hist), budget_chars=BUDGET, tail_keep=TAIL)
    assert calls["n"] <= 3, (
        f"{calls['n']} synth calls across 24 generations — the store must be "
        f"REUSED, not re-derived per generation (latency defect)")


def test_synth_failure_degrades_to_stored_summary_not_cliff(monkeypatch):
    _stub_synth(monkeypatch)
    hist: list = []
    for i in range(20):
        _grow(hist, i)
        eff = bs.maybe_summarize("thrCF", list(hist), budget_chars=BUDGET, tail_keep=TAIL)
    assert len(eff) < len(hist)                      # summary active
    monkeypatch.setattr(bs, "_synthesize", lambda *a, **k: "")   # synth now fails
    for i in range(20, 30):
        _grow(hist, i)
    eff2 = bs.maybe_summarize("thrCF", list(hist), budget_chars=BUDGET, tail_keep=TAIL)
    assert len(eff2) < len(hist), (
        "synth failure dumped the FULL history (the bail-out cliff) instead of "
        "serving the stale-but-stable stored summary")
    assert bs._TIER2_DIAG["reused_on_fail"] >= 1


def test_boundary_is_monotonic(monkeypatch):
    _stub_synth(monkeypatch)
    hist: list = []
    covs = []
    for i in range(30):
        _grow(hist, i)
        bs.maybe_summarize("thrMONO", list(hist), budget_chars=BUDGET, tail_keep=TAIL)
        row = bs._load("thrMONO")
        if row:
            covs.append(row[0])
    assert covs and all(b >= a for a, b in zip(covs, covs[1:])), covs


def test_tier1_prune_prefix_stability_documented():
    """Tier-1 stubs old tool_results by RECENCY window, so a message flips
    verbatim→stub when it falls off the K-recent window — a mid-list rewrite.
    This test DOCUMENTS the measured behavior either way: if it starts failing
    in the stable direction, tighten the guard; if it fails unstable, that's
    caching bug #2b surfacing and needs the Tier-2 treatment (freeze stubs
    once made)."""
    hist: list = []
    prev = None
    diverged_at: list = []
    for i in range(30):
        _grow(hist, i)
        eff = prune_transcript(list(hist), k_tool_keep=5, k_text_keep=3)
        if prev is not None and not _is_prefix(prev, eff):
            diverged_at.append(i)
        prev = eff
    # Current truth (measured, not assumed): the recency window makes a
    # tool_result flip verbatim→stub when it falls off the K-recent window —
    # a mid-list rewrite on essentially every generation past K. This is
    # caching bug #2b: same class as Tier-2's sliding window, smaller radius
    # (invalidates from ~K pairs back, not from message 0). Recorded here as
    # a failing-direction sentinel: when Tier-1 is fixed (freeze stubs once
    # made), flip this to assert `not diverged_at`.
    assert diverged_at, (
        "Tier-1 measured prefix-STABLE with the window engaged — the #2b bug "
        "appears fixed: flip this test to assert stability permanently")
