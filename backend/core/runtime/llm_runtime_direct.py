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

from core.llm import make_open_stream
from core.runtime.llm_errors import is_transient
from core.runtime.llm_runtime import (
    LLMRuntime,
    RuntimeEvent,
    RuntimeRequest,
    TextDelta,
    ToolExecutor,
    ToolUseStart,
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
        emitted = False
        try:
            async with _open_stream(history, tools, system,
                                    model=model,
                                    dynamic_system=dynamic_system) as stream:
                async for event in stream:
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
            await asyncio.sleep(backoff)


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
        """Skeleton: raises NotImplementedError. Phases 2-4 fill this in
        incrementally. The signature matches LLMRuntime.run_turn exactly
        so structural-protocol checks pass today."""
        raise NotImplementedError(
            "DirectAPIRuntime.run_turn is a skeleton (W1-A.2 phase 1). "
            "Phase 2 moves the inner streaming retry loop here; phase 3 "
            "the final-msg consumption; phase 4 the tool-dispatch loop. "
            "Until then, guide.py owns the loop body directly."
        )
        # Unreachable, but keeps the type checker happy that this is an
        # async generator.
        yield  # type: ignore[unreachable]


def _conforms_to_protocol() -> bool:
    """Cheap runtime protocol check — used by the unit test. Structural
    typing in Protocol means an isinstance() check needs
    @runtime_checkable on the Protocol; we don't add that to the
    domain-neutral protocol module, so we inline a method-presence
    check here. Keeps the protocol definition clean."""
    required = {"run_turn"}
    return all(hasattr(DirectAPIRuntime, m) for m in required)
