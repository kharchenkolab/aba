"""FakeRuntime — scripted-replay implementation of LLMRuntime.

Replaces today's ABA_FAKE_SESSION path (`core.llm.make_open_stream` →
`_fake_factory`) with a Protocol-conformant runtime so the eval harness +
unit tests share the same `LLMRuntime` surface as production.

Each call to `run_turn()` pops the next scripted turn from a JSONL file
and replays it as RuntimeEvents:

  - text blocks → TextDelta events (chunked for streaming fidelity)
  - tool_use blocks → ToolUseStart events; the tool_executor is invoked;
    its result becomes a ToolResult event (or TurnHalt for deferred /
    halt-envelope returns) — exactly the same contract DirectAPIRuntime
    uses with the live API.
  - end of blocks → TurnDone

A {"raise": "..."} turn simulates an API failure (the cursor advances,
so a subsequent retry picks up the next turn). Compatible with the
existing `_fake_factory` semantics in core.llm.

The scripted-turn JSONL shape is identical to what FakeStream consumes:

    {"blocks": [{"type": "text", "text": "hi"}]}
    {"blocks": [{"type": "tool_use", "name": "echo", "input": {"x": 1}},
                {"type": "text", "text": "done"}]}
    {"raise": "simulated 529 overloaded"}

Cursor management: at module-import time we lazy-load the JSONL configured
via `ABA_FAKE_SESSION` (relative to project root if not absolute). The
cursor is shared across all FakeRuntime instances in the same process —
matches today's `_fake_factory` behavior.
"""
from __future__ import annotations

import json
import os
import queue as _queue
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from core.runtime.llm_runtime import (
    RuntimeEvent,
    RuntimeRequest,
    TextDelta,
    ToolExecutor,
    ToolResult,
    ToolUseStart,
    TurnDone,
    TurnHalt,
)


_ROOT = Path(__file__).resolve().parents[2]   # workspace root (above backend/)


def _load_turns(path: Path) -> list[dict]:
    out: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        out.append(json.loads(line))
    return out


class _Cursor:
    """Shared per-process turn cursor across all FakeRuntime instances —
    mirrors `_fake_factory`'s `cursor` closure. Lazily reads ABA_FAKE_SESSION
    on first use so the env var can be set anywhere before the first
    run_turn() call."""
    _turns: list[dict] | None = None
    _i: int = 0
    _path: Path | None = None

    @classmethod
    def _ensure_loaded(cls) -> None:
        if cls._turns is not None:
            return
        env = os.environ.get("ABA_FAKE_SESSION")
        if not env:
            raise RuntimeError(
                "FakeRuntime needs ABA_FAKE_SESSION pointing at a JSONL of "
                "scripted turns. Set it before instantiating."
            )
        p = Path(env)
        if not p.is_absolute():
            p = _ROOT / env
        cls._turns = _load_turns(p)
        cls._path = p
        cls._i = 0

    @classmethod
    def next_turn(cls) -> dict:
        cls._ensure_loaded()
        assert cls._turns is not None   # _ensure_loaded sets it
        if cls._i >= len(cls._turns):
            # Exhausted — emit a polite terminator so the outer loop ends.
            return {"blocks": [{"type": "text",
                                "text": "[fake session exhausted]"}]}
        t = cls._turns[cls._i]
        cls._i += 1
        return t

    @classmethod
    def reset_for_testing(cls, turns: list[dict] | None = None) -> None:
        """Tests can inject turns directly + reset the cursor."""
        cls._turns = turns
        cls._i = 0
        cls._path = None


class FakeRuntime:
    """LLMRuntime that replays scripted turns from a JSONL file.

    No retry logic, no transient-error handling, no progress queue
    plumbing — those concerns belong to the live runtime. The fake stays
    deterministic so tests can assert exact event sequences.
    """

    async def run_turn(
        self,
        req: RuntimeRequest,   # noqa: ARG002 — unused; replay ignores history
        tool_executor: ToolExecutor,
        halt_on_tools: frozenset[str] = frozenset(),
    ) -> AsyncIterator[RuntimeEvent]:
        turn = _Cursor.next_turn()
        # Failure-injection turn: simulate an API exception so retry paths
        # in the orchestrator can be exercised without hitting the network.
        if isinstance(turn, dict) and "raise" in turn:
            raise RuntimeError(turn["raise"])
        blocks = turn.get("blocks", [])

        # ── stream phase: text + tool_use_start events ──
        for b in blocks:
            t = b.get("type")
            if t == "text":
                # Chunk the text into ~40-char pieces so consumers that
                # exercise multi-delta paths see realistic deltas.
                text = b.get("text", "")
                for i in range(0, len(text), 40):
                    yield TextDelta(text=text[i:i + 40])
            elif t == "tool_use":
                tool_use_id = b.get("id") or f"toolu_fake_{uuid.uuid4().hex[:12]}"
                yield ToolUseStart(
                    tool_use_id=tool_use_id,
                    tool_name=b["name"],
                    input=b.get("input", {}),
                )

        # ── tool dispatch phase ──
        # Walk the tool_use blocks; same halt-envelope contract as
        # DirectAPIRuntime: halt_on_tools → TurnHalt(pending_tool);
        # {_runtime_halt_before: <reason>} → TurnHalt(reason);
        # {deferred: True} → TurnHalt('deferred');
        # {_runtime_halt_after: <reason>} → ToolResult + TurnHalt.
        for b in blocks:
            if b.get("type") != "tool_use":
                continue
            tool_use_id = b.get("id") or f"toolu_fake_{uuid.uuid4().hex[:12]}"
            tool_name = b["name"]
            tool_input = b.get("input", {})

            if tool_name in halt_on_tools:
                yield TurnHalt(reason="pending_tool", detail={
                    "tool_use_id": tool_use_id,
                    "tool_name": tool_name,
                    "input": tool_input,
                })
                return

            # The executor sees the same ctx shape it'd see in production —
            # progress_q + tool_use_id added by the runtime, the rest from
            # req.ctx. The fake doesn't drain progress (no concurrent work)
            # but supplies a queue so executors that consult it don't crash.
            exec_ctx = {**req.ctx,
                        "progress_q": _queue.Queue(),
                        "tool_use_id": tool_use_id}
            result_obj = await tool_executor(tool_name, tool_input, exec_ctx)

            if isinstance(result_obj, dict):
                if result_obj.get("deferred"):
                    yield TurnHalt(reason="deferred", detail={
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

        # ── normal end-of-turn ──
        stop_reason = "tool_use" if any(b.get("type") == "tool_use"
                                         for b in blocks) else "end_turn"
        # FakeRuntime doesn't track usage tokens; emit zero-filled dict so
        # callers can mirror their production accounting.
        yield TurnDone(stop_reason=stop_reason, usage={
            "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
        })
