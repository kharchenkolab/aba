"""Turn dataclass — explicit per-turn state for the agent loop.

Pass E (arch3_plan.md): replaces the implicit `while True` state inside
guide.stream_response with a typed Turn object that's persisted on every
transition. Once Turn rows are reliable, `_repair_tool_pairs` becomes
unnecessary (a dangling tool_use is impossible because the Turn row
reflects truth, not message history).

For Pass E we add the data shape and a checkpoint helper. The full
state-machine extraction (driving loop bodies off TurnState) lands in
Pass F when Agent(spec) takes over.
"""
from __future__ import annotations
import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


class TurnState(str, enum.Enum):
    GENERATING       = "generating"        # streaming an LLM response
    EXECUTING_TOOLS  = "executing_tools"   # dispatching tool_use blocks
    AWAITING_USER    = "awaiting_user"     # halted on plan/clarification/approval
    SUMMARIZING      = "summarizing"       # running on_stop hooks
    DONE             = "done"
    FAILED           = "failed"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def gen_run_id() -> str:
    return f"run_{uuid.uuid4().hex[:10]}"


@dataclass
class Turn:
    """One agent turn. Persisted to the `runs` table on every state
    transition; load_turn() rehydrates from the row."""
    run_id:           str
    session_id:       str
    turn_index:       int
    agent_spec_name:  str                          # "guide" | "skeptic" | ...
    state:            TurnState
    focus_entity_id:  Optional[str] = None
    thread_id:        Optional[str] = None
    pending_tool_calls: list[dict] = field(default_factory=list)
    pending_user_signal: Optional[str] = None      # plan | clarify | approval
    final_message:    Optional[dict] = None
    error:            Optional[dict] = None
    usage_in:         int = 0
    usage_out:        int = 0
    usage_cache_read: int = 0
    usage_cache_write: int = 0
    started_at:       str = field(default_factory=_utcnow)
    updated_at:       str = field(default_factory=_utcnow)

    def transition(self, new_state: TurnState) -> None:
        self.state = new_state
        self.updated_at = _utcnow()

    def to_row(self) -> dict:
        import json
        return {
            "run_id":          self.run_id,
            "session_id":      self.session_id,
            "turn_index":      self.turn_index,
            "agent_spec_name": self.agent_spec_name,
            "state":           self.state.value,
            "focus_entity_id": self.focus_entity_id,
            "thread_id":       self.thread_id,
            "pending_blob":    json.dumps({
                "pending_tool_calls": self.pending_tool_calls,
                "pending_user_signal": self.pending_user_signal,
                "final_message": self.final_message,
            }),
            "error_blob":      json.dumps(self.error) if self.error else None,
            "usage_blob":      json.dumps({
                "input": self.usage_in, "output": self.usage_out,
                "cache_read": self.usage_cache_read,
                "cache_write": self.usage_cache_write,
            }),
            "started_at":      self.started_at,
            "updated_at":      self.updated_at,
        }

    @classmethod
    def from_row(cls, row: Any) -> "Turn":
        import json
        pend = json.loads(row["pending_blob"]) if row["pending_blob"] else {}
        usage = json.loads(row["usage_blob"]) if row["usage_blob"] else {}
        return cls(
            run_id=row["run_id"],
            session_id=row["session_id"],
            turn_index=row["turn_index"],
            agent_spec_name=row["agent_spec_name"],
            state=TurnState(row["state"]),
            focus_entity_id=row["focus_entity_id"],
            thread_id=row["thread_id"],
            pending_tool_calls=pend.get("pending_tool_calls") or [],
            pending_user_signal=pend.get("pending_user_signal"),
            final_message=pend.get("final_message"),
            error=json.loads(row["error_blob"]) if row["error_blob"] else None,
            usage_in=usage.get("input", 0),
            usage_out=usage.get("output", 0),
            usage_cache_read=usage.get("cache_read", 0),
            usage_cache_write=usage.get("cache_write", 0),
            started_at=row["started_at"],
            updated_at=row["updated_at"],
        )
