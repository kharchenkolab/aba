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
  R-3.2 — run_turn: SDK session + event translation + tool
          executor adapter. Halt support NOT yet — halt_on_tools
          raises NotImplementedError, and result envelopes
          (_runtime_halt_*, deferred) flow through unchanged.    [this commit]
  R-3.3 — hook adapters + halt support (halt_on_tools, deferred,
          _runtime_halt_before/after)                            [next]
  R-3.4 — full Methodologist/Critic flip                          [later]
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

    Current support (R-3.2 + R-3.3.a):
      - text + tool dispatch + TurnDone end-to-end
      - bio tools via in-process MCP (memory transport)
      - history seeded via interleaved query() messages (spike #2)
      - dynamic catalog as <system-reminder>-wrapped first user message
        (Option A; spike #4 measured +0.4% cache cost)
      - halt_on_tools: when the model issues a tool in the set, the
        PreToolUse hook returns continue_=False + permissionDecision=
        "deny" → SDK ends the session before MCP dispatch → we emit
        TurnHalt(reason='pending_tool', detail=...) instead of TurnDone.

    NOT yet wired (R-3.3.b-e):
      - {_runtime_halt_before / _after} envelopes flow through unchanged.
      - {deferred: True} envelopes flow through (model sees the queued
        ack and the session ends naturally — same as DirectAPIRuntime).
      - PreToolUse / PostToolUse hook bridging (R-3.3.c).
      - SDK-native defer for approval halts (R-3.3.b).
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
                    # Per-call ctx: our tool_use_id is fresh (the SDK's
                    # tool_use_id isn't passed through to MCP handlers;
                    # we reconcile via the ToolUseStart event below).
                    ctx = {**req.ctx,
                           "tool_use_id": f"toolu_sdk_{uuid.uuid4().hex[:12]}",
                           "progress_q": _queue.Queue()}
                    result = await tool_executor(tname, args, ctx)
                    # Wrap our dict result in MCP's content shape. R-3.3
                    # will translate halt envelopes here; for now they
                    # flow through to the model unchanged.
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
                    # If a halt was already intercepted on a prior
                    # ToolUseBlock, the model's follow-up retries (more
                    # ToolUseBlocks) and its filler-text explanation of
                    # the denial both belong in the bit-bucket, not in
                    # guide.py's event stream. Interrupt and break so
                    # the session ends promptly.
                    if halt_intercepted is not None:
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
                    # persist + render them — but suppress synthetic
                    # tool_results the SDK fabricates after a hook
                    # denial (those are halt-echoes, not real executor
                    # output). The synthetic block carries the same
                    # tool_use_id that the hook stamped on the halt.
                    if halt_intercepted is not None:
                        continue
                    if isinstance(msg.content, list):
                        for blk in msg.content:
                            if isinstance(blk, ToolResultBlock):
                                yield ToolResult(
                                    tool_use_id=blk.tool_use_id,
                                    # Tool name isn't carried on the
                                    # ToolResultBlock; caller correlates
                                    # by tool_use_id with the prior
                                    # ToolUseStart.
                                    tool_name="",
                                    result=_parse_tool_result_content(blk.content),
                                )
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

        # R-3.3.a: if can_use_tool denied a halt-on-tools call, the
        # session aborted before TurnDone semantics apply. Emit
        # TurnHalt(pending_tool) with the intercepted tool's details
        # instead. The ToolUseStart event was already yielded above
        # (before the SDK consulted can_use_tool), so guide.py has
        # already observed the model's intent; the halt event tells
        # it the dispatch was suppressed.
        if halt_intercepted is not None:
            # If the SDK didn't surface a tool_use_id via the
            # PermissionContext (older SDK builds), synthesize one so
            # downstream consumers have something stable to key on.
            tool_use_id = halt_intercepted.get("tool_use_id") or \
                          f"toolu_halt_{uuid.uuid4().hex[:12]}"
            from core.runtime.llm_runtime import TurnHalt
            yield TurnHalt(reason="pending_tool", detail={
                "tool_name":   halt_intercepted["tool_name"],
                "input":       halt_intercepted["input"],
                "tool_use_id": tool_use_id,
            })
            return
        yield TurnDone(stop_reason=stop_reason, usage=usage_delta)


def _conforms_to_protocol() -> bool:
    """Cheap method-presence check — the protocol module doesn't use
    `runtime_checkable`, so we inline a hasattr check here."""
    return all(hasattr(AgentSDKRuntime, m) for m in ("run_turn",))
