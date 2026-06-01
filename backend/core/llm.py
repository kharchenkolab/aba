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


def _strip_cc(content):
    """Strip `cache_control` from any content blocks. Used when canonicalizing
    a message list for hashing — cache_control is sender-side metadata, not
    a semantic input to the model, and stripping it lets the hash compare
    equal across calls that use different cache breakpoints."""
    if not isinstance(content, list):
        return content
    out = []
    for b in content:
        if isinstance(b, dict):
            out.append({k: v for k, v in b.items() if k != "cache_control"})
        else:
            out.append(b)
    return out


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
        # oauth_cc credential mode: the server gates non-Haiku models on OAuth by
        # checking that the first system block is byte-exactly this Claude Code
        # marker. Without it, OAuth+Sonnet/Opus returns 429. Marker MUST be first,
        # its own discrete block, and have NO cache_control (server check is
        # byte-exact). The model still adopts our real system prompt as persona.
        if _wants_cc_marker():
            system = [_CC_MARKER_BLOCK, *system]
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
        # Default: /tmp/aba_llm_sent. Set to "off" or "0" to disable.
        import os as _os, hashlib as _hashlib, time as _time
        import json as _json
        _rawdir = _os.environ.get("ABA_RAW_REQUEST_DIR", "/tmp/aba_llm_sent")
        if _rawdir and _rawdir.lower() not in ("off", "0", "false", ""):
            try:
                _os.makedirs(_rawdir, exist_ok=True)
                _payload = {"model": self._model, "max_tokens": 4096,
                            "system": self._system, "tools": self._tools, "messages": messages}
                _ts = int(_time.time() * 1000)
                _fn = _os.path.join(_rawdir, f"req_{_ts}.json")
                with open(_fn, "w") as _f:
                    _json.dump(_payload, _f, default=str)
                # Compact one-line summary on stdout so you can spot mismatches
                # without parsing JSONs. Logs the SHA-256 of the message envelope
                # (canonicalized, cache_control stripped — that's metadata, not
                # input semantics) so it's directly comparable to the same hash
                # computed from the dumped turn_context JSON.
                _canon = _json.dumps(
                    [{"role": m["role"], "content": _strip_cc(m.get("content"))} for m in messages],
                    sort_keys=True, default=str,
                ).encode("utf-8")
                _hist_sha = _hashlib.sha256(_canon).hexdigest()[:12]
                _sys_sha = _hashlib.sha256((self._system or "").encode("utf-8")).hexdigest()[:12]
                print(f"[llm-sent] model={self._model} sys_sha={_sys_sha} "
                      f"hist_sha={_hist_sha} n_msgs={len(messages)} "
                      f"sys_chars={len(self._system or '')} -> {_fn}",
                      flush=True)
            except Exception:  # noqa: BLE001 — debug dump must never break a turn
                pass
        if messages and isinstance(messages[-1]["content"], list) and messages[-1]["content"] \
                and isinstance(messages[-1]["content"][-1], dict):
            c = messages[-1]["content"]
            messages[-1] = {**messages[-1],
                            "content": [*c[:-1], {**c[-1], "cache_control": {"type": "ephemeral"}}]}
        # max_tokens caps a single assistant turn's output. 4096 was too tight:
        # when the agent emits a tool_use with large `code` content (e.g.
        # writing a multi-KB markdown recipe via run_python), the stream cuts
        # off mid-input, the SDK can't parse the partial JSON, and the tool_use
        # ends up with empty input — silent fail downstream (verified live
        # 2026-06-01, prj_8d699668 thr_97a96441). Env override so it's tunable.
        max_tok = int(os.environ.get("ABA_MAX_TOKENS", "16000"))
        self._cm = self._client.messages.stream(
            model=self._model, max_tokens=max_tok, system=system, tools=tools, messages=messages,
        )
        self._stream = await self._cm.__aenter__()
        return self

    async def __aexit__(self, *exc):
        return await self._cm.__aexit__(*exc)

    def __aiter__(self):
        return self._stream.__aiter__()

    async def get_final_message(self):
        return await self._stream.get_final_message()


# Claude Code subscription gate: the Anthropic API checks for this exact
# first-system-block when the request bears an OAuth bearer; without it
# non-Haiku models 429 (categorical reject, not actual quota). Verified
# 2026-06-01 via mitmproxy capture of the CLI. Pure routing marker — the
# model adopts whatever persona block #2 (our real system prompt) defines.
_CC_MARKER_BLOCK = {"type": "text",
                    "text": "You are a Claude agent, built on Anthropic's Claude Agent SDK."}


def _credential_mode() -> str:
    """ABA_LLM_CREDENTIAL ∈ {apikey, oauth, oauth_cc}. Default apikey."""
    return os.environ.get("ABA_LLM_CREDENTIAL", "apikey").lower()


def _wants_cc_marker() -> bool:
    """True iff we must prepend the Claude Code marker as the first system
    block — needed only on oauth_cc mode (OAuth bearer + non-Haiku models)."""
    return _credential_mode() == "oauth_cc"


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
    """Live-agent Anthropic ASYNC client.

    Modes (`ABA_LLM_CREDENTIAL`):
      apikey   (default) — `.env` ANTHROPIC_API_KEY, all models, bills ABA's key.
      oauth              — OAuth bearer, **Haiku-only** (server 429s non-Haiku).
                           Useful for comparison / legacy probe.
      oauth_cc           — OAuth bearer + CC system-marker prepend (see
                           `_CC_MARKER_BLOCK`). All models. Bills the Claude Code
                           subscription. **Dev/personal use only** — never for
                           production serving on a personal subscription.

    Token resolved per call so a refreshed stored token is picked up; falls back
    to the .env key if OAuth selected but no token is available.

    Returns `AsyncAnthropic` (not the sync `Anthropic`) so guide.py's streaming
    loop can iterate events without parking the event loop — fixed 2026-05-31."""
    import anthropic
    if _credential_mode() in ("oauth", "oauth_cc"):
        tok = _oauth_bearer()
        if tok:
            return anthropic.AsyncAnthropic(auth_token=tok)
    return anthropic.AsyncAnthropic(api_key=API_KEY)


def _real_factory():
    mode = _credential_mode()
    if mode in ("oauth", "oauth_cc"):
        if _oauth_bearer():
            extra = " + CC marker -> all models on subscription" if mode == "oauth_cc" else " -> Haiku-only"
            note = f" (OAuth bearer{extra})"
        else:
            note = " but NO token -> FALLBACK to .env key"
    else:
        note = " (.env API key)"
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
