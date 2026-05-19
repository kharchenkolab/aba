"""
LLM provider seam.

`open_stream(history)` returns a context-manager-shaped object that the guide
loop iterates over (yielding content_block_start/_delta/_stop and message_stop
events) and then calls `.get_final_message()` on.

Two implementations:
  - RealStream — wraps anthropic.Anthropic().messages.stream(...)
  - FakeStream — replays scripted assistant turns from a JSONL file
                 (no API calls; tool execution still runs for real)

Fixture format (one JSON object per line, one object = one assistant turn):
  {"blocks": [{"type": "text", "text": "..."},
              {"type": "tool_use", "name": "...", "input": {...}}]}

The fixture's turns are consumed in order across the session. Each call to
open_stream() in fake mode pops the next turn.
"""
from __future__ import annotations
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator, List, Dict, Any

from config import API_KEY, MODEL, SYSTEM_PROMPT, FAKE_SESSION


# ---------- Real provider ----------

class _RealStream:
    """Adapter around anthropic's streaming context manager — same shape we use."""
    def __init__(self, client, history, tools, system: str):
        self._client = client
        self._history = history
        self._tools = tools
        self._system = system
        self._cm = None
        self._stream = None

    def __enter__(self):
        self._cm = self._client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            system=self._system,
            tools=self._tools,
            messages=[{"role": m["role"], "content": m["content"]} for m in self._history],
        )
        self._stream = self._cm.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)

    def __iter__(self):
        return iter(self._stream)

    def get_final_message(self):
        return self._stream.get_final_message()


def _real_factory():
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    def open_stream(history, tools, system: str = SYSTEM_PROMPT):
        return _RealStream(client, history, tools, system)
    return open_stream


# ---------- Fake provider ----------

def _ns(**kw):
    return SimpleNamespace(**kw)


def _chunk(text: str, n: int = 24) -> List[str]:
    return [text[i:i+n] for i in range(0, len(text), n)] or [""]


class _FakeStream:
    """
    Replays one scripted turn as a sequence of Anthropic-shaped events.
    `get_final_message()` returns a SimpleNamespace mimicking anthropic.Message.
    """
    def __init__(self, turn: Dict[str, Any]):
        self._turn = turn
        self._blocks = turn.get("blocks", [])
        # Pre-build the final-message content (used by guide.py for persistence
        # and tool dispatch).
        self._final_content = []
        for b in self._blocks:
            if b["type"] == "text":
                self._final_content.append(_ns(type="text", text=b["text"]))
            elif b["type"] == "tool_use":
                self._final_content.append(_ns(
                    type="tool_use",
                    id=b.get("id") or f"toolu_fake_{uuid.uuid4().hex[:12]}",
                    name=b["name"],
                    input=b.get("input", {}),
                ))
        self._stop_reason = (
            "tool_use"
            if any(b["type"] == "tool_use" for b in self._blocks)
            else "end_turn"
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self) -> Iterator[SimpleNamespace]:
        for block in self._final_content:
            if block.type == "text":
                yield _ns(type="content_block_start",
                          content_block=_ns(type="text"))
                for piece in _chunk(block.text):
                    yield _ns(type="content_block_delta",
                              delta=_ns(type="text_delta", text=piece))
                yield _ns(type="content_block_stop")
            elif block.type == "tool_use":
                yield _ns(type="content_block_start",
                          content_block=_ns(type="tool_use",
                                            id=block.id,
                                            name=block.name))
                yield _ns(type="content_block_delta",
                          delta=_ns(type="input_json_delta",
                                    partial_json=json.dumps(block.input)))
                yield _ns(type="content_block_stop")
        yield _ns(type="message_stop")

    def get_final_message(self):
        return _ns(content=self._final_content, stop_reason=self._stop_reason)


def _fake_factory(path: Path):
    """Load all turns into memory; pop one per open_stream() call."""
    turns: List[Dict[str, Any]] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        turns.append(json.loads(line))
    cursor = {"i": 0}

    def open_stream(history, tools, system: str = ""):  # noqa: ARG001
        i = cursor["i"]
        if i >= len(turns):
            # Stream ran out — emit a polite final turn so the loop terminates.
            turn = {"blocks": [{"type": "text",
                                "text": "[fake session exhausted]"}]}
        else:
            turn = turns[i]
            cursor["i"] += 1
        return _FakeStream(turn)

    return open_stream


# ---------- Factory selection ----------

def make_open_stream():
    """Choose real vs fake based on ABA_FAKE_SESSION."""
    if FAKE_SESSION:
        path = Path(FAKE_SESSION)
        if not path.is_absolute():
            path = Path(__file__).parent.parent / FAKE_SESSION
        return _fake_factory(path)
    return _real_factory()


def is_fake() -> bool:
    return bool(FAKE_SESSION)
