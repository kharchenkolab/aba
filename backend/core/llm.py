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
import time
import threading
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
    def __init__(self, client, history, tools, system: str, model: str,
                 dynamic_system: str = ""):
        self._client = client
        self._history = history
        self._tools = tools
        self._system = system
        # CC-convergence Phase 4 (cache split): per-turn-dynamic system tail
        # (BM25 recipes catalog). Sent as its own block AFTER the cached prefix
        # so per-intent catalog changes don't bust the system-prefix cache.
        self._dynamic_system = dynamic_system or ""
        self._model = model
        self._cm = None
        self._stream = None

    async def __aenter__(self):
        # Prompt caching: stable prefix (cache_control) + dynamic tail (no cache).
        # Up to 4 breakpoints total: stable system, tools, last-message — 3 used.
        system = [{"type": "text", "text": self._system,
                   "cache_control": {"type": "ephemeral"}}]
        if self._dynamic_system:
            system.append({"type": "text", "text": self._dynamic_system})
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
        # THE single history→API transform: {role, content} with UI-only blocks
        # (e.g. the `attachments` chip block) stripped. Anything that reaches the
        # Anthropic SDK passes through here, so the validity guard test
        # (tests/test_chat_attachments) asserts api_messages' output against the
        # allow-list — that's the regression that would have caught the live 400.
        from core.runtime.history_prep import api_messages
        messages = api_messages(self._history)
        # Cache_control on the last message's last block — the third (of
        # three) caching breakpoint. Done BEFORE the dump so the persisted
        # payload reflects what the API actually sees (was previously
        # written without this marker, which made the dump misleading
        # about caching behavior).
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
        # Debug: persist the EXACT, replayable ("callable") request — the
        # structured system (list with cache_control on the stable prefix),
        # tools (cache_control on the last), and messages (cache_control on
        # the last block). Same kwargs the stream call below receives, so
        # `json.load(open(f)); client.messages.create(**payload)` reproduces
        # the request — including its caching behavior — byte-for-byte. Set
        # ABA_RAW_REQUEST_DIR to redirect; default /tmp/aba_llm_sent; "off"
        # / "0" / "" disables.
        import os as _os, hashlib as _hashlib, time as _time
        import json as _json
        _rawdir = _os.environ.get("ABA_RAW_REQUEST_DIR", "/tmp/aba_llm_sent")
        if _rawdir and _rawdir.lower() not in ("off", "0", "false", ""):
            try:
                _os.makedirs(_rawdir, exist_ok=True)
                _payload = {"model": self._model, "max_tokens": max_tok,
                            "system": system, "tools": tools, "messages": messages}
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
                _full_sys = (self._system or "") + (self._dynamic_system or "")
                _sys_sha = _hashlib.sha256(_full_sys.encode("utf-8")).hexdigest()[:12]
                print(f"[llm-sent] model={self._model} sys_sha={_sys_sha} "
                      f"hist_sha={_hist_sha} n_msgs={len(messages)} "
                      f"sys_chars={len(_full_sys)}"
                      f" (stable={len(self._system or '')}+dyn={len(self._dynamic_system or '')}) -> {_fn}",
                      flush=True)
            except Exception:  # noqa: BLE001 — debug dump must never break a turn
                pass
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


_auto_oauth_logged = False


def _credential_mode() -> str:
    """ABA_LLM_CREDENTIAL ∈ {apikey, oauth, oauth_cc}. An explicit value always
    wins. When UNSET, auto-default to oauth_cc IFF there is no API key but a usable
    Claude Code subscription token is present (refreshable store /
    $CLAUDE_CODE_OAUTH_TOKEN / ~/.claude/.credentials.json) — so a personal install
    'just works' on the subscription with no paste. Logged once (not silent); save
    an API key or set ABA_LLM_CREDENTIAL=apikey to opt out. NB: oauth_cc bills the
    subscription, so this is a personal/dev convenience."""
    explicit = os.environ.get("ABA_LLM_CREDENTIAL")
    if explicit:
        return explicit.lower()
    # No explicit mode AND no API key: prefer a detected subscription token over a
    # broken 'apikey' mode with an empty key. (Short-circuits before the file/IO of
    # _oauth_bearer when an API key IS set — the common path.)
    if not (os.environ.get("ANTHROPIC_API_KEY") or API_KEY) and _oauth_bearer():
        global _auto_oauth_logged
        if not _auto_oauth_logged:
            _auto_oauth_logged = True
            print("[llm] no API key set — auto-using the detected Claude Code "
                  "subscription token (oauth_cc). Save an API key or set "
                  "ABA_LLM_CREDENTIAL=apikey to opt out.", flush=True)
        return "oauth_cc"
    return "apikey"


def _current_api_key() -> str:
    """The Anthropic API key, read LIVE from the env (falling back to the
    import-time snapshot). Reading live — not the frozen `API_KEY` import — lets
    Settings → Account swap the key without a backend restart (core.credentials
    updates os.environ + clears the client cache)."""
    return os.environ.get("ANTHROPIC_API_KEY") or API_KEY or ""


def _wants_cc_marker() -> bool:
    """True iff we must prepend the Claude Code marker as the first system
    block — needed only on oauth_cc mode (OAuth bearer + non-Haiku models)."""
    return _credential_mode() == "oauth_cc"


# OAuth token refresh (tier 2). The browser sign-in flow now persists a small
# store ($ABA_HOME/oauth.json: access_token + refresh_token + expires_at) so the
# backend can mint a new access token when the old one expires — instead of
# 401ing until a restart/re-auth. Public OAuth client params (not secrets;
# mirror the installer's auth.py).
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_REFRESH_SKEW = 120          # refresh this many seconds before expiry
_oauth_lock = threading.Lock()     # one refresh at a time (refresh tokens are single-use)


def _oauth_store_path():
    home = os.environ.get("ABA_HOME")
    return os.path.join(home, "oauth.json") if home else None


def _load_oauth_store():
    p = _oauth_store_path()
    if not p or not os.path.exists(p):
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return None


def _save_oauth_store(d: dict) -> None:
    p = _oauth_store_path()
    if not p:
        return
    try:
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)        # atomic
    except Exception:  # noqa: BLE001
        pass


def _refresh_oauth(store: dict):
    """Exchange the refresh_token for a fresh access token; persist the rotated
    pair and return the new access token. Returns None on failure (→ the caller
    surfaces a clean re-auth error rather than shipping a doomed bearer)."""
    import urllib.request, urllib.error  # noqa: PLC0415
    body = json.dumps({
        "grant_type": "refresh_token",
        "refresh_token": store["refresh_token"],
        "client_id": _OAUTH_CLIENT_ID,
    }).encode()
    req = urllib.request.Request(
        _OAUTH_TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": "aba-backend (kharchenkolab/aba)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:  # noqa: BLE001
        print(f"[llm] OAuth token refresh failed: {e}", flush=True)
        return None
    at = data.get("access_token")
    if not at:
        return None
    _save_oauth_store({
        "access_token": at,
        # Refresh tokens rotate (single-use); keep the new one, fall back to the
        # old only if the response omitted it.
        "refresh_token": data.get("refresh_token") or store.get("refresh_token"),
        "expires_at": time.time() + (data.get("expires_in") or 3600),
    })
    print("[llm] OAuth access token refreshed", flush=True)
    return at


def _oauth_bearer():
    """Claude Code subscription OAuth bearer, or None. Re-read per call so a
    refreshed token is picked up immediately.

    Priority: (1) ABA's own refreshable store ($ABA_HOME/oauth.json) from the
    browser flow — auto-refreshed near expiry; (2) $CLAUDE_CODE_OAUTH_TOKEN env
    (pasted/setup-token path — long-lived, not refreshable); (3) the Claude Code
    CLI store (~/.claude/.credentials.json), which the CLI itself refreshes.
    An expired token with no way to refresh returns None so oauth_cc mode can
    refuse with a clear, actionable error instead of a confusing 401."""
    # (1) ABA's refreshable store.
    store = _load_oauth_store()
    if store and store.get("access_token"):
        exp = store.get("expires_at")
        if not exp or time.time() < exp - _OAUTH_REFRESH_SKEW:
            return store["access_token"]                       # still valid
        if store.get("refresh_token"):
            with _oauth_lock:
                s = _load_oauth_store() or {}                  # re-check: another turn may have refreshed
                e = s.get("expires_at")
                if s.get("refresh_token") and e and time.time() >= e - _OAUTH_REFRESH_SKEW:
                    return _refresh_oauth(s)                    # new token, or None on failure
                return s.get("access_token")                   # already refreshed by another turn
        return None                                            # expired, no refresh token → re-auth

    # (2) Static env var (back-compat / pasted token).
    tok = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if tok:
        return tok.strip()

    # (3) Claude Code CLI store (CLI keeps it refreshed).
    cred = os.path.expanduser("~/.claude/.credentials.json")
    if os.path.exists(cred):
        try:
            oa = json.load(open(cred)).get("claudeAiOauth") or {}
            exp = oa.get("expiresAt")
            # expiresAt is ms-since-epoch; treat expired (5s grace) as missing.
            if isinstance(exp, (int, float)) and exp <= int(time.time() * 1000) + 5_000:
                return None
            return (oa.get("accessToken") or "").strip() or None
        except Exception:  # noqa: BLE001
            return None
    return None


class OAuthTokenUnavailable(RuntimeError):
    """Raised when oauth_cc mode is configured but no usable Claude Code OAuth token
    is available (missing file, missing $CLAUDE_CODE_OAUTH_TOKEN, or token expired).
    Caught by guide.py's stream error handler — mapped to a friendly UI toast."""


# Module-level cache for AsyncAnthropic / Anthropic clients. Keyed by
# (mode, auth_material) so a refreshed OAuth token (which the SDK can't
# update on a constructed client) just produces a new cache entry —
# the old client lingers until GC, doesn't break in-flight calls.
#
# Before this cache + HTTP/2: every LLM call constructed a fresh
# AsyncAnthropic, which created a fresh httpx.AsyncClient over HTTP/1.1,
# which performed a fresh TLS handshake to api.anthropic.com.
# HTTP/1.1 streaming responses cannot multiplex — so even when reusing
# the AsyncAnthropic instance, each `messages.stream(...)` call still
# opened a new connection. Measured 1.2-2.4s of `create` overhead per
# turn on [direct-timing] markers (2026-06-21), ~13s of dead time across
# a 4-turn agentic loop.
#
# Fix: cache the AsyncAnthropic, BUT also pass a custom httpx client
# with http2=True so streams multiplex over a single connection.
_ASYNC_CLIENT_CACHE: dict[tuple[str, str], "object"] = {}
_SYNC_CLIENT_CACHE:  dict[tuple[str, str], "object"] = {}


def _httpx_async_client():
    """Tuned httpx.AsyncClient for the Anthropic SDK. http2 enables
    stream multiplexing (the source of the per-call TLS handshake);
    keepalive_expiry kept generous so an idle thread doesn't drop the
    connection."""
    import httpx
    return httpx.AsyncClient(
        http2=True,
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=10,
                            keepalive_expiry=300.0),
    )


def _httpx_sync_client():
    import httpx
    return httpx.Client(
        http2=True,
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_keepalive_connections=4,
                            keepalive_expiry=300.0),
    )


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
    mode = _credential_mode()
    if mode in ("oauth", "oauth_cc"):
        tok = _oauth_bearer()
        if tok:
            key = (mode, tok)
            cli = _ASYNC_CLIENT_CACHE.get(key)
            if cli is None:
                cli = anthropic.AsyncAnthropic(
                    auth_token=tok, http_client=_httpx_async_client())
                _ASYNC_CLIENT_CACHE[key] = cli
            return cli
        # No token + oauth_cc means the user explicitly chose subscription billing;
        # silently falling back to the .env API key would burn an unrelated budget
        # and hide the real problem. Refuse with a clear, actionable error.
        if mode == "oauth_cc":
            raise OAuthTokenUnavailable(
                "Claude Code OAuth token is missing or expired. "
                "Run `claude` (any quick command) to refresh ~/.claude/.credentials.json, "
                "or set $CLAUDE_CODE_OAUTH_TOKEN. Server bounce not required.")
    key = ("apikey", _current_api_key())
    cli = _ASYNC_CLIENT_CACHE.get(key)
    if cli is None:
        cli = anthropic.AsyncAnthropic(
            api_key=_current_api_key(), http_client=_httpx_async_client())
        _ASYNC_CLIENT_CACHE[key] = cli
    return cli


def sync_anthropic_client():
    """Sync Anthropic client. Mirrors `_llm_client` (mode selection, token
    handling, oauth_cc enforcement) but returns the sync `Anthropic` class —
    used by worker-thread callers that need `client.messages.create(...)`
    to block until the response is available (e.g., caption-generation,
    thread-history summarization). Lifted from
    `content.bio.lifecycle.promote._sync_anthropic_client` (Phase C.2 of
    misc/modularity_audit.md) — the SDK construction is domain-neutral."""
    import anthropic
    mode = _credential_mode()
    if mode in ("oauth", "oauth_cc"):
        tok = _oauth_bearer()
        if tok:
            key = (mode, tok)
            cli = _SYNC_CLIENT_CACHE.get(key)
            if cli is None:
                cli = anthropic.Anthropic(
                    auth_token=tok, http_client=_httpx_sync_client())
                _SYNC_CLIENT_CACHE[key] = cli
            return cli
        if mode == "oauth_cc":
            raise OAuthTokenUnavailable(
                "Claude Code OAuth token is missing or expired. "
                "Run `claude` (any quick command) to refresh ~/.claude/.credentials.json, "
                "or set $CLAUDE_CODE_OAUTH_TOKEN. Server bounce not required.")
    key = ("apikey", _current_api_key())
    cli = _SYNC_CLIENT_CACHE.get(key)
    if cli is None:
        cli = anthropic.Anthropic(
            api_key=_current_api_key(), http_client=_httpx_sync_client())
        _SYNC_CLIENT_CACHE[key] = cli
    return cli


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

    def open_stream(history, tools, system: str = "", model: str | None = None,
                    dynamic_system: str = ""):
        return _RealStream(_llm_client(), history, tools, system, model or MODEL,
                           dynamic_system=dynamic_system)
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

    def open_stream(history, tools, system: str = "", model: str | None = None,
                    dynamic_system: str = ""):  # noqa: ARG001
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
