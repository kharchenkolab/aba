"""R-2.1 — FakeRuntime: protocol conformance + scripted-turn behavior.

Exercises the LLMRuntime contract without any network: every test feeds
in-memory scripted turns via FakeRuntime._Cursor.reset_for_testing(),
calls run_turn() with a stub tool_executor, and asserts the event
sequence.

Pytest markers: `platform` — pure runtime, no bio dependency.
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_fakert_")
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "rt.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.llm_runtime import (   # noqa: E402
    RuntimeRequest, SystemSpec,
    TextDelta, ToolUseStart, ToolResult, TurnDone, TurnHalt,
)
from core.runtime.llm_runtime_fake import FakeRuntime, _Cursor   # noqa: E402


pytestmark = pytest.mark.platform


def _req() -> RuntimeRequest:
    return RuntimeRequest(
        history=[], tools=[], system=SystemSpec(stable="", dynamic=""),
        model="fake", max_tokens=128, ctx={},
    )


async def _collect(runtime, executor=None, halt_on_tools=frozenset()):
    """Drive run_turn to completion, collect events."""
    if executor is None:
        async def executor(name, inp, ctx):
            return {"ok": True, "echoed": inp}
    events = []
    async for ev in runtime.run_turn(_req(), executor, halt_on_tools):
        events.append(ev)
    return events


def test_protocol_conformance():
    """FakeRuntime must define run_turn as an async generator."""
    import inspect
    assert inspect.isasyncgenfunction(FakeRuntime.run_turn)


def test_text_only_turn():
    """A turn with one text block → N TextDeltas + TurnDone(end_turn)."""
    _Cursor.reset_for_testing([{"blocks": [
        {"type": "text", "text": "Hello world! This is a longer text to test chunking."}
    ]}])
    evs = asyncio.run(_collect(FakeRuntime()))

    deltas = [e for e in evs if isinstance(e, TextDelta)]
    assert deltas, "expected at least one TextDelta"
    assert "".join(d.text for d in deltas) == \
           "Hello world! This is a longer text to test chunking."
    assert isinstance(evs[-1], TurnDone)
    assert evs[-1].stop_reason == "end_turn"


def test_tool_use_dispatches_through_executor():
    """A turn with a tool_use → ToolUseStart, tool_executor invoked,
    ToolResult yielded, TurnDone(tool_use)."""
    _Cursor.reset_for_testing([{"blocks": [
        {"type": "tool_use", "id": "toolu_1", "name": "echo",
         "input": {"x": 42}},
    ]}])

    seen_calls = []
    async def executor(name, inp, ctx):
        seen_calls.append((name, inp))
        assert "progress_q" in ctx
        assert ctx["tool_use_id"] == "toolu_1"
        return {"result": inp["x"] * 2}

    evs = asyncio.run(_collect(FakeRuntime(), executor=executor))
    assert seen_calls == [("echo", {"x": 42})]

    tus = [e for e in evs if isinstance(e, ToolUseStart)]
    assert len(tus) == 1 and tus[0].tool_name == "echo"

    trs = [e for e in evs if isinstance(e, ToolResult)]
    assert len(trs) == 1 and trs[0].result == {"result": 84}

    assert isinstance(evs[-1], TurnDone)
    assert evs[-1].stop_reason == "tool_use"


def test_halt_on_tools_blocks_dispatch():
    """A tool in halt_on_tools → TurnHalt('pending_tool'), executor NOT
    called, no ToolResult emitted."""
    _Cursor.reset_for_testing([{"blocks": [
        {"type": "tool_use", "id": "toolu_p", "name": "present_plan",
         "input": {"title": "Plan"}},
    ]}])

    called = []
    async def executor(name, inp, ctx):
        called.append(name)
        return {}

    evs = asyncio.run(_collect(FakeRuntime(), executor=executor,
                                halt_on_tools=frozenset({"present_plan"})))
    assert called == [], "executor should NOT fire for halt-on-tools"
    halts = [e for e in evs if isinstance(e, TurnHalt)]
    assert len(halts) == 1
    assert halts[0].reason == "pending_tool"
    assert halts[0].detail["tool_name"] == "present_plan"


def test_runtime_halt_before_envelope():
    """{_runtime_halt_before: 'approval'} from the executor → TurnHalt
    before any ToolResult is emitted (matches DirectAPIRuntime)."""
    _Cursor.reset_for_testing([{"blocks": [
        {"type": "tool_use", "id": "toolu_a", "name": "rm_rf",
         "input": {"path": "/"}},
    ]}])

    async def executor(name, inp, ctx):
        return {"_runtime_halt_before": "approval",
                "tool_name": name}

    evs = asyncio.run(_collect(FakeRuntime(), executor=executor))
    trs = [e for e in evs if isinstance(e, ToolResult)]
    halts = [e for e in evs if isinstance(e, TurnHalt)]
    assert trs == [], "no ToolResult before approval halt"
    assert len(halts) == 1 and halts[0].reason == "approval"


def test_runtime_halt_after_envelope():
    """{_runtime_halt_after: 'plan'} → ToolResult yielded first, then
    TurnHalt('plan'). Same shape as present_plan in production."""
    _Cursor.reset_for_testing([{"blocks": [
        {"type": "tool_use", "id": "toolu_p", "name": "present_plan",
         "input": {"title": "Plan"}},
    ]}])

    async def executor(name, inp, ctx):
        return {"status": "presented",
                "_runtime_halt_after": "plan"}

    evs = asyncio.run(_collect(FakeRuntime(), executor=executor))
    types = [type(e).__name__ for e in evs]
    assert "ToolResult" in types and "TurnHalt" in types
    halt = [e for e in evs if isinstance(e, TurnHalt)][0]
    assert halt.reason == "plan"


def test_deferred_envelope_yields_turn_halt():
    """{deferred: True, deferred_id} → TurnHalt('deferred'), no ToolResult."""
    _Cursor.reset_for_testing([{"blocks": [
        {"type": "tool_use", "id": "toolu_d", "name": "long_job",
         "input": {}},
    ]}])

    async def executor(name, inp, ctx):
        return {"deferred": True, "deferred_id": "job_abc", "timeout_s": 60}

    evs = asyncio.run(_collect(FakeRuntime(), executor=executor))
    trs = [e for e in evs if isinstance(e, ToolResult)]
    halts = [e for e in evs if isinstance(e, TurnHalt)]
    assert trs == []
    assert len(halts) == 1
    assert halts[0].reason == "deferred"
    assert halts[0].detail["deferred_id"] == "job_abc"


def test_raise_turn_simulates_api_failure():
    """A `{raise: msg}` turn raises RuntimeError — exercises retry paths
    in the outer orchestrator."""
    _Cursor.reset_for_testing([{"raise": "simulated 529"}])

    async def executor(*_):
        return {}

    with pytest.raises(RuntimeError, match="simulated 529"):
        asyncio.run(_collect(FakeRuntime(), executor=executor))


def test_cursor_advances_across_calls():
    """Successive run_turn() calls consume successive turns. Matches
    today's _fake_factory cursor semantics."""
    _Cursor.reset_for_testing([
        {"blocks": [{"type": "text", "text": "first"}]},
        {"blocks": [{"type": "text", "text": "second"}]},
    ])

    rt = FakeRuntime()
    evs1 = asyncio.run(_collect(rt))
    evs2 = asyncio.run(_collect(rt))
    text1 = "".join(e.text for e in evs1 if isinstance(e, TextDelta))
    text2 = "".join(e.text for e in evs2 if isinstance(e, TextDelta))
    assert text1 == "first"
    assert text2 == "second"


def test_exhausted_session_emits_terminator():
    """Past the end of the JSONL → polite terminator turn, not an error."""
    _Cursor.reset_for_testing([])

    async def executor(*_):
        return {}

    evs = asyncio.run(_collect(FakeRuntime(), executor=executor))
    text = "".join(e.text for e in evs if isinstance(e, TextDelta))
    assert "exhausted" in text.lower()
    assert isinstance(evs[-1], TurnDone)


def test_module_has_no_bio_imports():
    """Platform purity guard — FakeRuntime must not import from content.*."""
    import ast
    src = (ROOT / "backend" / "core" / "runtime" / "llm_runtime_fake.py").read_text()
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
        "llm_runtime_fake.py must not import from content.*:\n"
        + "\n".join("  " + v for v in violations)
    )
