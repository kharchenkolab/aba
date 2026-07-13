"""DirectAPIRuntime — first implementation of LLMRuntime.

Wraps today's `core.llm.make_open_stream` + the inner phase body of
`guide.py:stream_response` (~lines 650-1232). Behaviorally invariant
relative to the pre-refactor guide.py: same retries on transient
errors, same final-msg consumption, same tool dispatch + halt
detection, same usage accounting.

Skeleton phase (W1-A.2 phase 1): class scaffold only. `run_turn` raises
`NotImplementedError` so the audit-gate tests pass + the protocol
conformance is provable, but no caller wires it in yet. Phase 2 lifts
the inner API-retry loop in; phase 3 the final-msg consumption; phase
4 the tool-dispatch loop + halt branches. Each phase verifies via a
backend bounce + a live smoke chat before the next.

The eventual companion to this module (sdk.md Phase R-3) is
`AgentSDKRuntime`, which re-targets the same protocol against the
Claude Agent SDK. Both can coexist; the active runtime is picked per
AgentSpec.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator

from core import config
from core.llm import make_open_stream
from core.runtime.llm_errors import is_transient
from core.runtime.llm_runtime import (
    LLMRuntime,
    RuntimeEvent,
    RuntimeRequest,
    TextDelta,
    ToolExecutor,
    ToolResult,
    ToolUseStart,
    TurnDone,
    TurnHalt,
)

_open_stream = make_open_stream()


# ─── Phase 2 helper ─────────────────────────────────────────────────
# `open_and_consume_stream` is the lifted form of guide.py's former
# 647-695 retry loop. It owns one stream open + the transient-error
# retry policy + accumulates the usage delta. It's an async generator;
# events flow back to the caller in causal order. Phase 3 wraps this
# into the proper run_turn() event-protocol shape; for now it's an
# extraction with private sentinel event types.


@dataclass(frozen=True)
class _RetryNotice:
    """Yielded BEFORE each backoff sleep when a transient API error
    triggers a retry. Caller surfaces it as the existing SSE 'notice'.
    Private — collapses into a public RuntimeEvent in phase 3."""
    attempt: int
    max_retries: int
    backoff_s: int
    error: str


@dataclass(frozen=True)
class _ToolProgress:
    """Phase 4 private — surfaces progress events the tool's synchronous
    body pushes onto ctx['progress_q'] while it runs. The runtime drains
    the queue around its `await fut` and yields one of these per tick;
    guide.py translates them to the existing tool_progress / tool_chunk
    SSE shapes. Stays private until/unless AgentSDKRuntime needs to
    speak the same vocabulary."""
    tool_use_id: str
    payload: dict


@dataclass(frozen=True)
class _StreamCompleted:
    """Sentinel — yielded exactly once, as the LAST event of the
    generator. Signals the retry loop + final-msg consumption ended.

    Carries everything the caller needs for the rest of the turn:
    - final_msg: the SDK's terminal Message (still needed by the
      tool-dispatch loop; phase 4 retires it)
    - usage_delta: per-turn usage accumulated from final_msg.usage
    - assistant_blocks: text + tool_use blocks in Anthropic shape,
      ready to hand to append_message("assistant", ...). Built here
      so guide.py drops its own block-iteration of final_msg.content.
    - stop_reason: 'end_turn' / 'tool_use' / 'max_tokens' / ...
    - tool_calls_this_turn: list[str] of tool names (for logging /
      context-assembly audit; preserves guide.py's prior accounting).
    Private — phase 4 retires this in favor of TurnDone + per-block
    events as the public protocol shape.
    """
    final_msg: Any | None       # None when cancelled mid-stream
    usage_delta: dict           # {input, output, cache_read, cache_write}
    assistant_blocks: list      # ready-to-persist Anthropic-shape blocks
    stop_reason: str | None     # final_msg.stop_reason mirror
    tool_calls_this_turn: list  # [tool_name] in order


async def open_and_consume_stream(
    *,
    history: list[dict],
    tools: list[dict],
    system: str,
    dynamic_system: str,
    model: str,
    cancel_token,
    max_retries: int = 4,
) -> AsyncIterator[Any]:
    """Open the model stream, consume content_block_delta events, retry
    transient errors. Behaviorally invariant to the inline loop in
    guide.py:stream_response (647-695 pre-W1-A.2).

    Yields:
      - TextDelta(text)              for each text chunk
      - _RetryNotice(...)            before each backoff sleep
      - _StreamCompleted(final_msg=, usage_delta=)
                                     exactly once, as the last event

    Cancellation: the cancel token is checked at each event boundary
    inside the stream. If it fires mid-stream, _StreamCompleted lands
    with final_msg=None; caller's outer-loop cancel-check handles the
    cancelled SSE + cleanup (same shape as the original code).

    Note we do NOT raise on cancel — caller controls the cancel path
    via final_msg=None.
    """
    attempt = 0
    while True:
        if cancel_token.cancelled:
            # Stop pressed before (re)opening the stream — crucially this also
            # catches a Stop BETWEEN transient-error retries (overloaded opus),
            # where no stream events fire so the in-stream cancel checks below
            # are never reached. Bail to the cancelled-completion shape instead
            # of looping into another retry.
            yield _StreamCompleted(final_msg=None, usage_delta={}, assistant_blocks=[],
                                   stop_reason=None, tool_calls_this_turn=[])
            return
        emitted = False
        try:
            import time as _time
            _debug_timing = config.settings.debug_timing.get()
            _t_create_begin = _time.perf_counter()
            async with _open_stream(history, tools, system,
                                    model=model,
                                    dynamic_system=dynamic_system) as stream:
                _t_create_done = _time.perf_counter()
                _t_first_event = None
                _n_events = 0
                async for event in stream:
                    if _t_first_event is None:
                        _t_first_event = _time.perf_counter()
                    _n_events += 1
                    if cancel_token.cancelled:
                        break
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            emitted = True
                            yield TextDelta(text=delta.text)
                if cancel_token.cancelled:
                    yield _StreamCompleted(final_msg=None, usage_delta={},
                                           assistant_blocks=[], stop_reason=None,
                                           tool_calls_this_turn=[])
                    return
                final_msg = await stream.get_final_message()
            # Default-on per-call usage line. Answers "is caching working?"
            # from a single grep without a SQLite dive into runs.usage_blob.
            # The verbose [direct-timing] block below still requires
            # ABA_DEBUG_TIMING for ms-level breakdown.
            _u = getattr(final_msg, "usage", None)
            _in = (getattr(_u, "input_tokens", 0) or 0) if _u else 0
            _out = (getattr(_u, "output_tokens", 0) or 0) if _u else 0
            _cr = (getattr(_u, "cache_read_input_tokens", 0) or 0) if _u else 0
            _cw = (getattr(_u, "cache_creation_input_tokens", 0) or 0) if _u else 0
            print(f"[llm-done] model={model} "
                  f"in={_in}t out={_out}t cache_read={_cr}t cache_write={_cw}t",
                  flush=True)
            # Per-call timing breakdown — symmetric to llm_runtime_openai
            # so multi-runtime sessions are equally diagnosable. Gated by
            # ABA_DEBUG_TIMING.
            if _debug_timing:
                _t_stream_done = _time.perf_counter()
                _create_ms = (_t_create_done - _t_create_begin) * 1000
                _ttft_ms = ((_t_first_event or _t_stream_done) - _t_create_done) * 1000
                _gen_ms  = (_t_stream_done - (_t_first_event or _t_create_done)) * 1000
                print(f"[direct-timing] create={_create_ms:.0f}ms "
                      f"TTFT={_ttft_ms:.0f}ms gen={_gen_ms:.0f}ms "
                      f"events={_n_events} in={_in}t out={_out}t "
                      f"cache_read={_cr}t cache_write={_cw}t",
                      flush=True)
            usage_delta: dict = {}
            if getattr(final_msg, "usage", None):
                u = final_msg.usage
                usage_delta = {
                    "input": u.input_tokens or 0,
                    "output": u.output_tokens or 0,
                    "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
                    "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
                }
            # Walk final_msg.content into Anthropic-shape blocks + emit
            # ToolUseStart events. guide.py used to do this inline at
            # the top of its outer loop (the pre-W1-A.2 681-704 region);
            # owning the assembly here makes guide.py 25 LOC lighter
            # and keeps the protocol-side close to its data source.
            assistant_blocks: list[dict] = []
            tool_calls_this_turn: list[str] = []
            stop_reason = getattr(final_msg, "stop_reason", None)
            for block in final_msg.content:
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    inp = block.input if isinstance(block.input, dict) else {}
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": inp,
                    })
                    tool_calls_this_turn.append(block.name)
                    yield ToolUseStart(
                        tool_use_id=block.id,
                        tool_name=block.name,
                        input=inp,
                    )
            yield _StreamCompleted(
                final_msg=final_msg, usage_delta=usage_delta,
                assistant_blocks=assistant_blocks, stop_reason=stop_reason,
                tool_calls_this_turn=tool_calls_this_turn,
            )
            return
        except Exception as e:
            if emitted or attempt >= max_retries or not is_transient(e):
                raise
            attempt += 1
            backoff = min(2 ** attempt, 8)
            yield _RetryNotice(attempt=attempt, max_retries=max_retries,
                               backoff_s=backoff, error=str(e))
            # Cancel-aware backoff: a Stop during 'model busy — retrying' breaks
            # out promptly (the loop-top check then returns the cancelled
            # completion) instead of sleeping the full backoff and re-opening
            # the stream into yet another overloaded retry.
            for _ in range(backoff * 10):
                if cancel_token.cancelled:
                    break
                await asyncio.sleep(0.1)


class DirectAPIRuntime:
    """LLMRuntime backed by core.llm.make_open_stream (the
    Anthropic/OAuth-CC Messages stream we use today).

    No constructor state yet — open_stream is module-level in core.llm.
    A constructor parameter set will land in phase 2 once the inner
    retry loop moves in and we have a clear list of injectables
    (cancellation, max_retries, transient-error predicate, …).
    """

    async def run_turn(
        self,
        req: RuntimeRequest,
        tool_executor: ToolExecutor,
        halt_on_tools: frozenset[str] = frozenset(),
    ) -> AsyncIterator[RuntimeEvent]:
        """Run one model-driven phase. Owns:
          1. model stream + transient-error retries (open_and_consume_stream)
          2. text-delta + tool_use_start emission
          3. tool dispatch via the caller's `tool_executor` async callable
          4. halt detection (model issued a tool in `halt_on_tools` →
             TurnHalt before dispatch; tool returned {deferred: True}
             envelope → TurnHalt after dispatch)
          5. progress passthrough (queue-drain while awaiting the executor)

        Yields:
          - TextDelta, ToolUseStart, ToolResult              public
          - TurnDone (natural end), TurnHalt (halt-on-tool /
            deferred / cancelled)                            public
          - _RetryNotice (transient retry notice),
            _ToolProgress (tool's progress_q events)         private,
            phase 5 may promote

        Cancellation: the cancel token on req is checked at each event
        boundary. Mid-stream cancel ends the model phase early and yields
        TurnHalt(reason='cancelled') without further tool dispatch.

        The caller provides a `tool_executor(name, input, ctx) ->
        Awaitable[dict]`. The executor owns approval gating, background-
        job branching, vision-blocks envelope, and any content-specific
        side effects. ctx will have 'progress_q' (a queue.Queue) and
        'tool_use_id' added by the runtime; the executor's sync body
        pushes onto progress_q while it runs.
        """
        # ── phase 1 — model stream ─────────────────────────────────────
        # open_and_consume_stream is the existing helper from phases 2+3.
        # We forward its events; the _StreamCompleted sentinel terminates
        # this section with the assistant_blocks + stop_reason + usage.
        final_msg = None
        assistant_blocks: list[dict] = []
        stop_reason: str | None = None
        usage_delta: dict = {}
        async for ev in open_and_consume_stream(
            history=req.history,
            tools=req.tools,
            system=req.system.stable,
            dynamic_system=req.system.dynamic,
            model=req.model,
            cancel_token=req.cancel,
        ):
            # Forward public events to caller; capture private sentinel.
            if isinstance(ev, (TextDelta, ToolUseStart)):
                yield ev
            elif isinstance(ev, _RetryNotice):
                yield ev   # private, caller translates to SSE notice
            elif isinstance(ev, _StreamCompleted):
                final_msg = ev.final_msg
                assistant_blocks = ev.assistant_blocks
                stop_reason = ev.stop_reason
                usage_delta = ev.usage_delta
                # Forward to caller: guide.py uses ev.assistant_blocks for
                # append_message("assistant", ...) + ev.tool_calls_this_turn
                # for the context-assembly audit. Phase 5 cleanup may
                # promote these fields into TurnDone's public shape.
                yield ev

        # Cancellation: open_and_consume_stream returns final_msg=None on
        # mid-stream cancel. The Protocol's cancellation event is
        # TurnHalt(reason='cancelled'); guide.py already emits its own
        # cancelled SSE on the outer loop's pre-iteration check, but the
        # runtime is content-neutral and signals via the event.
        if final_msg is None and req.cancel is not None and getattr(req.cancel, "cancelled", False):
            yield TurnHalt(reason='cancelled', detail={})
            return

        # No tool_use → natural end-of-turn. TurnDone carries stop_reason
        # + usage so guide.py can record telemetry without inspecting
        # any model-side object.
        if stop_reason != "tool_use":
            yield TurnDone(stop_reason=stop_reason or "end_turn", usage=usage_delta)
            return

        # ── phase 2 — tool dispatch ────────────────────────────────────
        # We walk the tool_use blocks from assistant_blocks (already
        # validated by phase 3 of W1-A.2). For each:
        #   - if name ∈ halt_on_tools: yield TurnHalt(pending_tool)
        #     BEFORE dispatch. Caller decides what to do.
        #   - else: invoke tool_executor, yielding _ToolProgress while
        #     it runs. On completion, inspect result for {deferred:true}
        #     envelope → TurnHalt(deferred). Otherwise yield ToolResult.
        import queue as _queue
        loop = asyncio.get_event_loop()
        for block in assistant_blocks:
            if block.get("type") != "tool_use":
                continue
            tool_name = block["name"]
            tool_input = block["input"]
            tool_use_id = block["id"]

            if tool_name in halt_on_tools:
                # The model wanted to invoke an intercepted tool — caller
                # handles it (present_plan / ask_clarification today; future
                # halt-on-tool sets are similar). No dispatch.
                yield TurnHalt(reason='pending_tool', detail={
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "input": tool_input,
                })
                return

            # Progress queue: the sync executor body pushes ticks/chunks
            # onto it; we drain while awaiting the future and surface
            # each one as a _ToolProgress event. The pattern matches the
            # pre-refactor inline loop in guide.py (lines 998-1061).
            progress_q: _queue.Queue = _queue.Queue()
            exec_ctx = {**req.ctx,
                        "progress_q": progress_q,
                        "tool_use_id": tool_use_id}
            # The executor is async; for sync bio dispatch the closure
            # in guide.py wraps run_in_executor itself. Either way we
            # await one task and yield progress events around it.
            exec_task = loop.create_task(tool_executor(tool_name, tool_input, exec_ctx))
            while not exec_task.done():
                drained = []
                try:
                    while True:
                        drained.append(progress_q.get_nowait())
                except _queue.Empty:
                    pass
                for prog in drained:
                    yield _ToolProgress(tool_use_id=tool_use_id, payload=prog)
                    await asyncio.sleep(0)
                if not drained:
                    await asyncio.sleep(0.2)
            # Tail flush — anything pushed between the last drain + task
            # completion must reach the caller before the result event.
            try:
                while True:
                    yield _ToolProgress(tool_use_id=tool_use_id,
                                        payload=progress_q.get_nowait())
                    await asyncio.sleep(0)
            except _queue.Empty:
                pass
            result_obj = await exec_task

            # Halt envelopes carried by the executor's return value. Three
            # shapes the runtime recognizes; everything else is a normal
            # ToolResult:
            #   {deferred: True, deferred_id, timeout_s?}
            #       → TurnHalt(deferred) — no ToolResult yielded. The
            #         deferred-tool wait + webhook fills the real result
            #         in caller-side state, NOT in history-as-tool_result.
            #   {_runtime_halt_before: "<reason>", ...}
            #       → TurnHalt(<reason>) — no ToolResult yielded. The
            #         tool's tool_use stays unresolved; caller's resume
            #         endpoint writes the real result later. Used by
            #         approval (the held tool runs only after user OK).
            #   {_runtime_halt_after: "<reason>", ...}
            #       → ToolResult + TurnHalt(<reason>). The ack stays in
            #         history (well-formed tool_use/tool_result pair).
            #         Used by present_plan + ask_clarification.
            # The `detail` on the emitted TurnHalt carries the whole
            # result envelope minus the marker key, so guide.py can pull
            # out validator outputs (concerns, plan_entity_id) etc.
            if isinstance(result_obj, dict):
                if result_obj.get("deferred"):
                    yield TurnHalt(reason='deferred', detail={
                        "tool_use_id": tool_use_id,
                        "tool_name": tool_name,
                        "deferred_id": result_obj.get("deferred_id"),
                        "timeout_s": result_obj.get("timeout_s"),
                    })
                    return
                if "_runtime_halt_before" in result_obj:
                    reason = result_obj.pop("_runtime_halt_before")
                    yield TurnHalt(reason=reason, detail={
                        **result_obj,
                        "tool_use_id": tool_use_id,
                        "tool_name": tool_name,
                    })
                    return
                halt_after = result_obj.pop("_runtime_halt_after", None)
            else:
                halt_after = None

            yield ToolResult(tool_use_id=tool_use_id, tool_name=tool_name,
                             result=result_obj)

            if halt_after:
                yield TurnHalt(reason=halt_after, detail={
                    **(result_obj if isinstance(result_obj, dict) else {}),
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                })
                return

        yield TurnDone(stop_reason=stop_reason, usage=usage_delta)


def _conforms_to_protocol() -> bool:
    """Cheap runtime protocol check — used by the unit test. Structural
    typing in Protocol means an isinstance() check needs
    @runtime_checkable on the Protocol; we don't add that to the
    domain-neutral protocol module, so we inline a method-presence
    check here. Keeps the protocol definition clean."""
    required = {"run_turn"}
    return all(hasattr(DirectAPIRuntime, m) for m in required)
