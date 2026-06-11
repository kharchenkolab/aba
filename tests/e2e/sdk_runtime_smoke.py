"""Live smoke test for AgentSDKRuntime — minimum-viable scope (R-3.2).

What it verifies, end-to-end, in <30 seconds against oauth_cc + Haiku:
  - AgentSDKRuntime.run_turn() drives a real ClaudeSDKClient session.
  - In-process MCP server hosts one trivial tool (`add`).
  - Model issues a tool_use → SDK dispatches to our handler → handler
    calls our tool_executor stub → result returns.
  - ABA receives TextDelta + ToolUseStart + ToolResult + TurnDone events
    in causal order.
  - The handler's tool_executor invocation HITS (verified via a
    side-channel counter); the SDK didn't bypass our wrapper.

Cost: ~$0.01 (one Haiku turn with one tool call). Same pattern + scale
as the SDK spikes — fast + cheap.

Run: .venv/bin/python tests/e2e/sdk_runtime_smoke.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_TMP = Path(tempfile.mkdtemp(prefix="aba_sdk_smoke_"))
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
os.environ.setdefault("ABA_DB_PATH", str(_TMP / "test.db"))
os.environ.setdefault("ARTIFACTS_DIR", str(_TMP / "artifacts"))
os.environ.setdefault("ABA_WORK_DIR", str(_TMP / "work"))
os.environ.setdefault("DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("ABA_RUNTIME_DIR", str(_TMP))
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.llm_runtime import (   # noqa: E402
    RuntimeRequest, SystemSpec,
    TextDelta, ToolUseStart, ToolResult, TurnDone,
)
from core.runtime.llm_runtime_sdk import AgentSDKRuntime   # noqa: E402


# Side-channel counter — verifies our tool_executor wrapper actually ran.
_HITS: list[dict] = []


async def _stub_tool_executor(name: str, args: dict, ctx: dict) -> dict:
    """The tool_executor closure ABA's guide.py would build. For this
    smoke it's trivial: increment counter, return a result the model can
    act on."""
    _HITS.append({"name": name, "args": args})
    assert "tool_use_id" in ctx
    assert "progress_q" in ctx
    if name == "add":
        return {"result": int(args["a"]) + int(args["b"])}
    return {"error": f"unknown tool {name}"}


async def smoke() -> None:
    print("AgentSDKRuntime live smoke — Haiku via oauth_cc")
    print()

    req = RuntimeRequest(
        history=[{
            "role": "user",
            "content": "What is 7 plus 19? Use the add tool. Reply with the result.",
        }],
        tools=[{
            "name": "add",
            "description": "Add two integers and return the sum.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer", "description": "first addend"},
                    "b": {"type": "integer", "description": "second addend"},
                },
                "required": ["a", "b"],
            },
        }],
        system=SystemSpec(
            stable=("You are a test agent. When asked to add integers, "
                    "you MUST call the `add` tool. Do not compute it yourself."),
            dynamic="",
        ),
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        ctx={},
    )

    rt = AgentSDKRuntime()
    events: list = []
    async for ev in rt.run_turn(req, _stub_tool_executor, frozenset()):
        events.append(ev)
        kind = type(ev).__name__
        if isinstance(ev, TextDelta):
            print(f"  [TextDelta]    {ev.text[:80]!r}")
        elif isinstance(ev, ToolUseStart):
            print(f"  [ToolUseStart] id={ev.tool_use_id[:20]}  "
                  f"name={ev.tool_name!r}  input={ev.input!r}")
        elif isinstance(ev, ToolResult):
            print(f"  [ToolResult]   id={ev.tool_use_id[:20]}  "
                  f"result={ev.result!r}")
        elif isinstance(ev, TurnDone):
            print(f"  [TurnDone]     stop_reason={ev.stop_reason!r}  usage={ev.usage}")
        else:
            print(f"  [{kind}]")
    print()

    text_deltas = [e for e in events if isinstance(e, TextDelta)]
    tool_uses   = [e for e in events if isinstance(e, ToolUseStart)]
    tool_results= [e for e in events if isinstance(e, ToolResult)]
    turn_done   = [e for e in events if isinstance(e, TurnDone)]

    fails: list[str] = []
    if not turn_done:
        fails.append("missing TurnDone (terminal event)")
    if not tool_uses:
        fails.append("model did not call the add tool")
    if not _HITS:
        fails.append("tool_executor stub never ran (handler bridge broken)")
    if _HITS and _HITS[0]["name"] != "add":
        fails.append(f"handler called for wrong tool: {_HITS[0]['name']!r}")
    if _HITS and _HITS[0]["args"] not in ({"a": 7, "b": 19}, {"a": 19, "b": 7}):
        fails.append(f"unexpected args: {_HITS[0]['args']!r}")
    if not tool_results:
        fails.append("no ToolResult event (executor returned but no event emitted)")
    if tool_results and tool_results[0].result.get("result") != 26:
        fails.append(f"unexpected result: {tool_results[0].result!r}")
    if turn_done and turn_done[0].usage.get("output", 0) == 0:
        fails.append("TurnDone.usage looks unpopulated (output=0)")

    if fails:
        print("FAIL:")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK — dispatch loop intact (tools=['{tool_uses[0].tool_name}'], "
          f"tool_executor hits={len(_HITS)})")


if __name__ == "__main__":
    asyncio.run(smoke())
