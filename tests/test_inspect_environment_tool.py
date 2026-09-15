"""The read-only door onto "what am I actually standing on".

WHY IT EXISTS. When an environment is not what the code expects — a package
missing, a version wrong, an install that will not take — the agent's only
move used to be to guess and retry, because nothing answered the prior
question: which interpreter is actually in play, and does it even have pip.
weft grew `env_inspect` for exactly that (record facts plus a LIVE probe of the
activated interpreter); this exposes it, read-only, so a diagnosis can be looked
up instead of inferred.

READ-ONLY IS THE PROPERTY, not a description. `env_exec` and `env_amend` exist
next to `env_inspect` in the same substrate and are deliberately NOT exposed
here: one of them runs arbitrary commands inside a realization and the other
mutates and re-identifies it. A later change that reaches for either through
this door is a different decision and must be made deliberately, so the test
below fails if this module starts calling them.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

SRC = (Path(__file__).resolve().parents[1] / "backend" / "content" / "bio"
       / "mcp_servers" / "aba_core" / "tools" / "compute_sites.py")


def _tool_body() -> str:
    src = SRC.read_text()
    m = re.search(r"^    def inspect_environment\(.*?(?=^    @mcp\.tool|\Z)",
                  src, re.S | re.M)
    assert m, "inspect_environment is not defined in compute_sites.py"
    return m.group(0)


def test_the_tool_is_registered():
    assert "def inspect_environment(" in SRC.read_text()


def test_it_calls_env_inspect_and_nothing_that_mutates():
    """ARMED. The whole justification for exposing this without a confirmation
    step is that it cannot change anything."""
    body = _tool_body()
    assert 'sync_call("env_inspect"' in body
    for forbidden in ("env_exec", "env_amend", "env_realize", "env_evict"):
        assert forbidden not in body, (
            f"inspect_environment reaches {forbidden}; this tool is exposed as "
            f"read-only and that is the only reason it needs no confirmation")


def test_a_substrate_without_env_inspect_says_which_door_is_missing():
    """WIDE — the shape every deployment takes until its image is rebuilt. An
    older substrate has no env_inspect, and the honest answer is 'this
    deployment cannot answer', not 'your environment is broken'."""
    body = _tool_body()
    assert "inspect_unavailable" in body
    assert "does not" in body and "env_inspect" in body


def test_the_no_environment_yet_case_names_what_creates_one():
    """ABSENT — the common first-call shape. A project that has not run
    anything has no session env, which is not an error to report as one."""
    body = _tool_body()
    assert body.count("created on first use") >= 2, (
        "both the unresolved and the no-env_id paths must say what makes an "
        "environment exist, or the agent retries the same call")


def test_it_reports_which_env_and_site_it_answered_about():
    """A probe whose answer cannot be attributed is not evidence."""
    body = _tool_body()
    assert '"env_id": env_id' in body and '"site": site' in body


def test_the_catalog_contract_is_unchanged_for_every_tier():
    """Per CLAUDE.md: prose is tiered, the calling contract is not. Guarded
    centrally by test_tool_presentation.py; this asserts the new tool is inside
    that guard's subject set rather than trusting that it is."""
    from core.runtime.mcp import presentation  # noqa: F401
    # The subject set is every registered tool; if registration were skipped
    # the tool would be invisible to the contract guard AND to the agent.
    assert "@mcp.tool()" in SRC.read_text().split("def inspect_environment")[0][-40:], (
        "inspect_environment is not decorated as a tool, so nothing registers "
        "it and no contract guard covers it")
