"""R-3.1 — AgentSDKRuntime skeleton + protocol-purity test.

Mirror of test_direct_api_runtime_skeleton.py for the SDK runtime.
Behavioral testing lives in tests/e2e/sdk_runtime_smoke.py (live API,
deferred until R-3.2).

Pytest markers: `platform` — pure runtime scaffold, no bio dependency.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_sdkrt_")
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "rt.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.llm_runtime import (   # noqa: E402
    RuntimeRequest, SystemSpec,
)
from core.runtime.llm_runtime_sdk import AgentSDKRuntime, _conforms_to_protocol   # noqa: E402


pytestmark = pytest.mark.platform


def test_imports_clean():
    """Module imports cleanly. claude_agent_sdk is lazy-imported inside
    run_turn so the cheap code paths don't pay the 75 MB import cost."""
    assert AgentSDKRuntime is not None


def test_protocol_conformance():
    """AgentSDKRuntime has run_turn as required by LLMRuntime."""
    assert _conforms_to_protocol()
    assert hasattr(AgentSDKRuntime, "run_turn")
    assert callable(AgentSDKRuntime.run_turn)


def test_run_turn_is_async_generator():
    """Structural check — run_turn must be an async generator function
    (same shape as DirectAPIRuntime + FakeRuntime). Catches the regression
    of accidentally turning it into a normal coro."""
    import inspect
    assert inspect.isasyncgenfunction(AgentSDKRuntime.run_turn)


def test_halt_on_tools_raises_until_r33():
    """halt_on_tools support is R-3.3. Passing a non-empty set today
    should raise loudly so callers know not to expect the present_plan /
    ask_clarification halt semantics through the SDK yet."""
    import asyncio
    rt = AgentSDKRuntime()
    req = RuntimeRequest(
        history=[], tools=[], system=SystemSpec(stable="", dynamic=""),
        model="claude-haiku-4-5", max_tokens=128, ctx={},
    )

    async def _consume():
        async for _ev in rt.run_turn(
                req, lambda *_a: None,
                halt_on_tools=frozenset({"present_plan"})):
            pass

    with pytest.raises(NotImplementedError, match="R-3.3"):
        asyncio.run(_consume())


def test_make_runtime_returns_sdk_instance():
    """make_runtime(spec) with spec.runtime='sdk' returns AgentSDKRuntime."""
    from core.runtime.agent import AgentSpec, make_runtime
    os.environ.pop("ABA_FAKE_SESSION", None)
    os.environ.pop("ABA_RUNTIME_OVERRIDE", None)
    spec = AgentSpec(name="t", role="advisor", model="m",
                     system_prompt="", manifest_role="t", runtime="sdk")
    rt = make_runtime(spec)
    assert isinstance(rt, AgentSDKRuntime)


def test_module_has_no_bio_imports():
    """Platform purity — llm_runtime_sdk.py must not import from content.*."""
    import ast
    src = (ROOT / "backend" / "core" / "runtime" / "llm_runtime_sdk.py").read_text()
    tree = ast.parse(src)
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("content."):
            violations.append(f"line {node.lineno}: from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("content."):
                    violations.append(f"line {node.lineno}: import {alias.name}")
    assert not violations, (
        "llm_runtime_sdk.py must not import from content.*:\n"
        + "\n".join("  " + v for v in violations)
    )
