"""W1-A.2 phase 1: DirectAPIRuntime skeleton.

This test only proves the scaffold imports + conforms to LLMRuntime.
Behavioral tests land alongside phase 2 (when the inner streaming
retry loop moves in), phase 3 (final-msg consumption), and phase 4
(tool dispatch + halt detection).

Pytest markers: `platform` — pure protocol/scaffold, no bio.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_directrt_")
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "rt.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.llm_runtime import (
    LLMRuntime, RuntimeRequest, SystemSpec,
    TextDelta, ToolUseStart, ToolResult, TurnDone, TurnHalt,
)
from core.runtime.llm_runtime_direct import DirectAPIRuntime, _conforms_to_protocol


pytestmark = pytest.mark.platform


def test_imports_clean():
    """All five event types + the request dataclasses + the runtime
    class import without error. Sanity check that phase 1's module
    doesn't accidentally drag in bio."""
    # The imports above are the test. Reaching this line is success.
    assert RuntimeRequest is not None
    assert SystemSpec is not None
    for cls in (TextDelta, ToolUseStart, ToolResult, TurnDone, TurnHalt):
        assert cls is not None


def test_protocol_conformance():
    """DirectAPIRuntime has the run_turn method LLMRuntime defines."""
    assert _conforms_to_protocol(), "DirectAPIRuntime missing required Protocol method"
    # Direct method-presence check too, just to be explicit:
    assert hasattr(DirectAPIRuntime, "run_turn")
    assert callable(DirectAPIRuntime.run_turn)


def test_run_turn_is_async_generator():
    """W1-A.2 phase 4.1: run_turn is a real async generator now, not the
    NotImplementedError skeleton. Behavioral testing is done via
    tests/e2e/quick_smoke.py (live agent loop through the runtime); this
    test just sanity-checks the entry point's shape so a structural
    regression — e.g. accidentally turning it back into a normal coro —
    fails fast in pytest before the next live smoke."""
    import inspect
    assert inspect.isasyncgenfunction(DirectAPIRuntime.run_turn), \
        "DirectAPIRuntime.run_turn must be an async generator"


def test_module_has_no_bio_imports():
    """The new runtime module is platform-tier — must not import bio."""
    import ast
    src = (ROOT / "backend" / "core" / "runtime" / "llm_runtime_direct.py").read_text()
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
        "llm_runtime_direct.py must not import from content.* (platform purity):\n"
        + "\n".join("  " + v for v in violations)
    )
