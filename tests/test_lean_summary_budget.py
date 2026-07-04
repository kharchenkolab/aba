"""Lean spec's two-tier history compression + the half-window invariant.

Three concerns:

  1. AgentSpec.summary_budget_chars round-trips through the YAML
     loader. None preserves today's global default; a positive int
     overrides per-spec.

  2. budget_summary._threshold(budget_chars=X) returns X when given,
     falls back to global default otherwise. Caller-supplied 0 / None
     is the back-compat path.

  3. The lean spec's static load (system prompt + tools JSON) must
     stay ≤ HALF of the target 40,960-token vLLM window — the user's
     explicit budget rule ("the rest is for memory + actual work").
     This is the regression bar that protects lean's reason to exist.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_lean_budget_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "lb.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))


pytestmark = pytest.mark.bio


# ── 1. AgentSpec.summary_budget_chars round-trip ─────────────────────
def _write_yaml(d: Path, name: str, body: str) -> Path:
    p = d / f"{name}.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_loader_defaults_summary_budget_to_none(tmp_path):
    from core.runtime.agent import load_agent_spec
    p = _write_yaml(tmp_path, "x", """
        name: x
        role: primary
        model: claude-haiku-4-5-20251001
        system_prompt: identity.md
        manifest_role: x
        tool_allowlist: ['*']
        """)
    spec = load_agent_spec(p)
    assert spec.summary_budget_chars is None


def test_loader_reads_summary_budget_int(tmp_path):
    from core.runtime.agent import load_agent_spec
    p = _write_yaml(tmp_path, "y", """
        name: y
        role: primary
        model: claude-haiku-4-5-20251001
        system_prompt: identity.md
        manifest_role: y
        tool_allowlist: ['*']
        summary_budget_chars: 25000
        """)
    spec = load_agent_spec(p)
    assert spec.summary_budget_chars == 25000


def test_shipped_lean_guide_has_summary_budget():
    import content.bio  # noqa: F401
    from core.runtime.agent import get_agent_spec
    lean = get_agent_spec("lean_guide")
    assert lean is not None
    # Whatever the value is, it must be set and much smaller than the
    # global default (400k) — that's the whole point.
    assert lean.summary_budget_chars is not None
    assert 5_000 <= lean.summary_budget_chars <= 100_000, (
        f"lean budget {lean.summary_budget_chars} is outside the sane "
        "range for a 40k-window backend")


# ── 2. _threshold override behavior ──────────────────────────────────
def test_threshold_with_override_returns_caller_value():
    from core.summarize.budget_summary import _threshold
    assert _threshold(25_000) == 25_000


def test_threshold_no_override_returns_global_default():
    from core.summarize.budget_summary import _threshold
    from core.config import HISTORY_SUMMARY_THRESHOLD_CHARS
    assert _threshold(None) == HISTORY_SUMMARY_THRESHOLD_CHARS
    assert _threshold() == HISTORY_SUMMARY_THRESHOLD_CHARS


def test_threshold_rejects_zero_falls_back():
    """0 (and negative) is treated as "no override" — defends against
    a YAML row that accidentally evaluates to 0 disabling tier-2."""
    from core.summarize.budget_summary import _threshold
    from core.config import HISTORY_SUMMARY_THRESHOLD_CHARS
    assert _threshold(0) == HISTORY_SUMMARY_THRESHOLD_CHARS


def test_maybe_summarize_uses_override(monkeypatch):
    """End-to-end: when budget_chars is small, maybe_summarize fires
    its threshold check at the lower bar. We don't actually invoke
    the LLM here — we just verify the threshold gate is consulted."""
    from core.summarize import budget_summary as bs
    # Small messages list — well under 400k but over our tiny budget.
    msgs = [{"role": "user", "content": "x" * 200}] * 30  # ~6k chars
    # With default budget (400k) → maybe_summarize returns input
    # unchanged (no summarization needed). With budget_chars=2000 it
    # should attempt to summarize. We monkeypatch _synthesize to a
    # stub so no real Anthropic call happens.
    # Use monkeypatch so these revert at end-of-test instead of
    # leaking module-level stubs that poison later test files.
    monkeypatch.setattr(bs, "_synthesize", lambda *a, **k: "STUB SUMMARY")
    monkeypatch.setattr(bs, "_save",       lambda *a, **k: None)
    monkeypatch.setattr(bs, "_load",       lambda *a, **k: None)
    out_default = bs.maybe_summarize("thr_test", list(msgs))
    out_override = bs.maybe_summarize("thr_test", list(msgs),
                                       budget_chars=2_000)
    assert out_default == msgs, "default should not summarize 6k chars"
    # Override should fire summarization → output messages != input
    assert out_override != msgs
    # And the summary placeholder should be in the user-role frame
    # the synthesizer wraps it in.
    payload = json.dumps(out_override)
    assert "STUB SUMMARY" in payload


# ── 3. half-window invariant ─────────────────────────────────────────
WINDOW = 40_960            # the bumped vLLM max_model_len for Qwen3-8B
HALF   = WINDOW // 2       # the user's "base context ≤ half" rule


_PRIORITY_TOOLS = (
    "run_python", "run_r",
    "Skill", "search_skills",
    "present_plan", "ask_clarification",
    "register_dataset", "list_data_files", "find_files",
    "ensure_capability", "describe_tool",
)


def _measure_static_tokens(spec_name: str) -> tuple[int, int, int]:
    """Reproduce the runtime's measurement: build_system + filter by
    allowlist + COMPACT the tools list when prompt_mode == "lean".
    Char ÷ 4 ≈ tokens.

    Post-2026-06-20 redesign: lean's reach is the full catalog
    (tool_allowlist '*'); the savings come from `compact=True` at
    list_tools, NOT from a curated list. The measurement must mirror
    that to stay honest about what the model actually receives."""
    import content.bio  # noqa: F401
    from core.runtime.mcp import (register_inprocess_server,
                                  _reset_for_testing, list_tools)
    from content.bio.mcp_servers.aba_core import make_server
    from content.bio.prompts.build import build_system
    from core.runtime.agent import (get_agent_spec,
                                     filter_tools_by_allowlist)
    try:
        register_inprocess_server("aba_core", make_server,
                                  expose_in_catalog=True,
                                  strip_prefix_in_catalog=True)
    except Exception:                                       # noqa: BLE001
        pass
    spec = get_agent_spec(spec_name)
    assert spec is not None, f"{spec_name!r} not registered"
    # Measure the REAL policy the agent gets for its prompt_mode (single-source
    # ToolPresentationPolicy), not a legacy compact bool — so the budget reflects
    # what the lean agent actually receives (lean drops input_schema param prose;
    # standard/full keep it). See core.runtime.mcp.presentation.
    tools_all = list_tools(mode=spec.prompt_mode, priority_tools=_PRIORITY_TOOLS)
    tools = filter_tools_by_allowlist(tools_all, spec.tool_allowlist)
    stable, dyn = build_system(
        tools, role=spec.manifest_role or "primary",
        intent="list the data files",
        ctx={"thread_id": "t"}, mode=spec.prompt_mode,
    )
    sys_chars   = len(stable) + (len("\n\n") + len(dyn) if dyn else 0)
    tools_json  = json.dumps(tools)
    tools_chars = len(tools_json)
    return sys_chars // 4, tools_chars // 4, (sys_chars + tools_chars) // 4


def test_lean_static_under_half_window():
    """The user's rule: base context (system + tools list) must be
    ≤ half the vLLM window. Otherwise the rest of the window has no
    room for memory, plan state, and actual conversation work."""
    sys_tok, tools_tok, total_tok = _measure_static_tokens("lean_guide")
    assert total_tok <= HALF, (
        f"lean static load {total_tok:,} tokens > half-window "
        f"({HALF:,}). System={sys_tok:,} tools={tools_tok:,}. "
        f"Cut tools from the allowlist or drop more prompt blocks.")


def test_lean_static_meaningfully_smaller_than_full():
    """Lean must be substantially smaller than the full guide.

    Bar: ≥ 30% reduction. Previously this was ≥ 50% (tuned for the
    curated 18-tool allowlist), but the 2026-06-20 redesign restored
    full functional reach (all 60 tools visible) and switched savings
    to description compaction + prompt-block drops. The new ceiling
    is ~40% because we can't compress what isn't there. Lean's value
    isn't being TINY anymore — it's fitting the half-window WITHOUT
    masking function (see test_lean_static_under_half_window)."""
    _, _, full_tok = _measure_static_tokens("guide")
    _, _, lean_tok = _measure_static_tokens("lean_guide")
    reduction = (full_tok - lean_tok) / max(full_tok, 1)
    assert reduction >= 0.30, (
        f"lean reduced static load by only {reduction:.1%} "
        f"(full={full_tok:,} lean={lean_tok:,}); expected ≥ 30%")


def test_full_static_documented():
    """The full guide ALREADY busts the half-window rule — this test
    pins that fact so a future change to the full guide doesn't
    silently regress the lean-vs-full delta. If this fails because
    full got smaller, that's good news; tighten the bound."""
    _, _, full_tok = _measure_static_tokens("guide")
    # Measured at ~25k tokens (2026-06-19); leave a small margin.
    assert full_tok > HALF, (
        f"full guide static load {full_tok:,} now fits in half-window — "
        f"good news! Tighten this regression bound.")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
