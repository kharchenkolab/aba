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
    for i in range(40):
        _grow(hist, i)
        eff = prune_transcript(list(hist), k_tool_keep=5, k_text_keep=3,
                               stub_batch=5, drop_batch=3)
        if prev is not None and not _is_prefix(prev, eff):
            diverged_at.append(i)
        prev = eff
    # Fixed (#2b): the demotion boundary is QUANTIZED, so between batch
    # boundaries the output is a strict prefix-extension; divergence happens
    # only at epoch jumps. 40 generations with stub_batch=5 → a handful of
    # epochs, never per-generation.
    assert len(diverged_at) <= 9, (
        f"Tier-1 diverged at {len(diverged_at)} generations {diverged_at} — "
        f"the sliding window is back (bug #2b): demotion must advance in "
        f"quantized batches, not per generation")
    assert diverged_at, "window never engaged — test measured nothing"
    gaps = [b - a for a, b in zip(diverged_at, diverged_at[1:])]
    assert gaps and min(gaps) >= 2, (
        f"epoch jumps every generation ({diverged_at}) — quantization inert")


def test_saturated_regime_stays_prefix_stable(monkeypatch):
    """The regime the LIVE incident was in: one message larger than the whole
    budget sits in the tail, so `chars(remainder) + summary <= budget` is
    unsatisfiable at ANY boundary. Pre-rule, the code fell through to
    advance-and-synthesize EVERY generation — full regression to sliding-window
    behavior (verified 30/30 divergences). The rule: when even maximum coverage
    cannot fit, serve the STORED summary verbatim (over-budget but byte-stable,
    `saturated` counter) and advance only once per quantized epoch."""
    calls = _stub_synth(monkeypatch)
    hist: list = []
    for i in range(8):                      # build a store under headroom first
        _grow(hist, i)
        bs.maybe_summarize("thrSAT", list(hist), budget_chars=BUDGET, tail_keep=TAIL)
    # one oversized message — bigger than the entire budget — enters the tail
    hist.append({"role": "user", "content": [
        {"type": "text", "text": "IMG:" + "z" * (BUDGET + 2000)}]})
    prev = None
    divergences = 0
    calls_before = calls["n"]
    for i in range(8, 28):
        _grow(hist, i)
        eff = bs.maybe_summarize("thrSAT", list(hist),
                                 budget_chars=BUDGET, tail_keep=TAIL)
        if prev is not None and not _is_prefix(prev, eff):
            divergences += 1
        prev = eff
    sat_calls = calls["n"] - calls_before
    assert bs._TIER2_DIAG.get("saturated", 0) > 0, \
        "saturated path never taken — the regime is undetected"
    # Sanctioned epochs only: quantum advances while saturated, plus the
    # entry/exit transitions as the oversized message crosses the tail window.
    # Pre-rule this was 20/20 (per-generation rewrite + synthesis).
    assert divergences <= 5, (
        f"{divergences} divergences in 20 saturated generations — "
        f"per-generation rewrite is back (the live-incident regime)")
    assert sat_calls <= 5, (
        f"{sat_calls} synth calls in 20 saturated generations — "
        f"per-generation synthesis is back (the latency defect)")


def test_saturated_from_first_engagement(monkeypatch):
    """Oversized message present BEFORE any store exists: first engagement may
    synthesize once to establish the store; after that, stability."""
    calls = _stub_synth(monkeypatch)
    hist: list = [{"role": "user", "content": [
        {"type": "text", "text": "IMG:" + "z" * (BUDGET + 2000)}]}]
    prev = None
    divergences = 0
    for i in range(20):
        _grow(hist, i)
        eff = bs.maybe_summarize("thrSAT2", list(hist),
                                 budget_chars=BUDGET, tail_keep=TAIL)
        if prev is not None and not _is_prefix(prev, eff):
            divergences += 1
        prev = eff
    assert divergences <= 3, f"{divergences} divergences from first engagement"
    assert calls["n"] <= 3, f"{calls['n']} synth calls"


def _grow_image(history: list, i: int, kb: int = 8):
    history.append({"role": "assistant", "content": [
        {"type": "tool_use", "id": f"v{i}", "name": "view_file",
         "input": {"path": f"plot_{i}.png"}}]})
    history.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": f"v{i}", "content": [
            {"type": "image", "source": {"type": "base64",
             "media_type": "image/png", "data": "A" * (kb * 1024)}}]}]})


def test_image_blocks_age_out_of_history():
    """A vision payload is consumed once; beyond k_image_keep results it must
    demote to a text stub carrying the re-view reference — while RECENT images
    stay verbatim for the generation(s) that need them."""
    hist: list = []
    _grow_image(hist, 0)
    for i in range(1, 8):
        _grow(hist, i)
    eff = prune_transcript(list(hist), k_image_keep=4)
    inner = eff[1]["content"][0]["content"]           # tool_result's content list
    assert inner[0]["type"] == "text" and "re-view via view_file" in inner[0]["text"]
    assert "plot_0.png" in inner[0]["text"]            # the reference survives
    # a FRESH image stays verbatim
    _grow_image(hist, 99)
    eff2 = prune_transcript(list(hist), k_image_keep=4)
    assert eff2[-1]["content"][0]["content"][0]["type"] == "image"


def test_image_demotion_is_one_rewrite_then_stable():
    hist: list = []
    _grow_image(hist, 0)
    prev = None
    divergences = 0
    for i in range(1, 14):
        _grow(hist, i)
        eff = prune_transcript(list(hist), k_image_keep=4)
        if prev is not None and not _is_prefix(prev, eff):
            divergences += 1
        prev = eff
    assert divergences == 1, (
        f"{divergences} divergences for one aging image — must rewrite exactly "
        f"once (when it crosses the age window), then stay byte-stable")


def test_aged_image_clears_tier2_saturation(monkeypatch):
    """End-to-end across both tiers: a large image saturates the Tier-2 budget
    while verbatim; after Tier-1 demotes it, the remainder fits again and the
    saturated counter stops climbing."""
    calls = _stub_synth(monkeypatch)
    hist: list = []
    for i in range(6):
        _grow(hist, i)
    _grow_image(hist, 6, kb=8)                        # ~8KB >> BUDGET
    sat_before = bs._TIER2_DIAG["saturated"]
    for i in range(7, 18):
        _grow(hist, i)
        pruned = prune_transcript(list(hist), k_image_keep=4)
        bs.maybe_summarize("thrIMG", pruned, budget_chars=BUDGET, tail_keep=TAIL)
    # once the image aged out (k_image_keep=4 results ≈ 4 generations), the
    # effective history shrinks below budget → reuse resumes, saturation stops
    pruned = prune_transcript(list(hist), k_image_keep=4)
    assert not any(
        isinstance(b, dict) and b.get("type") == "tool_result"
        and isinstance(b.get("content"), list)
        and any(isinstance(x, dict) and x.get("type") == "image" for x in b["content"])
        for m in pruned for b in (m.get("content") or [])), "image never demoted"
    sat_tail = bs._TIER2_DIAG["saturated"]
    for i in range(18, 22):
        _grow(hist, i)
        pruned = prune_transcript(list(hist), k_image_keep=4)
        bs.maybe_summarize("thrIMG", pruned, budget_chars=BUDGET, tail_keep=TAIL)
    assert bs._TIER2_DIAG["saturated"] == sat_tail, (
        "saturation persists after the image demoted — the two tiers aren't "
        "composing (upstream cap ineffective)")


def test_marginal_band_reuses_in_epochs(monkeypatch):
    """The THIRD regime (between headroom and saturation): the tail fits the
    budget, but the post-advance remainder sits just under it, so pre-slack the
    reuse check failed one generation after every advance — re-synthesis every
    generation (30/39 measured at 12k/tail-20). The char-space slack must buy
    several reuse generations per fold, with the saturated path staying cold
    (this band is NOT saturation — that distinction is the finding)."""
    calls = _stub_synth(monkeypatch)
    hist: list = []
    for i in range(30):
        _grow(hist, i)
    tail_chars = bs._message_chars(hist[-8:])
    budget = tail_chars + 400              # tail fits; remainder won't, for long
    sat_before = bs._TIER2_DIAG["saturated"]
    prev = None
    divergences = 0
    calls_before = calls["n"]
    for i in range(30, 50):
        _grow(hist, i)
        eff = bs.maybe_summarize("thrMARG", list(hist),
                                 budget_chars=budget, tail_keep=8)
        if prev is not None and not _is_prefix(prev, eff):
            divergences += 1
        prev = eff
    band_calls = calls["n"] - calls_before
    assert bs._TIER2_DIAG["reused"] > 0, "reuse never fired in the marginal band"
    assert bs._TIER2_DIAG["saturated"] == sat_before, (
        "saturated path fired — this scenario drifted out of the marginal band "
        "and no longer tests it")
    assert divergences <= 5, (
        f"{divergences} divergences in 20 marginal generations — "
        f"per-generation re-synthesis is back (the third-regime gap)")
    assert band_calls <= 5, f"{band_calls} synth calls in 20 marginal generations"


def test_reuse_overshoot_stays_within_its_stated_bound(monkeypatch):
    """The reuse path deliberately runs OVER budget to stay byte-stable, bounded
    by a slack quantum. Assert the bound it claims.

    Nothing checked this before, and it was false: the check budgeted the raw
    summary TEXT while the returned list carries the handoff-framed summary
    MESSAGE (~216 chars more), so the retained history exceeded threshold+slack
    by that constant. A stated numeric invariant with no assertion behind it is
    a comment, not a bound — and this one drifts furthest from truth exactly
    when slack is tightened toward its floor."""
    _stub_synth(monkeypatch)
    hist: list = [{"role": "user", "content": [{"type": "text", "text": "go"}]}]
    thr = bs._threshold(BUDGET)
    slack = min(8_000, max(1_500, thr // 4))
    worst = 0
    for i in range(30):
        _grow(hist, i)
        eff = bs.maybe_summarize("thrBOUND", list(hist),
                                 budget_chars=BUDGET, tail_keep=TAIL)
        worst = max(worst, bs._message_chars(eff))
    assert bs._TIER2_DIAG["reused"] > 0, "reuse path never ran — bound untested"
    assert worst <= thr + slack, (
        f"retained history reached {worst} chars, over the stated bound "
        f"threshold({thr}) + slack({slack}) = {thr + slack}")
