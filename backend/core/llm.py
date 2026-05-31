"""
LLM provider seam.

`open_stream(history)` returns an ASYNC context-manager-shaped object that the
guide loop iterates over (yielding content_block_start/_delta/_stop and
message_stop events) and then awaits `.get_final_message()` on.

Two implementations:
  - RealStream — wraps anthropic.AsyncAnthropic().messages.stream(...). Async
                 so guide.py's `async for event in stream:` doesn't park the
                 event loop on the sync SSE iterator (the bug behind 2026-05-31
                 "Files tab loads forever while agent is thinking"; sync iter
                 blocks the loop between events).
  - FakeStream — replays scripted assistant turns from a JSONL file
                 (no API calls; tool execution still runs for real). Implements
                 the same async interface so guide.py's path is uniform.

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
from typing import AsyncIterator, List, Dict, Any

from core.config import API_KEY, MODEL, FAKE_SESSION


# ---------- Real provider ----------

class _RealStream:
    """Adapter around anthropic's ASYNC streaming context manager — guide.py
    consumes it with `async with` + `async for`, so the event loop stays
    responsive to other HTTP requests while the LLM is generating."""
    def __init__(self, client, history, tools, system: str, model: str):
        self._client = client
        self._history = history
        self._tools = tools
        self._system = system
        self._model = model
        self._cm = None
        self._stream = None

    async def __aenter__(self):
        # Prompt caching: mark the large static prefix (system + tools) and the
        # conversation prefix so repeated turns re-read them cheaply instead of
        # re-charging full input each time. Up to 4 breakpoints; we use 3.
        system = [{"type": "text", "text": self._system,
                   "cache_control": {"type": "ephemeral"}}]
        # Strip internal-only fields (approval_policy etc.) before sending to the
        # Anthropic API — the API rejects unknown keys on tool definitions. The
        # in-process layer (guide.py's per-tool approval gate) reads these fields
        # off TOOL_SCHEMAS, not off the API request, so this is purely API hygiene.
        _INTERNAL_KEYS = {"approval_policy"}
        tools = [{k: v for k, v in t.items() if k not in _INTERNAL_KEYS} for t in (self._tools or [])]
        if tools:
            tools = [*tools[:-1], {**tools[-1], "cache_control": {"type": "ephemeral"}}]
        messages = [{"role": m["role"], "content": m["content"]} for m in self._history]
        # Debug: persist the EXACT, replayable ("callable") request — raw system
        # string, raw tool schemas, and the real messages (valid API format, with
        # tool_results — unlike the distilled turn-context .md). Set ABA_RAW_REQUEST_DIR
        # to enable; one file per API call. Load it and pass straight to
        # client.messages.create(**payload) to replay/modify the real failure prompt.
        import os as _os
        _rawdir = _os.environ.get("ABA_RAW_REQUEST_DIR")
        if _rawdir:
            try:
                import json as _json, time as _time
                _os.makedirs(_rawdir, exist_ok=True)
                _payload = {"model": self._model, "max_tokens": 4096,
                            "system": self._system, "tools": self._tools, "messages": messages}
                with open(_os.path.join(_rawdir, f"req_{int(_time.time()*1000)}.json"), "w") as _f:
                    _json.dump(_payload, _f, default=str)
            except Exception:  # noqa: BLE001 — debug dump must never break a turn
                pass
        if messages and isinstance(messages[-1]["content"], list) and messages[-1]["content"] \
                and isinstance(messages[-1]["content"][-1], dict):
            c = messages[-1]["content"]
            messages[-1] = {**messages[-1],
                            "content": [*c[:-1], {**c[-1], "cache_control": {"type": "ephemeral"}}]}
        self._cm = self._client.messages.stream(
            model=self._model, max_tokens=4096, system=system, tools=tools, messages=messages,
        )
        self._stream = await self._cm.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)

    def __aiter__(self):
        return self._stream.__aiter__()

    async def get_final_message(self):
        return await self._stream.get_final_message()


def _oauth_bearer():
    """Claude Code subscription OAuth bearer, or None. $CLAUDE_CODE_OAUTH_TOKEN else the
    stored CLI credential. Re-read per call so a refreshed token is picked up."""
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok.strip()
    cred = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.exists(cred):
        try:
            oa = json.load(open(cred)).get("claudeAiOauth") or {}
            return (oa.get("accessToken") or "").strip() or None
        except Exception:  # noqa: BLE001
            return None
    return None


def _llm_client():
    """Live-agent Anthropic ASYNC client. Default = project .env API key.
    ABA_LLM_CREDENTIAL=oauth (dev/eval ONLY — never production serving on a personal
    subscription) uses the Claude Code OAuth bearer instead, billing the subscription.
    Resolved per call so a refreshed stored token is picked up; falls back to the
    .env key if no token is available.

    Returns `AsyncAnthropic` (not the sync `Anthropic`) so guide.py's streaming
    loop can iterate events without parking the event loop — fixed 2026-05-31."""
    import anthropic
    if os.environ.get("ABA_LLM_CREDENTIAL", "apikey").lower() == "oauth":
        tok = _oauth_bearer()
        if tok:
            return anthropic.AsyncAnthropic(auth_token=tok)
    return anthropic.AsyncAnthropic(api_key=API_KEY)


def _real_factory():
    mode = os.environ.get("ABA_LLM_CREDENTIAL", "apikey").lower()
    note = (" (OAuth bearer -> subscription)" if _oauth_bearer() else " but NO token -> FALLBACK to .env key") \
        if mode == "oauth" else " (.env API key)"
    print(f"[llm] live-agent credential mode={mode}{note}", flush=True)

    def open_stream(history, tools, system: str = "", model: str | None = None):
        return _RealStream(_llm_client(), history, tools, system, model or MODEL)
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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        return self._events()

    async def _events(self) -> AsyncIterator[SimpleNamespace]:
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

    async def get_final_message(self):
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

    def open_stream(history, tools, system: str = "", model: str | None = None):  # noqa: ARG001
        i = cursor["i"]
        if i >= len(turns):
            # Stream ran out — emit a polite final turn so the loop terminates.
            turn = {"blocks": [{"type": "text",
                                "text": "[fake session exhausted]"}]}
        else:
            turn = turns[i]
            cursor["i"] += 1
        # Test hook: a {"raise": "..."} turn simulates an API failure (the
        # turn is consumed, so a subsequent retry advances to the next turn).
        if isinstance(turn, dict) and "raise" in turn:
            raise RuntimeError(turn["raise"])
        return _FakeStream(turn)

    return open_stream


# ---------- Factory selection ----------

def make_open_stream():
    """Choose real vs fake based on ABA_FAKE_SESSION."""
    if FAKE_SESSION:
        path = Path(FAKE_SESSION)
        if not path.is_absolute():
            # Pre-Pass-A this resolved relative to workspace root from
            # backend/llm.py; now backend/core/llm.py needs one more parent.
            path = Path(__file__).parent.parent.parent / FAKE_SESSION
        return _fake_factory(path)
    return _real_factory()


def is_fake() -> bool:
    return bool(FAKE_SESSION)
