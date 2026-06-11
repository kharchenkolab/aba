"""R-2.2 — AgentSpec.runtime + make_runtime() selector.

Verifies:
  - AgentSpec accepts a `runtime` field; default is 'direct'
  - load_agent_spec() reads `runtime: <value>` from YAML; rejects unknown
  - make_runtime() returns the right class per the spec
  - Env-var precedence: ABA_FAKE_SESSION > ABA_RUNTIME_OVERRIDE > spec
"""
from __future__ import annotations
import os
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_rtsel_")
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "rt.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.agent import AgentSpec, load_agent_spec, make_runtime   # noqa: E402
from core.runtime.llm_runtime_direct import DirectAPIRuntime   # noqa: E402
from core.runtime.llm_runtime_fake import FakeRuntime   # noqa: E402


pytestmark = pytest.mark.platform


def _spec(runtime: str = "direct") -> AgentSpec:
    return AgentSpec(
        name="t", role="primary", model="claude-haiku-4-5-20251001",
        system_prompt="x", manifest_role="t", runtime=runtime,
    )


def test_default_runtime_is_direct():
    s = AgentSpec(name="t", role="primary", model="m",
                  system_prompt="", manifest_role="t")
    assert s.runtime == "direct"


def test_make_runtime_direct():
    # Clear any env override.
    os.environ.pop("ABA_FAKE_SESSION", None)
    os.environ.pop("ABA_RUNTIME_OVERRIDE", None)
    rt = make_runtime(_spec("direct"))
    assert isinstance(rt, DirectAPIRuntime)


def test_make_runtime_fake():
    os.environ.pop("ABA_FAKE_SESSION", None)
    os.environ.pop("ABA_RUNTIME_OVERRIDE", None)
    rt = make_runtime(_spec("fake"))
    assert isinstance(rt, FakeRuntime)


def test_fake_session_env_overrides_spec():
    """ABA_FAKE_SESSION set → FakeRuntime regardless of spec.runtime.
    Mirrors the legacy core.llm.make_open_stream() override."""
    os.environ["ABA_FAKE_SESSION"] = "tests/fixtures/dummy.jsonl"
    os.environ.pop("ABA_RUNTIME_OVERRIDE", None)
    try:
        rt = make_runtime(_spec("direct"))   # spec says direct, env wins
        assert isinstance(rt, FakeRuntime)
    finally:
        del os.environ["ABA_FAKE_SESSION"]


def test_runtime_override_env():
    """ABA_RUNTIME_OVERRIDE=fake forces fake even when spec says direct."""
    os.environ.pop("ABA_FAKE_SESSION", None)
    os.environ["ABA_RUNTIME_OVERRIDE"] = "fake"
    try:
        rt = make_runtime(_spec("direct"))
        assert isinstance(rt, FakeRuntime)
    finally:
        del os.environ["ABA_RUNTIME_OVERRIDE"]


def test_fake_session_beats_override():
    """When both env vars are set, ABA_FAKE_SESSION wins (it's the legacy
    behavior we explicitly preserve)."""
    os.environ["ABA_FAKE_SESSION"] = "tests/fixtures/dummy.jsonl"
    os.environ["ABA_RUNTIME_OVERRIDE"] = "direct"
    try:
        rt = make_runtime(_spec("direct"))
        assert isinstance(rt, FakeRuntime)
    finally:
        del os.environ["ABA_FAKE_SESSION"]
        del os.environ["ABA_RUNTIME_OVERRIDE"]


def test_load_agent_spec_accepts_runtime_field(tmp_path: Path):
    """YAML can declare runtime: direct|sdk|fake."""
    yml = tmp_path / "agent.yaml"
    yml.write_text(textwrap.dedent("""\
        name: test_agent
        role: advisor
        model: claude-haiku-4-5-20251001
        system_prompt: hi
        manifest_role: test
        runtime: fake
    """))
    spec = load_agent_spec(yml)
    assert spec.runtime == "fake"


def test_load_agent_spec_default_runtime(tmp_path: Path):
    """No `runtime:` in YAML → defaults to 'direct'."""
    yml = tmp_path / "agent.yaml"
    yml.write_text(textwrap.dedent("""\
        name: test_agent
        role: advisor
        model: claude-haiku-4-5-20251001
        system_prompt: hi
        manifest_role: test
    """))
    spec = load_agent_spec(yml)
    assert spec.runtime == "direct"


def test_load_agent_spec_rejects_unknown(tmp_path: Path):
    """An unknown runtime value should raise — better than silently
    falling back to a default the user didn't intend."""
    yml = tmp_path / "agent.yaml"
    yml.write_text(textwrap.dedent("""\
        name: test_agent
        role: advisor
        model: claude-haiku-4-5-20251001
        system_prompt: hi
        manifest_role: test
        runtime: claude-something-typoed
    """))
    with pytest.raises(ValueError, match="runtime="):
        load_agent_spec(yml)
