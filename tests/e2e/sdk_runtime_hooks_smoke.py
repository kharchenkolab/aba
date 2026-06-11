"""Live smoke — R-3.3.c ABA PreToolUse/PostToolUse hooks fire under SDK.

What it proves:
  - ABA's core.runtime.hooks registry (the #305 veto + post-guardrails) is
    NOT bypassed when an agent runs under AgentSDKRuntime.
  - A PreToolUse veto returned by `hooks.run_pre` reaches the model as a
    typed `blocked` result via the same path DirectAPIRuntime uses
    (executor returns the deny dict; runtime wraps it as a normal
    ToolResult).
  - A PostToolUse mutation made by `hooks.run_post` is preserved through
    the MCP handler → SDK → ToolResultBlock → ToolResult event chain.

WHY no bridging is needed: ABA's hooks fire inside `_dispatch_tool`, which
is called by guide.py's `_tool_executor`, which is the callback the SDK
runtime invokes from its MCP handler. The SDK never sees the hook layer;
it sees only the final result envelope.

Cost: ~$0.02 (two Haiku turns).
Run: .venv/bin/python tests/e2e/sdk_runtime_hooks_smoke.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_TMP = Path(tempfile.mkdtemp(prefix="aba_sdk_hooks_"))
os.environ.setdefault("ABA_LLM_CREDENTIAL", "oauth_cc")
os.environ.setdefault("ABA_DB_PATH", str(_TMP / "test.db"))
os.environ.setdefault("ARTIFACTS_DIR", str(_TMP / "artifacts"))
os.environ.setdefault("ABA_WORK_DIR", str(_TMP / "work"))
os.environ.setdefault("DATA_DIR", str(_TMP / "data"))
os.environ.setdefault("ABA_RUNTIME_DIR", str(_TMP))
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime import hooks                              # noqa: E402
from core.runtime.llm_runtime import (                      # noqa: E402
    RuntimeRequest, SystemSpec, ToolUseStart, ToolResult,
    TurnDone, TurnHalt,
)
from core.runtime.llm_runtime_sdk import AgentSDKRuntime    # noqa: E402


# Register two test-only hooks. The matchers are scoped to the tool
# names we use here so we don't accidentally affect real bio tools if
# this module is ever imported elsewhere.

@hooks.pre_tool_use("smoke_blocked_tool", label="smoke_block_hook")
def _block_smoke(name, input_, ctx):
    return hooks.Deny(
        reason_code="SMOKE_BLOCK",
        block_type="test",
        reason="Blocked by R-3.3.c smoke hook.",
        allowed=["pick a different approach"],
    )


@hooks.post_tool_use("smoke_mutate_tool", label="smoke_mutate_hook")
def _mutate_smoke(name, input_, result, ctx):
    if isinstance(result, dict):
        result["mutated_by_hook"] = True


def _build_request(tool_name: str, descr: str, prompt: str) -> RuntimeRequest:
    return RuntimeRequest(
        history=[{"role": "user", "content": prompt}],
        tools=[{
            "name": tool_name,
            "description": descr,
            "input_schema": {"type": "object",
                              "properties": {"q": {"type": "string"}},
                              "required": ["q"]},
        }],
        system=SystemSpec(
            stable="You are a test agent. When asked to use a tool, "
                   "call it once with any sensible input.",
            dynamic="",
        ),
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        ctx={},
    )


async def _tool_executor(name, args, ctx):
    """Stand-in for guide.py's _tool_executor: drive ABA's hook stack
    around a trivial body, mirroring what `_dispatch_tool` does."""
    decision, args = hooks.run_pre(name, args, ctx)
    if decision is not None:
        return hooks.deny_to_result(decision)
    result = {"echoed": args.get("q") or ""}
    hooks.run_post(name, args, result, ctx)
    return result


async def _drive(req, label):
    print(f"── {label} ────────────────────────────────────────────")
    rt = AgentSDKRuntime()
    events: list = []
    async for ev in rt.run_turn(req, _tool_executor):
        events.append(ev)
        kind = type(ev).__name__
        if isinstance(ev, ToolUseStart):
            print(f"  [ToolUseStart] name={ev.tool_name!r}")
        elif isinstance(ev, ToolResult):
            print(f"  [ToolResult]   keys={sorted((ev.result or {}).keys())!r}")
        elif isinstance(ev, TurnHalt):
            print(f"  [TurnHalt]     reason={ev.reason!r}")
        elif isinstance(ev, TurnDone):
            print(f"  [TurnDone]     stop_reason={ev.stop_reason!r}")
    print()
    return events


async def test_pre_blocks():
    req = _build_request(
        "smoke_blocked_tool",
        "A tool. Call it once with q='hi'.",
        "Call smoke_blocked_tool once with q='hi' to test the path.",
    )
    events = await _drive(req, "PreToolUse veto reaches model")
    fails: list[str] = []
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    if not tool_results:
        fails.append("no ToolResult emitted — Deny should still surface "
                     "via the executor")
    else:
        r = tool_results[0].result or {}
        if r.get("status") != "blocked":
            fails.append(f"ToolResult.status != 'blocked': {r!r}")
        if r.get("reason_code") != "SMOKE_BLOCK":
            fails.append(f"ToolResult.reason_code wrong: {r.get('reason_code')!r}")
    return fails


async def test_post_mutates():
    req = _build_request(
        "smoke_mutate_tool",
        "A tool. Call it once with q='hi'.",
        "Call smoke_mutate_tool once with q='hi'.",
    )
    events = await _drive(req, "PostToolUse mutation reaches model")
    fails: list[str] = []
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    if not tool_results:
        fails.append("no ToolResult emitted")
    else:
        r = tool_results[0].result or {}
        if not r.get("mutated_by_hook"):
            fails.append(f"PostToolUse mutation lost: {r!r}")
        if r.get("echoed") != "hi":
            fails.append(f"original payload lost: {r!r}")
    return fails


async def main():
    print("AgentSDKRuntime R-3.3.c hooks smoke — Haiku via oauth_cc")
    print()
    all_fails: list[tuple[str, str]] = []
    for label, fn in [("pre_veto", test_pre_blocks),
                      ("post_mutate", test_post_mutates)]:
        fails = await fn()
        all_fails += [(label, f) for f in fails]
    print()
    if all_fails:
        print("FAIL:")
        for label, f in all_fails:
            print(f"  [{label}] {f}")
        sys.exit(1)
    print("OK — ABA hooks fire under SDK runtime, no bridging needed")


if __name__ == "__main__":
    asyncio.run(main())
