"""Live smoke — R-3.3.a halt_on_tools intercept under AgentSDKRuntime.

What it verifies:
  - Pass halt_on_tools={"present_plan"} to run_turn
  - The model issues mcp__aba_runtime__present_plan
  - can_use_tool fires → returns PermissionResultDeny(interrupt=True)
  - SDK aborts; the held tool's name + input get translated to TurnHalt
  - tool_executor for present_plan is NEVER invoked
  - The ToolUseStart event is still emitted (model's intent is visible)

Cost: ~$0.01 (one Haiku turn with one halt). Same pattern as R-3.2's
tests/e2e/sdk_runtime_smoke.py.

Run: .venv/bin/python tests/e2e/sdk_runtime_halt_smoke.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_TMP = Path(tempfile.mkdtemp(prefix="aba_sdk_halt_"))
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
os.environ.setdefault("ABA_DB_PATH", str(_TMP / "test.db"))
os.environ.setdefault("ARTIFACTS_DIR", str(_TMP / "artifacts"))
os.environ.setdefault("ABA_WORK_DIR", str(_TMP / "work"))
os.environ.setdefault("DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("ABA_RUNTIME_DIR", str(_TMP))
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.llm_runtime import (   # noqa: E402
    RuntimeRequest, SystemSpec,
    TextDelta, ToolUseStart, ToolResult, TurnDone, TurnHalt,
)
from core.runtime.llm_runtime_sdk import AgentSDKRuntime   # noqa: E402


_DISPATCH_HITS: list[dict] = []


async def _stub_tool_executor(name: str, args: dict, ctx: dict) -> dict:
    _DISPATCH_HITS.append({"name": name, "args": args})
    if name == "present_plan":
        return {"status": "presented", "_runtime_halt_after": "plan"}
    return {"error": f"unexpected dispatch of {name!r}"}


async def smoke() -> None:
    print("AgentSDKRuntime R-3.3.a halt_on_tools smoke — Haiku via oauth_cc")
    print()

    req = RuntimeRequest(
        history=[{
            "role": "user",
            "content": ("Use the present_plan tool to outline a 3-step plan "
                        "for cleaning a dataset. Use the tool — do NOT just "
                        "describe it in text."),
        }],
        tools=[{
            "name": "present_plan",
            "description": "Present a structured plan with title + steps.",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "steps": {"type": "array",
                              "items": {"type": "string"}},
                },
                "required": ["title", "steps"],
            },
        }],
        system=SystemSpec(
            stable=("You are a planning test agent. When asked for a plan, "
                    "you MUST call the `present_plan` tool. Don't write the "
                    "plan as plain text."),
            dynamic="",
        ),
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        ctx={},
    )

    rt = AgentSDKRuntime()
    events: list = []
    async for ev in rt.run_turn(req, _stub_tool_executor,
                                 halt_on_tools=frozenset({"present_plan"})):
        events.append(ev)
        kind = type(ev).__name__
        if isinstance(ev, TextDelta):
            print(f"  [TextDelta]    {ev.text[:80]!r}")
        elif isinstance(ev, ToolUseStart):
            print(f"  [ToolUseStart] name={ev.tool_name!r}  "
                  f"input_keys={list(ev.input)!r}")
        elif isinstance(ev, ToolResult):
            print(f"  [ToolResult]   should NOT fire when halt_on_tools "
                  f"intercepts: {ev!r}")
        elif isinstance(ev, TurnHalt):
            print(f"  [TurnHalt]     reason={ev.reason!r}  "
                  f"tool_name={ev.detail.get('tool_name')!r}")
        elif isinstance(ev, TurnDone):
            print(f"  [TurnDone]     stop_reason={ev.stop_reason!r}  "
                  f"⚠ unexpected — halt should preempt")
        else:
            print(f"  [{kind}]")
    print()

    tool_uses = [e for e in events if isinstance(e, ToolUseStart)]
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    halts = [e for e in events if isinstance(e, TurnHalt)]
    turn_done = [e for e in events if isinstance(e, TurnDone)]

    fails: list[str] = []
    if not tool_uses or tool_uses[0].tool_name != "present_plan":
        fails.append("model did not call present_plan as instructed")
    if _DISPATCH_HITS:
        fails.append(f"tool_executor was invoked despite halt — "
                     f"intercept broken. Hits: {_DISPATCH_HITS!r}")
    if not halts:
        fails.append("no TurnHalt event — halt_on_tools didn't translate")
    if halts and halts[0].reason != "pending_tool":
        fails.append(f"wrong halt reason: {halts[0].reason!r}")
    if halts and halts[0].detail.get("tool_name") != "present_plan":
        fails.append(f"halt detail lost tool_name: {halts[0].detail!r}")
    if tool_results:
        fails.append("ToolResult fired but should not — halt happened "
                     "before dispatch")
    if turn_done:
        fails.append("TurnDone fired alongside TurnHalt — should be one OR "
                     "the other, not both")

    if fails:
        print("FAIL:")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK — halt_on_tools intercept works "
          f"(executor hits={len(_DISPATCH_HITS)}, halts={len(halts)})")


if __name__ == "__main__":
    asyncio.run(smoke())
