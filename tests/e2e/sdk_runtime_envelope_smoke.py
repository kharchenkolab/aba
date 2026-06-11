"""Live smoke — R-3.3.b envelope halts under AgentSDKRuntime.

Covers all three executor-side halt envelopes:

  1. {_runtime_halt_after: "<reason>", ...}  — model calls present_plan;
     executor returns the plan_ack + halt-after marker; runtime emits
     ToolResult (sans marker) + TurnHalt(reason) + NO TurnDone.

  2. {_runtime_halt_before: "<reason>", ...} — model calls a heavy tool;
     executor returns ONLY the halt-before marker (no real work yet);
     runtime SUPPRESSES ToolResult + emits TurnHalt(reason) + NO TurnDone.
     This is the approval-halt shape.

  3. {deferred: True, deferred_id, timeout_s} — model calls a background
     tool; executor submits a job and returns the deferred envelope;
     runtime SUPPRESSES ToolResult + emits TurnHalt('deferred') + NO TurnDone.

Cost: ~$0.03 (three Haiku turns). Same OAuth-CC path as the other smokes.

Run: .venv/bin/python tests/e2e/sdk_runtime_envelope_smoke.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_TMP = Path(tempfile.mkdtemp(prefix="aba_sdk_envelope_"))
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


def _build_request(*, tool_name: str, tool_descr: str, user_prompt: str,
                   sys_prompt: str, schema: dict) -> RuntimeRequest:
    return RuntimeRequest(
        history=[{"role": "user", "content": user_prompt}],
        tools=[{
            "name": tool_name,
            "description": tool_descr,
            "input_schema": schema,
        }],
        system=SystemSpec(stable=sys_prompt, dynamic=""),
        model="claude-haiku-4-5-20251001",
        max_tokens=4096,
        ctx={},
    )


async def _drive(req, executor, *, label: str) -> tuple[list, list[dict]]:
    """Run one turn under AgentSDKRuntime; print events; return collected
    events + executor hits."""
    print(f"── {label} ────────────────────────────────────────────")
    rt = AgentSDKRuntime()
    events: list = []
    hits: list[dict] = []

    async def _tap(name: str, args: dict, ctx: dict) -> dict:
        hits.append({"name": name, "args": args})
        return await executor(name, args, ctx)

    async for ev in rt.run_turn(req, _tap):
        events.append(ev)
        kind = type(ev).__name__
        if isinstance(ev, TextDelta):
            print(f"  [TextDelta]    {ev.text[:60]!r}")
        elif isinstance(ev, ToolUseStart):
            print(f"  [ToolUseStart] name={ev.tool_name!r}")
        elif isinstance(ev, ToolResult):
            print(f"  [ToolResult]   id={ev.tool_use_id[:14]}..  "
                  f"name={ev.tool_name!r}  keys={sorted(ev.result.keys())!r}")
        elif isinstance(ev, TurnHalt):
            print(f"  [TurnHalt]     reason={ev.reason!r}  "
                  f"tool={ev.detail.get('tool_name')!r}  "
                  f"detail_keys={sorted(k for k in ev.detail if k != 'tool_name')!r}")
        elif isinstance(ev, TurnDone):
            print(f"  [TurnDone]     stop_reason={ev.stop_reason!r}")
        else:
            print(f"  [{kind}]")
    print()
    return events, hits


# ─────────────────── Test 1: _runtime_halt_after ────────────────────

async def _exec_after(name: str, args: dict, ctx: dict) -> dict:
    return {
        "status": "presented",
        "plan_entity_id": "plan_FAKE_001",
        "_runtime_halt_after": "plan",
    }


async def test_after():
    req = _build_request(
        tool_name="present_plan",
        tool_descr="Present a structured plan with title + steps.",
        user_prompt=("Use the present_plan tool to outline a 3-step plan "
                     "for cleaning a dataset. Use the tool — do not just "
                     "describe it in text."),
        sys_prompt=("You are a planning test agent. When asked for a plan, "
                    "you MUST call the `present_plan` tool."),
        schema={
            "type": "object",
            "properties": {"title": {"type": "string"},
                            "steps": {"type": "array",
                                       "items": {"type": "string"}}},
            "required": ["title", "steps"],
        },
    )
    events, hits = await _drive(req, _exec_after, label="halt_after / plan")
    fails: list[str] = []
    tool_uses = [e for e in events if isinstance(e, ToolUseStart)]
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    halts = [e for e in events if isinstance(e, TurnHalt)]
    dones = [e for e in events if isinstance(e, TurnDone)]
    if not tool_uses or tool_uses[0].tool_name != "present_plan":
        fails.append("model did not call present_plan")
    if len(hits) != 1:
        fails.append(f"executor hits={len(hits)} (expected 1)")
    if not tool_results:
        fails.append("no ToolResult emitted — halt_after must yield it")
    elif "_runtime_halt_after" in (tool_results[0].result or {}):
        fails.append("halt marker leaked into ToolResult.result")
    if not halts:
        fails.append("no TurnHalt emitted")
    elif halts[0].reason != "plan":
        fails.append(f"wrong reason: {halts[0].reason!r}")
    if dones:
        fails.append("TurnDone fired alongside TurnHalt")
    return fails


# ─────────────────── Test 2: _runtime_halt_before (approval) ─────────

async def _exec_before(name: str, args: dict, ctx: dict) -> dict:
    return {
        "_runtime_halt_before": "approval",
        "_emit_sse_at_halt": {"type": "approval_pending",
                              "tool_name": name},
    }


async def test_before():
    req = _build_request(
        tool_name="run_expensive_job",
        tool_descr="Submit a costly compute job.",
        user_prompt=("Use the run_expensive_job tool with a dummy "
                     "config to test the approval flow. Just call it "
                     "once — do not describe what you would do."),
        sys_prompt="You are a test agent. When asked to use a tool, call it.",
        schema={
            "type": "object",
            "properties": {"job_kind": {"type": "string"}},
            "required": ["job_kind"],
        },
    )
    events, hits = await _drive(req, _exec_before,
                                  label="halt_before / approval")
    fails: list[str] = []
    tool_uses = [e for e in events if isinstance(e, ToolUseStart)]
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    halts = [e for e in events if isinstance(e, TurnHalt)]
    dones = [e for e in events if isinstance(e, TurnDone)]
    if not tool_uses or tool_uses[0].tool_name != "run_expensive_job":
        fails.append("model did not call run_expensive_job")
    if len(hits) != 1:
        fails.append(f"executor hits={len(hits)} (expected 1)")
    if tool_results:
        fails.append(f"ToolResult fired but halt_before must suppress "
                     f"it: {[r.result for r in tool_results]!r}")
    if not halts:
        fails.append("no TurnHalt emitted")
    elif halts[0].reason != "approval":
        fails.append(f"wrong reason: {halts[0].reason!r}")
    elif halts[0].detail.get("tool_name") != "run_expensive_job":
        fails.append(f"halt detail lost tool_name: {halts[0].detail!r}")
    elif "_emit_sse_at_halt" not in halts[0].detail:
        fails.append("halt detail dropped _emit_sse_at_halt envelope")
    if dones:
        fails.append("TurnDone fired alongside TurnHalt")
    return fails


# ─────────────────── Test 3: {deferred: True} ────────────────────────

async def _exec_deferred(name: str, args: dict, ctx: dict) -> dict:
    return {
        "deferred": True,
        "deferred_id": "job_FAKE_42",
        "timeout_s": 600,
    }


async def test_deferred():
    req = _build_request(
        tool_name="background_run",
        tool_descr="Submit a background job and get a queued ack.",
        user_prompt=("Use background_run with code='print(1)' to submit "
                     "the job and stop. Just call the tool once."),
        sys_prompt="You are a test agent. When asked to use a tool, call it.",
        schema={
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    )
    events, hits = await _drive(req, _exec_deferred,
                                  label="deferred / HPC")
    fails: list[str] = []
    tool_uses = [e for e in events if isinstance(e, ToolUseStart)]
    tool_results = [e for e in events if isinstance(e, ToolResult)]
    halts = [e for e in events if isinstance(e, TurnHalt)]
    dones = [e for e in events if isinstance(e, TurnDone)]
    if not tool_uses or tool_uses[0].tool_name != "background_run":
        fails.append("model did not call background_run")
    if len(hits) != 1:
        fails.append(f"executor hits={len(hits)} (expected 1)")
    if tool_results:
        fails.append("ToolResult fired but deferred must suppress it")
    if not halts:
        fails.append("no TurnHalt emitted")
    elif halts[0].reason != "deferred":
        fails.append(f"wrong reason: {halts[0].reason!r}")
    elif halts[0].detail.get("deferred_id") != "job_FAKE_42":
        fails.append(f"halt detail lost deferred_id: {halts[0].detail!r}")
    if dones:
        fails.append("TurnDone fired alongside TurnHalt")
    return fails


async def main():
    print("AgentSDKRuntime R-3.3.b envelope-halt smoke — Haiku via oauth_cc")
    print()
    all_fails: list[tuple[str, str]] = []
    for label, fn in [("halt_after",  test_after),
                      ("halt_before", test_before),
                      ("deferred",    test_deferred)]:
        fails = await fn()
        all_fails += [(label, f) for f in fails]
    print()
    if all_fails:
        print("FAIL:")
        for label, f in all_fails:
            print(f"  [{label}] {f}")
        sys.exit(1)
    print("OK — all three envelope halts translate cleanly")


if __name__ == "__main__":
    asyncio.run(main())
