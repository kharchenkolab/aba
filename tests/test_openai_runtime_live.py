"""OpenAICompatibleRuntime — live smoke against localhost:8001.

Drives ONE real run_turn against a self-hosted Qwen3-8B vLLM and
verifies:

  - run_turn yields ToolUseStart with a valid tool name
  - the tool_executor stub receives that call with valid JSON args
  - run_turn proceeds to TurnDone with non-zero usage

Skipped when the endpoint is unreachable, so this test file safely
sits in the default pytest collection. To require liveness (e.g. in
CI) set ABA_REQUIRE_LIVE_LLM=1.

The scenario re-uses the lean fixtures dumped by
scripts/dump_phase0_fixture.py — same system prompt and tool catalog
the runtime will see in production lean turns.
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform


BASE = os.environ.get("ABA_OPENAI_BASE_URL", "http://localhost:8001/v1")
FIXTURES = ROOT.parent / "local-llm" / "phase0" / "fixtures" / "lean_guide"


def _endpoint_reachable() -> bool:
    """Probe via the openai SDK rather than urllib — vLLM sometimes
    closes raw HTTP/1.1 connections in a way urllib reports as
    ConnectionReset, even when curl + the SDK get fine responses."""
    try:
        import openai
        c = openai.OpenAI(base_url=BASE, api_key="none")
        c.models.list()
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _fixtures_present() -> bool:
    return (FIXTURES / "aba_system.txt").is_file() and \
           (FIXTURES / "aba_tools_anthropic.json").is_file()


SKIP_LIVE = pytest.mark.skipif(
    not _endpoint_reachable() and not os.environ.get("ABA_REQUIRE_LIVE_LLM"),
    reason="vLLM endpoint not reachable at " + BASE
           + " (set ABA_REQUIRE_LIVE_LLM=1 to fail instead)",
)

SKIP_NO_FIXTURES = pytest.mark.skipif(
    not _fixtures_present(),
    reason="lean fixtures missing — run scripts/dump_phase0_fixture.py",
)


@SKIP_LIVE
@SKIP_NO_FIXTURES
def test_lean_fixtures_tool_call_round_trip():
    """One real Qwen3 turn with the lean system + tools.

    Pass criteria:
      - run_turn emits ToolUseStart with a name that's in the lean
        allowlist
      - args parse as JSON
      - TurnDone fires with finish_reason in {"tool_calls","stop"}
        and usage["input"] > 0
    """
    from core.runtime.llm_runtime import RuntimeRequest, SystemSpec
    from core.runtime.llm_runtime_openai import OpenAICompatibleRuntime
    from core.runtime.llm_runtime import (TextDelta, ToolUseStart,
                                           ToolResult, TurnDone, TurnHalt)

    system_text = (FIXTURES / "aba_system.txt").read_text()
    tools_ant = json.loads(
        (FIXTURES / "aba_tools_anthropic.json").read_text())
    expected_tools = {t["name"] for t in tools_ant}

    rt  = OpenAICompatibleRuntime(base_url=BASE, api_key="none")
    req = RuntimeRequest(
        history=[{"role": "user",
                  "content": "list the data files in this project"}],
        tools=tools_ant,
        system=SystemSpec(stable=system_text, dynamic=""),
        model="qwen3-8b",
        max_tokens=2048,
        ctx={"thread_id": "thr_live_test"},
    )
    captured: dict = {}

    async def tool_exec(name, input_, ctx):
        captured["name"]   = name
        captured["input"]  = input_
        # Simple canned result that lets the model continue if it
        # wants a second turn (we won't drive it further here).
        return {"files": [], "data_dir": "/tmp"}

    async def drive():
        evs: list = []
        async for ev in rt.run_turn(req, tool_exec):
            evs.append(ev)
            # Don't run beyond one full turn — the goal is the
            # first ToolUseStart + ToolResult + TurnDone.
            if isinstance(ev, TurnDone) or isinstance(ev, TurnHalt):
                break
        return evs

    events = asyncio.new_event_loop().run_until_complete(drive())

    # We MUST see at least one ToolUseStart.
    starts = [e for e in events if isinstance(e, ToolUseStart)]
    assert starts, (
        "model emitted no tool calls — Qwen3-8B should pick "
        "list_data_files (or similar) for this prompt. Events: "
        + ", ".join(type(e).__name__ for e in events))

    s = starts[0]
    assert s.tool_name in expected_tools, (
        f"model called {s.tool_name!r} which isn't in the lean "
        f"allowlist (would have failed at the dispatcher)")
    assert isinstance(s.input, dict), "tool args must parse to a dict"

    # The tool_executor was called.
    assert captured.get("name") == s.tool_name

    # The turn finished, with usage > 0 (vLLM reports prompt_tokens
    # in the trailing chunk when stream_options.include_usage=True).
    done = [e for e in events if isinstance(e, TurnDone)]
    assert done, "no TurnDone — turn didn't complete cleanly"
    d = done[0]
    assert d.stop_reason in ("tool_calls", "stop")
    assert d.usage["input"] > 0, (
        "no prompt_tokens reported — vLLM must support "
        "stream_options.include_usage")


@SKIP_LIVE
def test_runtime_handles_unreachable_endpoint_cleanly(monkeypatch):
    """When the endpoint is up but our config points at the wrong
    port, the runtime yields TurnHalt(reason='error') instead of
    raising. Defends against a bad ABA_OPENAI_BASE_URL silently
    killing the live chat loop.
    """
    from core.runtime.llm_runtime import RuntimeRequest, SystemSpec
    from core.runtime.llm_runtime_openai import OpenAICompatibleRuntime
    from core.runtime.llm_runtime import TurnHalt

    # Point at a port nothing's listening on.
    rt  = OpenAICompatibleRuntime(base_url="http://localhost:1/v1",
                                   api_key="none")
    req = RuntimeRequest(
        history=[{"role": "user", "content": "hi"}],
        tools=[], system=SystemSpec(stable="t", dynamic=""),
        model="qwen3-8b", max_tokens=16, ctx={},
    )

    async def noop_exec(name, i, c):
        return {}

    async def drive():
        evs: list = []
        async for ev in rt.run_turn(req, noop_exec):
            evs.append(ev)
        return evs

    events = asyncio.new_event_loop().run_until_complete(drive())
    halts = [e for e in events if isinstance(e, TurnHalt)]
    assert halts, "expected TurnHalt(error) when endpoint is down"
    assert halts[0].reason == "error"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
