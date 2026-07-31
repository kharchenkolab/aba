"""Behavioral guards for AgentSpec.tool_allowlist (core/runtime/agent.py).

Two invariants:
1. Parser semantics — including the bare-exclusion form: an allowlist that
   only excludes, ("!x",), means "everything except x", never "no tools"
   (the parser used to return [] there: a silently tool-less agent).
2. Full-surface parity — the standing decision that every interactive guide
   tier (standard and lean alike) surfaces the ENTIRE tool catalog: tiers
   compress prose (presentation policy), they never shed callable tools.
   Deployment-specific capabilities refuse honestly at call time instead
   (availability is a deployment fact, not a tier fact).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.agent import (  # noqa: E402
    filter_tools_by_allowlist, get_agent_spec)

_TOOLS = [{"name": n} for n in ("alpha", "beta", "gamma")]


def _names(tools):
    return [t["name"] for t in tools]


# ── 1. parser semantics ──────────────────────────────────────────────────────

def test_empty_allowlist_means_no_tools():
    assert filter_tools_by_allowlist(_TOOLS, ()) == []


def test_star_passes_everything():
    assert _names(filter_tools_by_allowlist(_TOOLS, ("*",))) == \
        ["alpha", "beta", "gamma"]


def test_enumerated_keeps_only_named():
    assert _names(filter_tools_by_allowlist(_TOOLS, ("beta",))) == ["beta"]


def test_star_with_exclusion():
    assert _names(filter_tools_by_allowlist(_TOOLS, ("*", "!beta"))) == \
        ["alpha", "gamma"]


def test_bare_exclusion_implies_star():
    """("!x",) is everything-except-x — NOT zero tools."""
    assert _names(filter_tools_by_allowlist(_TOOLS, ("!beta",))) == \
        ["alpha", "gamma"]


def test_exclusion_wins_over_enumeration():
    assert _names(filter_tools_by_allowlist(
        _TOOLS, ("alpha", "beta", "!beta"))) == ["alpha"]


# ── 2. full-surface parity across guide tiers ────────────────────────────────

_GUIDE_SPECS = ("grounded_guide", "guide", "lean_guide", "lean_qwen_guide")


def _catalog(mode):
    from core.runtime.mcp.gateway import register_inprocess_server, list_tools
    from content.bio.mcp_servers.aba_core import make_server
    try:
        register_inprocess_server("aba_core", make_server,
                                  expose_in_catalog=True,
                                  strip_prefix_in_catalog=True)
    except Exception:  # noqa: BLE001 — already registered in this process
        pass
    return list_tools(mode=mode)


def test_every_guide_tier_surfaces_full_catalog():
    """No tier sheds callable tools — a per-tier '!tool' exclusion quietly
    landing in a spec again fails here, naming the shed tools."""
    for spec_name in _GUIDE_SPECS:
        spec = get_agent_spec(spec_name)
        assert spec is not None, f"{spec_name!r} not registered"
        tools = _catalog(spec.prompt_mode)
        full = {t["name"] for t in tools}
        assert full, "catalog came back empty — a setup error, not a verdict"
        surfaced = {t["name"] for t in
                    filter_tools_by_allowlist(tools, spec.tool_allowlist)}
        missing = full - surfaced
        assert not missing, (f"{spec_name}: {len(missing)} tool(s) shed from "
                             f"its tier: {sorted(missing)}")
