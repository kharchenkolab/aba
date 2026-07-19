"""Drive canonical chat scenarios against a real LLMRuntime.

For each Scenario in tests.scenarios.SCENARIOS we:
  1. Build a RuntimeRequest with the lean fixtures (real system
     prompt + real tools) + a one-message history (the user prompt).
  2. Run the chosen runtime through the agent loop, dispatching the
     scenario's MOCK tool_executor each time a tool fires.
  3. Record the (tool_name, tool_input) sequence.
  4. Evaluate the scenario's assertions and report each.

The runtime under test is chosen via ABA_TEST_RUNTIME:
  - "openai" (default if endpoint reachable) — local Qwen3 on vLLM
  - "direct" — Anthropic (skipped without credentials)

Both run the SAME assertions, so the same regression suite covers
both backends.

When a scenario fails, the report includes the tool call sequence so
you can see what the model did instead. That's the artifact you'd
hand back to "iterate on the description / system prompt"."""
from __future__ import annotations
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_chat_scen_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "s.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))

from scenarios import SCENARIOS, Scenario                       # noqa: E402

pytestmark = pytest.mark.bio   # scenarios with use_real_tool dispatch
                                # bio tools (search_skills, Skill, …);
                                # conftest's bio-pack registration is
                                # needed before they fire.


FIXTURES = ROOT.parent / "local-llm" / "phase0" / "fixtures" / "lean_guide"
DEFAULT_RUNTIME = os.environ.get("ABA_TEST_RUNTIME", "openai")
OPENAI_BASE     = os.environ.get("ABA_OPENAI_BASE_URL",
                                  "http://localhost:8001/v1")


def _openai_reachable() -> bool:
    try:
        import openai
        openai.OpenAI(base_url=OPENAI_BASE, api_key="none").models.list()
        return True
    except Exception:                                          # noqa: BLE001
        return False


def _fixtures_present() -> bool:
    return ((FIXTURES / "aba_system.txt").is_file()
            and (FIXTURES / "aba_tools_anthropic.json").is_file())


SKIP = pytest.mark.skipif(
    not _fixtures_present(),
    reason="lean fixtures missing — run scripts/dump_phase0_fixture.py",
)
SKIP_OPENAI = pytest.mark.skipif(
    DEFAULT_RUNTIME == "openai" and not _openai_reachable(),
    reason=f"vLLM endpoint not reachable at {OPENAI_BASE}",
)


# ── Runtime construction ───────────────────────────────────────────


def _build_runtime(name: str):
    if name == "openai":
        from core.runtime.llm_runtime_openai import OpenAICompatibleRuntime
        return OpenAICompatibleRuntime(base_url=OPENAI_BASE, api_key="none",
                                        enable_thinking=False)
    if name == "direct":
        from core.runtime.llm_runtime_direct import DirectAPIRuntime
        return DirectAPIRuntime()
    raise ValueError(f"unknown ABA_TEST_RUNTIME: {name!r}")


def _model_for(runtime_name: str) -> str:
    if runtime_name == "openai":
        # Honor ABA_OPENAI_MODEL so a different endpoint variant (8B,
        # 30B-A3B, …) flows through without code changes. Fall back to
        # the historical default for back-compat with old test runs.
        return os.environ.get("ABA_OPENAI_MODEL") or "qwen3-8b"
    return "claude-haiku-4-5-20251001"


# ── Scenario driver ────────────────────────────────────────────────


def _run_scenario(s: Scenario, runtime_name: str = DEFAULT_RUNTIME) -> dict:
    """Drive one scenario and return a record of what happened."""
    from core.runtime.llm_runtime import (RuntimeRequest, SystemSpec,
                                          TextDelta, ToolUseStart,
                                          ToolResult, TurnDone, TurnHalt)

    system_text = (FIXTURES / "aba_system.txt").read_text()
    tools_ant   = json.loads(
        (FIXTURES / "aba_tools_anthropic.json").read_text())

    rt = _build_runtime(runtime_name)
    history: list[dict] = [
        {"role": "user", "content": s.user_prompt},
    ]
    calls: list[tuple[str, dict]] = []

    # Real-tool dispatch uses the SAME MCP gateway the live server
    # routes through — `core.runtime.mcp.call`. Going through bio's
    # `execute_tool` directly skips the gateway's BM25 index init, so
    # search_skills would return empty for everything (live bug
    # masked in prj_52ec6529 → suite false-green).
    real_dispatch = None
    if s.use_real_tool:
        # Initialize the in-process aba_core server so the gateway
        # has live tool handles to route through.
        import content.bio                                       # noqa: F401
        from core.runtime.mcp import (call as _mcp_call,
                                       register_inprocess_server,
                                       _reset_for_testing)
        from content.bio.mcp_servers.aba_core import make_server
        _reset_for_testing()
        register_inprocess_server("aba_core", make_server,
                                  expose_in_catalog=True,
                                  strip_prefix_in_catalog=True)
        real_dispatch = _mcp_call

    async def tool_exec(name: str, input_: dict, ctx: dict) -> dict:
        calls.append((name, dict(input_) if isinstance(input_, dict) else {}))
        if name in s.use_real_tool and real_dispatch is not None:
            try:
                result = real_dispatch(name, dict(input_ or {}))
                if isinstance(result, str):
                    return json.loads(result)
                return result
            except Exception as e:                              # noqa: BLE001
                return {"status": "error",
                        "_real_dispatch_error": str(e),
                        "_tool": name}
        mock = s.tool_mocks.get(name)
        if mock is not None:
            return mock(input_ or {})
        # Generic ok — keeps the loop moving for unmocked tools.
        return {"status": "ok", "_unmocked_tool": name}

    text_buf:    list[str] = []
    last_assistant_text = ""
    halted = False
    halt_reason = ""

    async def one_turn() -> tuple[bool, str | None]:
        """Run one model phase. Returns (should_loop, stop_reason).
        should_loop=True means we appended a tool_result and the model
        wants another turn (Anthropic 'tool_use' semantics)."""
        nonlocal last_assistant_text
        text_buf.clear()
        assistant_blocks: list[dict] = []
        stop_reason: str | None = None

        req = RuntimeRequest(
            history=list(history),
            tools=tools_ant,
            system=SystemSpec(stable=system_text, dynamic=""),
            model=_model_for(runtime_name),
            max_tokens=2048,
            ctx={"thread_id": "thr_scenario"},
        )

        async for ev in rt.run_turn(req, tool_exec):
            if isinstance(ev, TextDelta):
                text_buf.append(ev.text)
            elif isinstance(ev, ToolUseStart):
                assistant_blocks.append({"type": "tool_use",
                                          "id":    ev.tool_use_id,
                                          "name":  ev.tool_name,
                                          "input": ev.input})
            elif isinstance(ev, ToolResult):
                # Append assistant tool_use we collected + the result.
                # If the runtime emitted _StreamCompleted with text,
                # we'll capture it via the text_buf above.
                history.append({"role":    "assistant",
                                "content": assistant_blocks})
                history.append({"role":    "user",
                                "content": [{"type": "tool_result",
                                              "tool_use_id": ev.tool_use_id,
                                              "content": json.dumps(
                                                  ev.result, default=str)}]})
                assistant_blocks = []
            elif isinstance(ev, TurnDone):
                stop_reason = ev.stop_reason
                if text_buf:
                    last_assistant_text = "".join(text_buf)
                    # If we have text + assistant_blocks already, fold
                    # the text into the assistant turn we're about to
                    # cap. Otherwise the bare text becomes its own msg.
                    if not assistant_blocks:
                        history.append({
                            "role":    "assistant",
                            "content": [{"type": "text",
                                          "text":  last_assistant_text}],
                        })
                break
            elif isinstance(ev, TurnHalt):
                nonlocal halted, halt_reason
                halted = True
                halt_reason = f"{ev.reason}: {ev.detail}"
                return False, ev.reason
            else:
                # _StreamCompleted (private) — ignore for the loop;
                # we already extracted what we need from public events.
                pass

        # Anthropic semantics: loop again only if the model used tools.
        return (stop_reason == "tool_use", stop_reason)

    async def drive():
        for _ in range(s.max_turns):
            should_loop, _ = await one_turn()
            if not should_loop:
                break
            if halted:
                break

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(drive())
    finally:
        loop.close()

    # Evaluate assertions.
    results = []
    for a in s.assertions:
        ok, detail = a.predicate(calls)
        results.append({"name": a.name, "ok": ok, "detail": detail})

    return {
        "scenario":    s.name,
        "runtime":     runtime_name,
        "calls":       [{"tool": n, "input": i} for n, i in calls],
        "halted":      halted,
        "halt_reason": halt_reason,
        "last_text":   last_assistant_text[:400],
        "assertions":  results,
    }


# ── Tests — one per scenario ───────────────────────────────────────


@SKIP
@SKIP_OPENAI
@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_scenario_against_runtime(scenario):
    """Run one scenario. The pytest failure message includes the
    sequence of tool calls + which assertions passed/failed so you
    can act on it directly."""
    report = _run_scenario(scenario)
    failed = [r for r in report["assertions"] if not r["ok"]]
    if failed:
        # Pretty-print the report into the failure message so the
        # diagnostic is right there.
        lines = ["\n", f"SCENARIO {scenario.name} on {report['runtime']}:"]
        lines.append(f"  user: {scenario.user_prompt!r}")
        lines.append(f"  halted: {report['halted']} ({report['halt_reason']})")
        lines.append(f"  tool calls ({len(report['calls'])}):")
        for c in report["calls"]:
            lines.append(f"    🔧 {c['tool']}({json.dumps(c['input'])[:140]})")
        lines.append(f"  assistant tail: {report['last_text']!r}")
        lines.append("  assertions:")
        for r in report["assertions"]:
            mark = "✓" if r["ok"] else "✗"
            lines.append(f"    {mark} {r['name']}: {r['detail']}")
        pytest.fail("\n".join(lines))
    # On success, still emit the trace via stdout so a `-s` run shows it.
    print(f"\n[{scenario.name}] {len(report['calls'])} calls; "
          f"all {len(report['assertions'])} assertions passed")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
