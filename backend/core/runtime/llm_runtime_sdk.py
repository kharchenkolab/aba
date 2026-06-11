"""AgentSDKRuntime — LLMRuntime backed by `claude-agent-sdk`.

Alternative implementation to DirectAPIRuntime that delegates the
model-driven phase to Anthropic's Claude Agent SDK
(`claude_agent_sdk.ClaudeSDKClient`). Same protocol — pick-by-spec via
`AgentSpec.runtime = 'sdk'` or `ABA_RUNTIME_OVERRIDE=sdk`.

Why it earns its place alongside DirectAPIRuntime:
- The SDK absorbs retry / cache placement / per-session compaction
  that we hand-roll in core/llm.py + open_and_consume_stream.
- The SDK speaks MCP natively; bio's aba_core MCP server runs
  in-process via `create_sdk_mcp_server` (memory transport, no
  subprocess).
- The SDK ships its own CC marker for oauth_cc credentials; verified
  to work with Haiku/Sonnet/Opus during 2026-06-11 spike #3.

What stays in ABA's outer harness regardless of runtime:
- guide.py's outer multi-turn loop + halt-state machine.
- The TurnSink / scribe / focus / manifest / on_stop hook layer.
- The eval-harness history-seeding path — DirectAPIRuntime owns that.

Phase plan:
  R-3.1 (this file) — skeleton; run_turn raises NotImplementedError.
                       Protocol-purity test passes; importing this module
                       has no side effect on the runtime selector path.
  R-3.2             — implement run_turn: SDK session lifecycle + event
                       translation + tool_executor adapter.
  R-3.3             — hook adapters (PreToolUse / PostToolUse /
                       can_use_tool) wiring core/runtime/hooks.
  R-3.4             — catalog-as-first-user-message (Option A from
                       spike #4, +0.4% cache cost, well within noise).
  R-3.5             — flip Methodologist + Critic onto runtime: sdk.

The lazy-import of `claude_agent_sdk` keeps this module cheap to import
on the direct/fake paths (where it's never needed). The SDK pulls in
~75 MB and a ton of transitive deps; we only pay that when sdk runtime
is actually selected.
"""
from __future__ import annotations

from typing import AsyncIterator

from core.runtime.llm_runtime import (
    LLMRuntime,
    RuntimeEvent,
    RuntimeRequest,
    ToolExecutor,
)


class AgentSDKRuntime:
    """LLMRuntime backed by claude_agent_sdk.ClaudeSDKClient.

    Skeleton — run_turn raises NotImplementedError until R-3.2 lands the
    real session lifecycle + event translation. The class exists today
    so make_runtime(spec) can return AgentSDKRuntime instances and the
    protocol-purity test can assert structural conformance.
    """

    async def run_turn(
        self,
        req: RuntimeRequest,
        tool_executor: ToolExecutor,
        halt_on_tools: frozenset[str] = frozenset(),
    ) -> AsyncIterator[RuntimeEvent]:
        """Skeleton: raises NotImplementedError. R-3.2 lands the real
        implementation — ClaudeSDKClient session, in-process MCP via
        create_sdk_mcp_server, event-stream translation to
        RuntimeEvents, halt-envelope handling matching DirectAPIRuntime."""
        raise NotImplementedError(
            "AgentSDKRuntime.run_turn is a skeleton (W1-A.2 / R-3.1). "
            "R-3.2 implements ClaudeSDKClient session + event translation."
        )
        # Unreachable; keeps the type checker happy that this is an
        # async generator.
        yield   # type: ignore[unreachable]


def _conforms_to_protocol() -> bool:
    """Cheap method-presence check — the protocol module doesn't use
    `runtime_checkable`, so we inline a hasattr check here. Same shape
    as llm_runtime_direct._conforms_to_protocol."""
    return all(hasattr(AgentSDKRuntime, m) for m in ("run_turn",))
