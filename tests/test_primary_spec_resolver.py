"""ABA_PRIMARY_SPEC env override + registry coexistence.

Two lean-LLM features land here:

  1. resolve_primary_spec_name() returns "guide" by default but flips
     to whatever ABA_PRIMARY_SPEC names. Resolved per-call so a config
     edit takes effect on the next turn without a restart.

  2. Multiple primary specs can register and coexist in _SPECS. Adding
     a `lean_guide.yaml` peer to advisors/ doesn't replace "guide";
     both are available, selection is by name.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_primary_spec_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "ps.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.agent import (                                           # noqa: E402
    AgentSpec, _SPECS, register_agent_spec, resolve_primary_spec_name,
    get_agent_spec,
)


pytestmark = pytest.mark.platform


def _mk(name: str) -> AgentSpec:
    return AgentSpec(
        name=name, role="primary", model="claude-haiku-4-5-20251001",
        system_prompt="x", manifest_role=name, runtime="direct",
    )


def test_default_resolves_to_guide(monkeypatch):
    monkeypatch.delenv("ABA_PRIMARY_SPEC", raising=False)
    assert resolve_primary_spec_name() == "guide"


def test_env_override_picks_lean(monkeypatch):
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "lean_guide")
    assert resolve_primary_spec_name() == "lean_guide"


def test_env_empty_string_falls_back_to_default(monkeypatch):
    # An empty value (export ABA_PRIMARY_SPEC=) should NOT name a bogus
    # spec — it should behave like "unset" so a typo-then-clear doesn't
    # leave the install pointing at an empty name.
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "")
    assert resolve_primary_spec_name() == "guide"


def test_env_whitespace_trimmed(monkeypatch):
    monkeypatch.setenv("ABA_PRIMARY_SPEC", "  lean_guide  ")
    assert resolve_primary_spec_name() == "lean_guide"


def test_multiple_primary_specs_coexist():
    """The registry is a flat name→spec dict. Adding a second primary
    does not displace the first. The Guide loop calls get_agent_spec
    with the resolved name; both must be reachable.

    Snapshot/restore the real registry around this test so we don't
    poison subsequent tests in the same pytest run (which would
    otherwise see our stub specs with empty tool_allowlists)."""
    snap = dict(_SPECS)
    try:
        g = _mk("test_guide_iso")
        l = _mk("test_lean_iso")
        register_agent_spec(g)
        register_agent_spec(l)
        assert get_agent_spec("test_guide_iso") is g
        assert get_agent_spec("test_lean_iso") is l
    finally:
        _SPECS.clear()
        _SPECS.update(snap)


def test_unknown_spec_returns_none():
    # Caller (guide.py) is responsible for warning + fallback; the
    # registry itself just returns None.
    assert get_agent_spec("does_not_exist_xyz") is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
