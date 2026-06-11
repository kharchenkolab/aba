"""AgentSDKRuntime — LLMRuntime backed by `claude-agent-sdk`.

Alternative implementation to DirectAPIRuntime that delegates the
model-driven phase to Anthropic's Claude Agent SDK
(`claude_agent_sdk.ClaudeSDKClient`). Same protocol — pick-by-spec via
`AgentSpec.runtime = 'sdk'` or `ABA_RUNTIME_OVERRIDE=sdk`.

Why it earns its place alongside DirectAPIRuntime:
- The SDK absorbs retry / cache placement / per-session compaction
  that we hand-roll in core/llm.py + open_and_consume_stream.
- The SDK speaks MCP natively; bio tools surface via
  `create_sdk_mcp_server` (memory transport, no subprocess).
- The SDK ships its own CC marker for oauth_cc credentials; verified
  to work with Haiku/Sonnet/Opus during 2026-06-11 spike #3.

What stays in ABA's outer harness regardless of runtime:
- guide.py's outer multi-turn loop + halt-state machine.
- The TurnSink / scribe / focus / manifest / on_stop hook layer.
- The eval-harness history-seeding path — DirectAPIRuntime owns that.

Phase status:
  R-3.1 — skeleton                                              [done]
  R-3.2 — run_turn: SDK session + event translation +
          tool-executor adapter.                                 [done]
  R-3.3.a — halt_on_tools via PreToolUse hook + interrupt.       [done]
  R-3.3.b — translate executor halt envelopes
            ({deferred}, {_runtime_halt_before}, {_runtime_halt_after})
            into TurnHalt + interrupt.                           [this commit]
  R-3.3.c — bridge ABA PreToolUse/PostToolUse hook stack.        [next]
  R-3.5  — flip Methodologist + Critic to runtime: sdk.          [later]
"""
from __future__ import annotations

import json as _json
import queue as _queue
import uuid
from typing import AsyncIterator

from core.runtime.llm_runtime import (
    LLMRuntime,
    RuntimeEvent,
    RuntimeRequest,
    TextDelta,
    ToolExecutor,
    ToolResult,
    ToolUseStart,
    TurnDone,
)


# MCP server name we use to expose bio tools to the SDK. The full
# SDK-side name is `mcp__<this>__<tool_name>`; we strip the prefix
# when emitting ToolUseStart / ToolResult so the rest of ABA sees
# the bare tool name it expects.
_MCP_SERVER_NAME = "aba_runtime"
_MCP_TOOL_PREFIX = f"mcp__{_MCP_SERVER_NAME}__"

# Claude Code default tools we explicitly disallow — without these the
# model sees ToolSearch, Read, Write, Edit, Bash, etc. as candidates
# (Spike #1 observed the model trying ToolSearch on a simple add task).
# ABA agents speak bio's MCP surface only.
_DISALLOWED_CC_DEFAULTS = [
    "Read", "Write", "Edit", "Bash", "Grep", "Glob",
    "WebFetch", "WebSearch", "ToolSearch",
    "Task", "TodoWrite", "NotebookEdit",
]


def _strip_mcp_prefix(name: str) -> str:
    """`mcp__aba_runtime__foo` → `foo`. Any other prefix passes through
    untouched (defensive: lets us evolve the server name without
    breaking translation)."""
    return name[len(_MCP_TOOL_PREFIX):] if name.startswith(_MCP_TOOL_PREFIX) else name


def _parse_tool_result_content(content) -> dict:
    """SDK's ToolResultBlock.content is what the MCP server returned —
    a list of {type, text} blocks or a raw string. Our internal
    ToolResult event carries a dict (the result the executor returned).
    Round-trip via JSON since our handler dumps the result as JSON in
    the text block."""
    if isinstance(content, list):
        for blk in content:
            if isinstance(blk, dict) and blk.get("type") == "text":
                txt = blk.get("text", "")
                try:
                    return _json.loads(txt)
                except (_json.JSONDecodeError, TypeError):
                    return {"text": txt}
        return {}
    if isinstance(content, str):
        try:
            return _json.loads(content)
        except (_json.JSONDecodeError, TypeError):
            return {"text": content}
    return {}


class AgentSDKRuntime:
    """LLMRuntime backed by claude_agent_sdk.ClaudeSDKClient.

    Current support (R-3.2 + R-3.3.a + R-3.3.b):
      - text + tool dispatch + TurnDone end-to-end
      - bio tools via in-process MCP (memory transport)
      - history seeded via interleaved query() messages (spike #2)
      - dynamic catalog as <system-reminder>-wrapped first user message
        (Option A; spike #4 measured +0.4% cache cost)
      - halt_on_tools (R-3.3.a): a PreToolUse hook denies + interrupts
        the session before MCP dispatch → TurnHalt(pending_tool).
      - executor halt envelopes (R-3.3.b):
          {_runtime_halt_before: <reason>, ...}  → TurnHalt(reason);
              ToolResult event SUPPRESSED. Approval halts ride this.
          {_runtime_halt_after:  <reason>, ...}  → ToolResult + TurnHalt
              (reason). present_plan + ask_clarification ride this.
          {deferred: True, deferred_id, timeout_s?} → TurnHalt(deferred);
              ToolResult event SUPPRESSED. HPC / background jobs.

    NOT yet wired (R-3.3.c, R-3.5):
      - PreToolUse / PostToolUse hook bridging into core/runtime/hooks.
      - Methodologist + Critic agent flip to runtime: sdk (R-3.5).
    """

    async def run_turn(
        self,
        req: RuntimeRequest,
        tool_executor: ToolExecutor,
        halt_on_tools: frozenset[str] = frozenset(),
    ) -> AsyncIterator[RuntimeEvent]:
        # Lazy-import — the SDK is ~75 MB and only this code path needs
        # it. Direct + fake runtimes don't pay this cost.
        from claude_agent_sdk import (   # noqa: PLC0415
            ClaudeAgentOptions, ClaudeSDKClient,
            SdkMcpTool, create_sdk_mcp_server,
            AssistantMessage, UserMessage, ResultMessage,
            HookMatcher,
        )
        from claude_agent_sdk.types import (   # noqa: PLC0415
            TextBlock, ToolUseBlock, ToolResultBlock,
        )

        # R-3.3.b envelope-driven halts: the executor returns one of
        # three special envelopes to signal a halt. The MCP handler
        # cannot synthesize a halt directly (it must return a content
        # block to satisfy the SDK contract), so it stashes the halt
        # intent here and the main loop reacts when the synthetic
        # ToolResultBlock surfaces. Mirrors DirectAPIRuntime semantics:
        #   {deferred: True, deferred_id, timeout_s?}
        #       → TurnHalt(deferred); ToolResult event suppressed.
        #   {_runtime_halt_before: "<reason>", ...}
        #       → TurnHalt(<reason>); ToolResult event suppressed; the
        #         tool's tool_use stays unresolved in ABA's manifest.
        #   {_runtime_halt_after: "<reason>", ...}
        #       → ToolResult emitted (sans marker) + TurnHalt(<reason>).
        envelope_halt: dict | None = None

        # ── 1. Build SDK MCP server bridging bio tools to our executor ──
        # Each entry in req.tools is an Anthropic-format schema
        # ({name, description, input_schema}). The SDK accepts a full
        # JSON Schema dict when it has type+properties keys (verified
        # against claude_agent_sdk/__init__.py:_build_schema).
        sdk_tools: list = []
        for schema in (req.tools or []):
            t_name = schema["name"]

            # Closure captures the tool name so SDK's handler invocation
            # (which doesn't pass the name) can still tell which tool
            # was called.
            def _make_handler(tname: str):
                async def _handler(args: dict) -> dict:
                    nonlocal envelope_halt
                    # Per-call ctx: our tool_use_id is fresh (the SDK's
                    # tool_use_id isn't passed through to MCP handlers;
                    # we reconcile via the ToolUseStart event below).
                    ctx = {**req.ctx,
                           "tool_use_id": f"toolu_sdk_{uuid.uuid4().hex[:12]}",
                           "progress_q": _queue.Queue()}
                    result = await tool_executor(tname, args, ctx)

                    # Envelope translation. First halt observed wins —
                    # subsequent dispatches in the same run_turn are
                    # cut off by the interrupt the main loop fires.
                    if isinstance(result, dict) and envelope_halt is None:
                        if result.get("deferred"):
                            envelope_halt = {
                                "kind": "before",   # suppress ToolResult
                                "reason": "deferred",
                                "tool_name": tname,
                                "detail": {
                                    "deferred_id": result.get("deferred_id"),
                                    "timeout_s": result.get("timeout_s"),
                                },
                            }
                        elif "_runtime_halt_before" in result:
                            reason = result.pop("_runtime_halt_before")
                            envelope_halt = {
                                "kind": "before",
                                "reason": reason,
                                "tool_name": tname,
                                "detail": dict(result),
                            }
                        elif "_runtime_halt_after" in result:
                            reason = result.pop("_runtime_halt_after")
                            envelope_halt = {
                                "kind": "after",    # yield ToolResult first
                                "reason": reason,
                                "tool_name": tname,
                                "detail": dict(result),
                            }

                    # Wrap our dict result in MCP's content shape. The
                    # SDK requires a return value even when we plan to
                    # interrupt — for `before`/`deferred` the model
                    # would see this stub momentarily, but the main
                    # loop's interrupt aborts the next assistant turn
                    # so it never persists in ABA's manifest history.
                    return {
                        "content": [{"type": "text",
                                      "text": _json.dumps(result, default=str)}],
                        "isError": bool(isinstance(result, dict)
                                         and (result.get("error")
                                              or result.get("status") == "error")),
                    }
                return _handler

            sdk_tools.append(SdkMcpTool(
                name=t_name,
                description=schema.get("description", ""),
                input_schema=schema.get("input_schema", {"type": "object",
                                                          "properties": {}}),
                handler=_make_handler(t_name),
            ))

        # ── 2. Build SDK options ──
        mcp_servers: dict = {}
        allowed_tools: list[str] = []
        if sdk_tools:
            mcp_servers[_MCP_SERVER_NAME] = create_sdk_mcp_server(
                _MCP_SERVER_NAME, tools=sdk_tools,
            )
            allowed_tools = [f"{_MCP_TOOL_PREFIX}{t.name}" for t in sdk_tools]

        # R-3.3.a halt_on_tools: intercept via PreToolUse hook.
        #
        # Why a hook, not `can_use_tool`: `can_use_tool` only fires when
        # the SDK's permission ladder evaluates to "ask". We run under
        # permission_mode="bypassPermissions" so MCP bio tools dispatch
        # without prompting — and that mode also skips `can_use_tool`
        # entirely. PreToolUse hooks always fire (verified by inspection
        # of claude_agent_sdk/_internal/transport hook plumbing).
        #
        # The hook returns `continue_: False` + `permissionDecision:
        # "deny"`, which (a) suppresses the MCP handler dispatch and
        # (b) ends the session with a ResultMessage. We translate to
        # TurnHalt(pending_tool) at the post-session step.
        #
        # nonlocal state captures the intercepted tool so the post-
        # session translator can synthesize TurnHalt. None = no halt
        # observed; dict = the held tool.
        halt_intercepted: dict | None = None

        async def _pre_tool_use_hook(input_data: dict, tool_use_id, context):
            nonlocal halt_intercepted
            raw_name = input_data.get("tool_name", "")
            stripped = _strip_mcp_prefix(raw_name)
            if stripped in halt_on_tools:
                halt_intercepted = {
                    "tool_name": stripped,
                    "input": input_data.get("tool_input") or {},
                    "tool_use_id": input_data.get("tool_use_id")
                                    or tool_use_id,
                }
                return {
                    "continue_": False,
                    "stopReason": f"halt-on-tool: {stripped}",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason":
                            "halt_on_tools intercept",
                    },
                }
            return {}

        opts = ClaudeAgentOptions(
            system_prompt=req.system.stable,
            mcp_servers=mcp_servers,
            allowed_tools=allowed_tools,
            disallowed_tools=_DISALLOWED_CC_DEFAULTS,
            model=req.model,
            # max_turns caps the SDK's own multi-turn loop within ONE
            # run_turn() call. ABA's outer loop in guide.py re-enters
            # run_turn() per phase, so a generous SDK budget here is
            # fine — the outer halt-state machine still controls phase
            # boundaries.
            max_turns=20,
            # bypassPermissions skips SDK prompt UI for MCP bio tools.
            # halt_on_tools rides through PreToolUse below (always fires
            # regardless of permission_mode).
            permission_mode="bypassPermissions",
            hooks={"PreToolUse": [HookMatcher(matcher=None,
                                              hooks=[_pre_tool_use_hook])]},
        )

        # ── 3. Seed history + dynamic-catalog as a stream of query frames ──
        # Per spike #2: the SDK accepts pre-baked assistant + tool_use
        # + tool_result blocks via interleaved query() calls. The
        # `type` field is 'user' or 'assistant'; 'message.content' is
        # the same shape as the Anthropic API.
        history = list(req.history)
        # Option A (spike #4, +0.4% cache neutral): the dynamic recipes
        # catalog rides as a synthetic <system-reminder>-wrapped user
        # message BEFORE the conversation. The stable system stays in
        # the SDK-cached system block.
        catalog_msg: dict | None = None
        if req.system.dynamic:
            catalog_msg = {
                "role": "user",
                "content": f"<system-reminder>\n{req.system.dynamic}\n</system-reminder>",
            }

        async def _msg_stream():
            if catalog_msg is not None:
                yield {"type": "user", "message": catalog_msg,
                       "parent_tool_use_id": None}
            for msg in history:
                yield {"type": msg["role"],
                       "message": {"role": msg["role"], "content": msg["content"]},
                       "parent_tool_use_id": None}

        # ── 4. Run the session; translate events ──
        stop_reason = "end_turn"
        usage_delta = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        async with ClaudeSDKClient(options=opts) as client:
            await client.query(_msg_stream())
            async for msg in client.receive_response():
                if isinstance(msg, AssistantMessage):
                    # If a halt was already raised on a prior
                    # ToolUseBlock — either via the halt_on_tools
                    # PreToolUse hook OR via an executor envelope —
                    # the model's follow-up retries (more ToolUseBlocks)
                    # and its filler-text explanation of the denial
                    # belong in the bit-bucket. Interrupt + break.
                    if halt_intercepted is not None or envelope_halt is not None:
                        try:
                            await client.interrupt()
                        except Exception:
                            pass
                        break
                    # Per-block translation: TextBlock -> TextDelta;
                    # ToolUseBlock -> ToolUseStart. ThinkingBlock blocks
                    # are silently dropped (we don't surface them in
                    # DirectAPIRuntime either).
                    for blk in msg.content:
                        if isinstance(blk, TextBlock):
                            yield TextDelta(text=blk.text)
                        elif isinstance(blk, ToolUseBlock):
                            yield ToolUseStart(
                                tool_use_id=blk.id,
                                tool_name=_strip_mcp_prefix(blk.name),
                                input=blk.input or {},
                            )
                    # Accumulate per-turn usage from the assistant message
                    # if the SDK populates it. ResultMessage at the end
                    # has the canonical totals; this is a best-effort
                    # tick-by-tick fallback.
                    u = getattr(msg, "usage", None)
                    if u:
                        usage_delta["input"]       += u.get("input_tokens", 0) or 0
                        usage_delta["output"]      += u.get("output_tokens", 0) or 0
                        usage_delta["cache_read"]  += u.get("cache_read_input_tokens", 0) or 0
                        usage_delta["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
                elif isinstance(msg, UserMessage):
                    # The SDK delivers tool_results back as UserMessage
                    # with ToolResultBlock content. Mirror these into
                    # our ToolResult events so the outer layer can
                    # persist + render them — but two halt cases
                    # suppress or rewrite the event:
                    #   1) halt_on_tools (R-3.3.a) — synthetic deny echo
                    #   2) envelope halt before/deferred (R-3.3.b) — the
                    #      stub stays out of guide.py's manifest
                    #   3) envelope halt after — yield the stripped real
                    #      content, then emit TurnHalt + interrupt
                    if halt_intercepted is not None:
                        continue
                    if not isinstance(msg.content, list):
                        continue
                    for blk in msg.content:
                        if not isinstance(blk, ToolResultBlock):
                            continue
                        if envelope_halt is not None:
                            # First ToolResultBlock observed after a
                            # halt envelope is the one the held tool
                            # produced. Capture the SDK's real
                            # tool_use_id into the halt detail.
                            envelope_halt["tool_use_id"] = blk.tool_use_id
                            if envelope_halt["kind"] == "before":
                                # suppress entirely; main loop's break
                                # below + post-loop translator emit
                                # TurnHalt(reason).
                                pass
                            else:
                                # halt_after: emit ToolResult with the
                                # stripped real content + the SDK's
                                # tool_use_id, then break to translator.
                                yield ToolResult(
                                    tool_use_id=blk.tool_use_id,
                                    tool_name=envelope_halt["tool_name"],
                                    result=envelope_halt["detail"],
                                )
                            try:
                                await client.interrupt()
                            except Exception:
                                pass
                            break
                        yield ToolResult(
                            tool_use_id=blk.tool_use_id,
                            # Tool name isn't carried on the
                            # ToolResultBlock; caller correlates
                            # by tool_use_id with the prior
                            # ToolUseStart.
                            tool_name="",
                            result=_parse_tool_result_content(blk.content),
                        )
                    if envelope_halt is not None:
                        break
                elif isinstance(msg, ResultMessage):
                    # Terminal event for this session. Use the canonical
                    # usage + stop_reason. break out of the loop so
                    # __aexit__ runs and ClaudeSDKClient cleans up.
                    if msg.stop_reason:
                        stop_reason = msg.stop_reason
                    u = msg.usage or {}
                    if u:
                        usage_delta = {
                            "input":       u.get("input_tokens", 0) or 0,
                            "output":      u.get("output_tokens", 0) or 0,
                            "cache_read":  u.get("cache_read_input_tokens", 0) or 0,
                            "cache_write": u.get("cache_creation_input_tokens", 0) or 0,
                        }
                    break
                # Other message types (SystemMessage with init/setup
                # info, RateLimitEvent, etc.) we currently ignore.
                # When R-3.3 adds hook bridging, some may surface.

        # R-3.3.a: PreToolUse hook denied a halt-on-tools call.
        # Translate to TurnHalt(pending_tool). The ToolUseStart event
        # was already yielded above (before the SDK consulted the
        # hook), so guide.py has already observed the model's intent;
        # the halt event tells it the dispatch was suppressed.
        if halt_intercepted is not None:
            # If the SDK didn't surface a tool_use_id via the hook
            # input (older SDK builds), synthesize one so downstream
            # consumers have something stable to key on.
            tool_use_id = halt_intercepted.get("tool_use_id") or \
                          f"toolu_halt_{uuid.uuid4().hex[:12]}"
            from core.runtime.llm_runtime import TurnHalt
            yield TurnHalt(reason="pending_tool", detail={
                "tool_name":   halt_intercepted["tool_name"],
                "input":       halt_intercepted["input"],
                "tool_use_id": tool_use_id,
            })
            return

        # R-3.3.b: executor returned a halt envelope. Translate to the
        # matching TurnHalt. The ToolResult event (for `after` only)
        # has already been yielded inside the UserMessage branch
        # above; here we emit the halt signal.
        if envelope_halt is not None:
            from core.runtime.llm_runtime import TurnHalt
            detail = {
                **envelope_halt["detail"],
                "tool_name": envelope_halt["tool_name"],
                "tool_use_id": envelope_halt.get("tool_use_id"),
            }
            yield TurnHalt(reason=envelope_halt["reason"], detail=detail)
            return

        yield TurnDone(stop_reason=stop_reason, usage=usage_delta)


def _conforms_to_protocol() -> bool:
    """Cheap method-presence check — the protocol module doesn't use
    `runtime_checkable`, so we inline a hasattr check here."""
    return all(hasattr(AgentSDKRuntime, m) for m in ("run_turn",))
