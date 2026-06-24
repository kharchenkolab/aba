"""Logging plumbing for LLM calls — dump fidelity and per-call usage line.

LOG-1: The dump persisted to ABA_RAW_REQUEST_DIR must reflect the exact
       structured payload the API call receives — including cache_control
       markers on the stable system prefix, the last tool, and the last
       message block. Pre-fix it dumped the string-form system without
       any cache_control, which made "is caching working?" answerable
       only via a SQLite dive (or by trusting the code).

LOG-2: Every successful stream emits a one-line `[llm-done]` print with
       in/out/cache_read/cache_write token counts. Default-on (no env
       gate) so live cache hit rate is a `tail -f | grep llm-done` away.
       The verbose `[direct-timing]` line stays gated on ABA_DEBUG_TIMING.
"""
from __future__ import annotations
import asyncio
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="aba_llm_log_")
os.environ["ABA_DB_PATH"]     = os.path.join(_tmp, "x.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_WORK_DIR"]    = os.path.join(_tmp, "work")
os.environ["ABA_ENVS_DIR"]    = os.path.join(_tmp, "envs")
os.environ["DATA_DIR"]        = os.path.join(_tmp, "data")
os.environ["ARTIFACTS_DIR"]   = os.path.join(_tmp, "artifacts")

from core.graph._schema import init_db                            # noqa: E402
init_db()


# ── Fakes ───────────────────────────────────────────────────────────────

class _FakeUsage:
    """Mirror of anthropic.types.Usage with the fields aba reads."""
    def __init__(self, *, input_tokens: int, output_tokens: int,
                  cache_read_input_tokens: int,
                  cache_creation_input_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = cache_read_input_tokens
        self.cache_creation_input_tokens = cache_creation_input_tokens


class _FakeFinalMsg:
    def __init__(self):
        self.usage = _FakeUsage(
            input_tokens=42,
            output_tokens=123,
            cache_read_input_tokens=98765,
            cache_creation_input_tokens=43210,
        )
        # Empty content — no tool_use, no text — exercises the
        # "no blocks" branch cleanly.
        self.content = []
        self.stop_reason = "end_turn"


class _FakeStream:
    """Stand-in for anthropic.MessageStream's async context manager."""
    def __init__(self):
        self._captured_kwargs: dict[str, Any] = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        async def _gen():
            if False:
                yield None        # noqa: E501 — typing-only async generator
        return _gen()

    async def get_final_message(self):
        return _FakeFinalMsg()


class _FakeMessages:
    def __init__(self, capture: dict):
        self._capture = capture

    def stream(self, **kwargs):
        # Stash kwargs so the test can compare them to the dump.
        self._capture.update(kwargs)
        return _FakeStream()


class _FakeClient:
    def __init__(self, capture: dict):
        self.messages = _FakeMessages(capture)


# ── Test fixtures ──────────────────────────────────────────────────────

def _history() -> list[dict]:
    """Two-message exchange. Tail block carries a tool_result — the
    realistic shape where cache_control will be stamped."""
    return [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "tu_1", "name": "x", "input": {}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "ok"},
        ]},
    ]


def _tools() -> list[dict]:
    return [
        {"name": "alpha", "description": "first", "input_schema": {"type": "object"}},
        {"name": "beta",  "description": "second", "input_schema": {"type": "object"}},
    ]


def _make_ctx(*, dump_dir: str, dynamic_system: str = "DYN-TAIL"):
    """Open an LLMStreamingClient with the fake client and the env that
    routes the dump into a per-test directory."""
    os.environ["ABA_RAW_REQUEST_DIR"] = dump_dir
    from core.llm import _RealStream
    capture: dict = {}
    return capture, _RealStream(
        client=_FakeClient(capture),
        history=_history(),
        tools=_tools(),
        system="STABLE-SYS-PREFIX",
        model="claude-test-model",
        dynamic_system=dynamic_system,
    )


# ── LOG-1: dump fidelity ────────────────────────────────────────────────

def test_dump_has_cache_control_on_stable_system():
    with tempfile.TemporaryDirectory(prefix="dump_") as dump_dir:
        capture, ctx = _make_ctx(dump_dir=dump_dir)
        asyncio.run(_open_and_close(ctx))
        dump = _load_one_dump(dump_dir)
        assert isinstance(dump["system"], list), "system must be structured-list form"
        # First system block is the stable prefix and carries the
        # cache_control marker; subsequent blocks (dynamic tail) do not.
        assert dump["system"][0]["cache_control"] == {"type": "ephemeral"}
        assert dump["system"][0]["text"] == "STABLE-SYS-PREFIX"
        # Dynamic tail is present but uncached — sanity-check both.
        assert any(b["text"] == "DYN-TAIL" for b in dump["system"])
        assert all("cache_control" not in b
                   for b in dump["system"] if b.get("text") == "DYN-TAIL")


def test_dump_has_cache_control_on_last_tool():
    with tempfile.TemporaryDirectory(prefix="dump_") as dump_dir:
        capture, ctx = _make_ctx(dump_dir=dump_dir)
        asyncio.run(_open_and_close(ctx))
        dump = _load_one_dump(dump_dir)
        assert dump["tools"][-1]["cache_control"] == {"type": "ephemeral"}
        # Earlier tools must NOT carry the marker.
        for t in dump["tools"][:-1]:
            assert "cache_control" not in t


def test_dump_has_cache_control_on_last_message_block():
    with tempfile.TemporaryDirectory(prefix="dump_") as dump_dir:
        capture, ctx = _make_ctx(dump_dir=dump_dir)
        asyncio.run(_open_and_close(ctx))
        dump = _load_one_dump(dump_dir)
        last_msg = dump["messages"][-1]
        last_block = last_msg["content"][-1]
        assert last_block["cache_control"] == {"type": "ephemeral"}


def test_dump_matches_actual_api_call_kwargs():
    """The whole point: dump bytes ≈ what the stream call received.
    Loading the dump and calling client.messages.create(**dump) is the
    canonical replay path; that requires byte-for-byte equivalence."""
    with tempfile.TemporaryDirectory(prefix="dump_") as dump_dir:
        capture, ctx = _make_ctx(dump_dir=dump_dir)
        asyncio.run(_open_and_close(ctx))
        dump = _load_one_dump(dump_dir)
        for k in ("system", "tools", "messages", "model", "max_tokens"):
            assert dump[k] == capture[k], (k, dump[k], capture[k])


# ── LOG-2: [llm-done] line ──────────────────────────────────────────────

def test_llm_done_line_fires_unconditionally():
    """Default-on. Captures the print and checks for the prefix +
    expected fields. Synthesized via open_and_consume_stream (the
    direct path) since [llm-done] lives there."""
    captured = _run_open_and_consume_capture_stdout()
    assert "[llm-done]" in captured, captured
    # Tokens should reflect the fake usage we wired in.
    assert "in=42t" in captured
    assert "out=123t" in captured
    assert "cache_read=98765t" in captured
    assert "cache_write=43210t" in captured
    assert "model=claude-test-model" in captured


def test_llm_done_does_not_require_debug_timing_env():
    """The verbose [direct-timing] line stays gated; the new [llm-done]
    must not. Ensure env state is clean during the test."""
    os.environ.pop("ABA_DEBUG_TIMING", None)
    captured = _run_open_and_consume_capture_stdout()
    assert "[llm-done]" in captured
    # Verbose breakdown should be ABSENT when the gate is off.
    assert "[direct-timing]" not in captured


# ── Helpers ─────────────────────────────────────────────────────────────

async def _open_and_close(ctx):
    """Open + close the LLMStreamingClient context, flushing the dump
    path. No stream consumption needed — the dump is written at
    __aenter__."""
    await ctx.__aenter__()
    await ctx.__aexit__(None, None, None)


def _load_one_dump(dump_dir: str) -> dict:
    files = sorted(Path(dump_dir).glob("req_*.json"))
    assert files, f"no dump file written in {dump_dir}"
    assert len(files) == 1, f"expected exactly 1 dump, got {len(files)}"
    return json.loads(files[0].read_text())


def _run_open_and_consume_capture_stdout() -> str:
    """Drive open_and_consume_stream against the fake client and
    capture stdout so the [llm-done] line is observable."""
    # open_and_consume_stream creates its own client via factory; the
    # cleanest mock is to monkeypatch the DirectAPIRuntime client builder
    # to return our fake. We patch where it's read.
    import core.runtime.llm_runtime_direct as direct
    import core.llm as llm_mod
    capture: dict = {}
    fake = _FakeClient(capture)

    # Force open_and_consume_stream to use our fake client. The function
    # builds its own via core.llm._llm_client; patch the factory.
    orig_llm_client = llm_mod._llm_client
    llm_mod._llm_client = lambda *a, **kw: fake     # type: ignore[assignment]

    class _NeverCancel:
        @property
        def cancelled(self) -> bool: return False

    async def _drain():
        async for _ in direct.open_and_consume_stream(
            history=_history(), tools=_tools(),
            system="STABLE-SYS-PREFIX", dynamic_system="",
            model="claude-test-model", cancel_token=_NeverCancel(),
            max_retries=0,
        ):
            pass

    buf = io.StringIO()
    try:
        with redirect_stdout(buf):
            asyncio.run(_drain())
    finally:
        llm_mod._llm_client = orig_llm_client       # type: ignore[assignment]
    return buf.getvalue()
