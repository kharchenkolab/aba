"""Lean prompt-mode plumbing across build_system + AgentSpec + lean_guide.yaml.

Three layers:
  1. build_system(mode='lean') drops the right blocks AND swaps
     behavior.md → behavior_slim.md. build_system(mode='full') matches
     today's output bit-for-bit.
  2. AgentSpec loader reads `prompt_mode:` from YAML, defaults to
     'full', rejects unknown values.
  3. The shipped lean_guide.yaml loads with the expected fields and
     registers under "lean_guide" alongside "guide" (multi-primary
     coexistence — exercised by resolve_primary_spec_name elsewhere).
"""
from __future__ import annotations
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_lean_mode_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "lm.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))


pytestmark = pytest.mark.bio


# ── helpers ─────────────────────────────────────────────────────────
def _mk_tools(names):
    """Stub tools list shaped like list_tools() output."""
    return [{"name": n, "description": f"stub {n}", "input_schema": {}} for n in names]


# A representative active-tool set that hits the gates of every
# primary-only block: Skill (skills_core, recipe_arm, skills_recipes),
# read_memory (memory), present_plan (plan_first, declared_recipes).
_TOOLS = _mk_tools(["Skill", "read_memory", "present_plan", "run_python"])


# ── 1. build_system mode behavior ────────────────────────────────────
def test_full_mode_keeps_behavior_full_and_includes_dynamic_block():
    from content.bio.prompts.build import build_system
    stable, dynamic = build_system(
        _TOOLS, role="primary", intent="fit single-cell QC pipeline",
        ctx={"thread_id": "t", "focus_is_figure": False,
             "highlight_active": False}, mode="full",
    )
    # behavior.md ≈ 13.4k chars; behavior_slim.md ≈ 8.7k. The
    # full-mode stable block should be larger than slim alone.
    assert len(stable) > 12_000, len(stable)
    # The BM25 recipes catalog (skills_recipes block, dynamic=True)
    # emits some text whenever recipes are registered. May be empty in
    # a tmp registry — accept either shape.
    assert dynamic == "" or "skill" in dynamic.lower()


def test_lean_mode_drops_heavy_blocks_and_dynamic_tail():
    from content.bio.prompts.build import build_system, _LEAN_DROP
    stable, dynamic = build_system(
        _TOOLS, role="primary", intent="fit single-cell QC pipeline",
        ctx={"thread_id": "t", "focus_is_figure": False,
             "highlight_active": False}, mode="lean",
    )
    # Dynamic block is skills_recipes only, which is in _LEAN_DROP →
    # the second (uncached) system block is empty in lean.
    assert dynamic == "", repr(dynamic)
    # Spot-check the must-drop set is encoded.
    assert {"skills_recipes", "recipe_arm", "declared_recipes",
            "highlighting", "data_orientation"} <= _LEAN_DROP


def test_lean_mode_swaps_behavior_to_slim():
    """The behavior block is the single biggest contributor to system
    size; lean forces behavior_slim.md regardless of arm. Detect via
    content fingerprint rather than file path (the block emits a
    distinguishable opening line in slim vs full)."""
    from content.bio.prompts.build import build_system, _prompt
    full_stable, _ = build_system(
        _TOOLS, role="primary", intent="x",
        ctx={"thread_id": "t"}, mode="full")
    lean_stable, _ = build_system(
        _TOOLS, role="primary", intent="x",
        ctx={"thread_id": "t"}, mode="lean")
    behavior_full = _prompt("behavior.md")
    behavior_slim = _prompt("behavior_slim.md")
    # behavior_slim should appear in lean output; behavior (full) should NOT.
    # Pick a marker from the FULL file that ISN'T in slim.
    full_only_markers = [ln for ln in behavior_full.splitlines()
                          if ln.strip() and ln not in behavior_slim]
    # Need at least one full-only line to make this assertion meaningful.
    assert full_only_markers, "behavior.md and behavior_slim.md don't differ?"
    marker = full_only_markers[0]
    assert marker in full_stable, "behavior.md content missing from full mode"
    assert marker not in lean_stable, (
        f"lean mode leaked full behavior content: {marker!r}")


def test_lean_mode_smaller_than_full():
    """Headline assertion: lean must be measurably smaller end-to-end."""
    from content.bio.prompts.build import build_system
    fs, fd = build_system(_TOOLS, role="primary", intent="x",
                          ctx={"thread_id": "t"}, mode="full")
    ls, ld = build_system(_TOOLS, role="primary", intent="x",
                          ctx={"thread_id": "t"}, mode="lean")
    full_total = len(fs) + len(fd)
    lean_total = len(ls) + len(ld)
    # Target: at least 15% reduction from the system blocks alone
    # (the tools-list reduction comes from spec.tool_allowlist, not
    # here). Validated empirically at ~24% in the dumper.
    assert lean_total < full_total * 0.85, (
        f"lean ({lean_total}) not meaningfully smaller than full ({full_total})")


def test_invalid_mode_raises():
    from content.bio.prompts.build import build_system
    with pytest.raises(ValueError, match="mode="):
        build_system(_TOOLS, role="primary", intent="x",
                     ctx={"thread_id": "t"}, mode="bogus")


def test_full_mode_default_unchanged():
    """Regression: not passing `mode` produces the same output as
    mode='full'. Callers that haven't been updated must not break."""
    from content.bio.prompts.build import build_system
    a = build_system(_TOOLS, role="primary", intent="x", ctx={"thread_id": "t"})
    b = build_system(_TOOLS, role="primary", intent="x",
                     ctx={"thread_id": "t"}, mode="full")
    assert a == b


# ── 2. AgentSpec loader: prompt_mode field ───────────────────────────
def _write_yaml(d: Path, name: str, body: str) -> Path:
    p = d / f"{name}.yaml"
    p.write_text(textwrap.dedent(body))
    return p


def test_loader_defaults_prompt_mode_to_full(tmp_path):
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
    assert spec.prompt_mode == "full"


def test_loader_reads_lean_prompt_mode(tmp_path):
    from core.runtime.agent import load_agent_spec
    p = _write_yaml(tmp_path, "y", """
        name: y
        role: primary
        model: claude-haiku-4-5-20251001
        system_prompt: identity.md
        manifest_role: y
        tool_allowlist: ['run_python']
        prompt_mode: lean
        """)
    spec = load_agent_spec(p)
    assert spec.prompt_mode == "lean"


def test_loader_rejects_bad_prompt_mode(tmp_path):
    from core.runtime.agent import load_agent_spec
    p = _write_yaml(tmp_path, "z", """
        name: z
        role: primary
        model: claude-haiku-4-5-20251001
        system_prompt: identity.md
        manifest_role: z
        tool_allowlist: ['*']
        prompt_mode: medium
        """)
    with pytest.raises(ValueError, match="prompt_mode="):
        load_agent_spec(p)


# ── 3. shipped lean_guide.yaml round-trip ────────────────────────────
def test_shipped_lean_guide_registers():
    """lean_guide.yaml is shipped under bio/advisors/. Importing
    content.bio auto-registers every spec; we just look it up."""
    import content.bio  # noqa: F401
    from core.runtime.agent import get_agent_spec, list_agent_specs
    names = list_agent_specs()
    assert "guide" in names
    assert "lean_guide" in names, names
    lean = get_agent_spec("lean_guide")
    assert lean is not None
    assert lean.role == "primary"
    assert lean.prompt_mode == "lean"
    # Post-2026-06-20 redesign: lean has FULL reach (allowlist '*'),
    # the savings come from `prompt_mode: lean` triggering compact
    # tool descriptions at the gateway. The earlier curated allowlist
    # was masking function — see lean_guide.yaml header comment.
    assert "*" in lean.tool_allowlist or lean.tool_allowlist == ("*",)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
