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

from typing import AsyncIterator

from core.runtime.llm_runtime import (
    LLMRuntime,
    RuntimeEvent,
    RuntimeRequest,
    ToolExecutor,
)


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
