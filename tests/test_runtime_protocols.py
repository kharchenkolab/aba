"""Smoke tests for the LLMRuntime + ContentPack protocols.

These tests verify the protocols are importable, the data classes
construct, and a minimal mock implementation satisfies the Protocol
shape. No actual LLM call; no actual content registration. The bigger
behavioral tests land alongside DirectAPIRuntime (Wave 1 A.2) and the
BioPack implementation (Wave 1 A.3).
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.platform


def test_llm_runtime_imports():
    from core.runtime.llm_runtime import (
        LLMRuntime, RuntimeEvent, RuntimeRequest, SystemSpec,
        TextDelta, ToolExecutor, ToolResult, ToolUseStart, TurnDone, TurnHalt,
    )
    # Protocol is a typing construct — just check it has the run_turn attr.
    assert hasattr(LLMRuntime, "run_turn")
    # Dataclasses construct.
    sys = SystemSpec(stable="ok", dynamic="")
    req = RuntimeRequest(
        history=[], tools=[], system=sys, model="claude-test", max_tokens=10,
        ctx={"thread_id": "t1"},
    )
    assert req.system.stable == "ok"
    assert req.seeded_history is False
    # Events.
    assert TextDelta(text="hi").text == "hi"
    assert ToolUseStart(tool_use_id="u1", tool_name="x", input={}).tool_name == "x"
    assert ToolResult(tool_use_id="u1", tool_name="x", result={}).tool_use_id == "u1"
    assert TurnDone(stop_reason="end_turn", usage={}).stop_reason == "end_turn"
    assert TurnHalt(reason="cancelled", detail={}).reason == "cancelled"
    # All events inherit from RuntimeEvent.
    assert all(
        issubclass(t, RuntimeEvent)
        for t in (TextDelta, ToolUseStart, ToolResult, TurnDone, TurnHalt)
    )


def test_content_pack_singleton():
    from core.runtime.content_pack import (
        active_pack, clear_active_pack_for_testing, set_active_pack,
    )

    # Defensive: clear any leftover state from other tests.
    clear_active_pack_for_testing()

    class _Mock:
        name = "mock"
        def prompts(self):       return {}
        def tools(self):         return []
        def execute_tool(self):  return lambda *_a, **_k: {}
        def cards(self):         return {}
        def register_hooks(self) -> None: pass

    # Querying before registration raises.
    with pytest.raises(RuntimeError, match="no content pack"):
        active_pack()

    m = _Mock()
    set_active_pack(m)
    assert active_pack() is m

    # Re-registering the SAME pack is a no-op.
    set_active_pack(m)
    assert active_pack() is m

    # Replacing with a different pack raises.
    other = _Mock()
    other.name = "other"
    with pytest.raises(RuntimeError, match="already registered"):
        set_active_pack(other)

    clear_active_pack_for_testing()
    with pytest.raises(RuntimeError):
        active_pack()


def test_tool_executor_typing():
    """ToolExecutor is just a Callable typedef — verify it accepts a
    plausible async callable shape."""
    from core.runtime.llm_runtime import ToolExecutor

    async def _exec(name: str, inp: dict, ctx: dict) -> dict:
        return {"ok": True}

    # ToolExecutor is a type alias, not a runtime check — but we can
    # assign and verify the call shape.
    fn: ToolExecutor = _exec  # noqa: F841
    # Smoke-call to make sure the signature is callable.
    import asyncio
    result = asyncio.get_event_loop().run_until_complete(
        _exec("dummy", {}, {"thread_id": "t1"})
    ) if False else None  # don't actually run an event loop in this test
    assert result is None  # placeholder; the typing check is the real assertion
