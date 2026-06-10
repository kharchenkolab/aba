"""LLMRuntime — protocol for one model-driven phase of an agent turn.

A "phase" is the work between two halt-or-end transitions: zero or more
text deltas, zero or more tool calls (executed by the caller-provided
ToolExecutor), then either an end-of-turn (TurnDone) or a halt
(TurnHalt) when the model issues a tool that the orchestrator wants to
intercept (present_plan, ask_clarification) or a deferred-result tool.

Design (sdk.md §"The protocol"):
- The runtime owns ONE phase. The orchestrator (guide.py) loops over
  phases, decides what each halt means, drives the state machine, and
  re-enters with the next RuntimeRequest.
- Tool execution is delegated to the caller via ToolExecutor — the
  runtime calls it for every tool_use; the caller's closure runs hooks
  + ctx propagation + the actual dispatch. The runtime stays
  domain-neutral.
- Events are PRIMITIVE — TextDelta / ToolUseStart / ToolResult /
  TurnDone / TurnHalt. The orchestrator translates these into the
  custom SSE vocabulary (plan, manifest, deferred_tool_pending, …)
  the frontend expects. Translation lives OUTSIDE this module.

Two implementations target this protocol:
- DirectAPIRuntime (this commit + the next one): wraps today's
  core.llm.make_open_stream + the inner body of guide.py:stream_response.
  Behaviorally invariant — the rest of the system can't tell the
  difference.
- AgentSDKRuntime (sdk.md Phase R-3, deferred): wraps the Claude Agent
  SDK once the open questions (cache layout, in-process MCP transport,
  history seeding for resumed conversations) are answered.

The abstraction lets us flip per-AgentSpec via a `runtime` field — no
fork in the codebase.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol


# ─── inputs ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SystemSpec:
    """The two-block system prompt build_system() returns: a stable
    cached prefix + a dynamic uncached tail (BM25 catalog of recipes).

    Runtimes are free to compose them differently. DirectAPIRuntime
    preserves the split (3-block: CC marker, stable cached, dynamic
    uncached) for the existing cache hit-rate. AgentSDKRuntime would
    relocate the dynamic tail (sdk.md §"Cache control under the SDK"
    Option A).
    """
    stable: str
    dynamic: str = ""


@dataclass(frozen=True)
class RuntimeRequest:
    """Everything one phase needs. Caller assembles this; runtime
    consumes it without mutation."""
    history: list[dict]              # canonical Anthropic-shape messages
    tools: list[dict]                # active tool schemas (Anthropic shape)
    system: SystemSpec
    model: str
    max_tokens: int
    ctx: dict                        # per-turn ctx (thread_id, run_id, ...)
    # cancel: an opaque token the runtime polls; honor a set() value as
    # cooperative cancellation. Typed `Any` so we don't drag the existing
    # CancelToken class into this module's surface; concrete runtimes
    # know how to read it. Today: core.runtime.cancellation.CancelToken.
    cancel: Any = None
    # Eval-only escape hatch: when True, the runtime MUST send `history`
    # verbatim and skip any internal compaction / cache-placement
    # heuristics. Used by the prompt-regression harness which seeds the
    # message list with prior tool_use/tool_result pairs from JSONL
    # fixtures. AgentSDKRuntime rejects seeded_history=True (raise);
    # DirectAPIRuntime honors it.
    seeded_history: bool = False


# ─── outputs (primitive events) ──────────────────────────────────────


class RuntimeEvent:
    """Marker base — runtime emits subclasses below."""


@dataclass(frozen=True)
class TextDelta(RuntimeEvent):
    """A chunk of assistant text. The orchestrator concatenates them
    into the user-visible streamed response."""
    text: str


@dataclass(frozen=True)
class ToolUseStart(RuntimeEvent):
    """The model issued a tool_use block. Emitted BEFORE the runtime
    dispatches via ToolExecutor — the orchestrator can use this for
    UI hints (chip rendering, etc.) before the result lands."""
    tool_use_id: str
    tool_name: str
    input: dict


@dataclass(frozen=True)
class ToolResult(RuntimeEvent):
    """The ToolExecutor returned (or raised — runtime maps errors to a
    `{is_error: True, ...}` result). Carries the same dict shape the
    Anthropic API expects in the next tool_result message."""
    tool_use_id: str
    tool_name: str
    result: dict


@dataclass(frozen=True)
class TurnDone(RuntimeEvent):
    """End-of-turn: stop_reason ∈ {'end_turn', 'max_tokens', ...}.
    Carries the API's usage block ({input, output, cache_read,
    cache_write}) so the orchestrator can record + observability can
    surface it."""
    stop_reason: str
    usage: dict


@dataclass(frozen=True)
class TurnHalt(RuntimeEvent):
    """The runtime stopped the phase short of normal end_turn:
    - reason='pending_tool': the model issued a tool in halt_on_tools
      (the orchestrator wanted to intercept it — present_plan,
      ask_clarification, etc.). detail={'tool_use_id', 'tool_name',
      'input'}.
    - reason='deferred': the ToolExecutor returned a result with the
      `{deferred: True}` marker (background job). detail={'tool_use_id',
      'job_id'}.
    - reason='cancelled': the cancel token fired mid-phase.
    - reason='error': unrecoverable error (auth, model 4xx, etc.).
      detail={'message', 'type'}.

    The orchestrator inspects `reason` + `detail` and decides what to
    do next (which state to transition to, which custom SSE event to
    emit, whether/when to call run_turn again).
    """
    reason: str
    detail: dict


# ─── the tool executor ──────────────────────────────────────────────


# (tool_name, tool_input, ctx) → tool_result_dict
# The runtime calls this for every tool_use block it sees. The result
# dict goes into the message history as a tool_result block. Today's
# `_dispatch_tool` (which runs PreToolUse/PostToolUse hooks, threads
# ctx via aba_ctx_id, and routes to the in-process MCP server or the
# bare bio EXECUTORS dict) IS this signature — the wiring is a closure
# guide.py builds at the top of stream_response.
ToolExecutor = Callable[[str, dict, dict], Awaitable[dict]]


# ─── the interface ──────────────────────────────────────────────────


class LLMRuntime(Protocol):
    async def run_turn(
        self,
        req: RuntimeRequest,
        tool_executor: ToolExecutor,
        halt_on_tools: frozenset[str] = frozenset(),
    ) -> AsyncIterator[RuntimeEvent]:
        """Run one model-driven phase. Async-iterates primitive events.

        Stops when one of:
        (a) model issues a tool in `halt_on_tools` — yields TurnHalt
            with reason='pending_tool' BEFORE dispatching the tool
            (the orchestrator wants to intercept it);
        (b) model issues any tool whose ToolExecutor result has
            `{deferred: True}` — yields TurnHalt reason='deferred';
        (c) model emits end_turn / max_tokens — yields TurnDone;
        (d) cancel token fires — yields TurnHalt reason='cancelled';
        (e) unrecoverable error — yields TurnHalt reason='error'.

        Implementations MUST yield events in causal order and MUST emit
        exactly one terminal event (TurnDone or TurnHalt) before
        returning.
        """
        # noqa: D401 — Protocol method, no implementation here
        ...
