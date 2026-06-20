"""OpenAICompatibleRuntime — protocol conformance + behavioral skeleton.

Mirrors test_direct_api_runtime_skeleton.py: imports clean (no bio),
Protocol satisfied, run_turn is a real async generator.

Behavioral coverage uses a fake openai.AsyncOpenAI client that yields
canned ChatCompletions chunks. That lets us pin the event sequence
the runtime emits without touching a real endpoint — the live smoke
against localhost:8001 runs separately in test_openai_runtime_live.py.
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_openrt_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "rt.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
sys.path.insert(0, str(ROOT / "backend"))


from core.runtime.llm_runtime import (                     # noqa: E402
    LLMRuntime, RuntimeRequest, SystemSpec,
    TextDelta, ToolUseStart, ToolResult, TurnDone, TurnHalt,
)
from core.runtime.llm_runtime_openai import (              # noqa: E402
    OpenAICompatibleRuntime, _conforms_to_protocol,
)


pytestmark = pytest.mark.platform


# ── 1. Structural ───────────────────────────────────────────────────
def test_imports_clean():
    assert RuntimeRequest is not None
    assert SystemSpec is not None
    for cls in (TextDelta, ToolUseStart, ToolResult, TurnDone, TurnHalt):
        assert cls is not None


def test_protocol_conformance():
    assert _conforms_to_protocol(), "OpenAICompatibleRuntime missing run_turn"
    assert hasattr(OpenAICompatibleRuntime, "run_turn")


def test_run_turn_is_async_generator():
    import inspect
    assert inspect.isasyncgenfunction(OpenAICompatibleRuntime.run_turn)


def test_module_has_no_bio_imports():
    """Platform-tier module — must not import from content.*."""
    import ast
    src = (ROOT / "backend" / "core" / "runtime"
           / "llm_runtime_openai.py").read_text()
    tree = ast.parse(src)
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and \
                node.module.startswith("content."):
            bad.append(f"line {node.lineno}: from {node.module}")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("content."):
                    bad.append(f"line {node.lineno}: import {alias.name}")
    assert not bad, ("must not import content.*: " + ", ".join(bad))


# ── 2. Behavioral skeleton with a fake AsyncOpenAI client ──────────
class _FakeUsage:
    def __init__(self, prompt_tokens=10, completion_tokens=5):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChoice:
    def __init__(self, delta, finish_reason=None):
        self.delta = delta
        self.finish_reason = finish_reason


class _FakeChunk:
    def __init__(self, choices=None, usage=None):
        self.choices = choices or []
        self.usage = usage


class _FakeToolCall:
    def __init__(self, index, id=None, name=None, arguments=None):
        self.index = index
        self.id = id
        if name is not None or arguments is not None:
            self.function = _FakeFunction(name, arguments)
        else:
            self.function = None


class _FakeFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


async def _stream_from(chunks):
    for c in chunks:
        yield c


class _FakeChatCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **kw):
        return _stream_from(self._chunks)


class _FakeAsyncOpenAI:
    def __init__(self, chunks):
        self.chat = type("C", (), {"completions": _FakeChatCompletions(chunks)})()


def _patch_client(monkeypatch, chunks):
    rt = OpenAICompatibleRuntime(base_url="http://fake", api_key="none")
    rt._client = _FakeAsyncOpenAI(chunks)
    return rt


def _req(**over) -> RuntimeRequest:
    return RuntimeRequest(
        history=over.get("history", [{"role": "user", "content": "hi"}]),
        tools=over.get("tools", []),
        system=over.get("system", SystemSpec(stable="you are a test", dynamic="")),
        model=over.get("model", "qwen3-8b"),
        max_tokens=over.get("max_tokens", 256),
        ctx=over.get("ctx", {"thread_id": "t"}),
    )


def _collect(rt, req, tool_executor=None, halt_on_tools=frozenset()):
    async def _drive():
        events = []
        async for ev in rt.run_turn(req,
                                    tool_executor or _noop_tool_exec,
                                    halt_on_tools=halt_on_tools):
            events.append(ev)
        return events
    return asyncio.get_event_loop().run_until_complete(_drive())


async def _noop_tool_exec(name, input_, ctx):
    return {"ok": True, "tool": name, "echo": input_}


def test_text_only_response_yields_textdelta_then_turndone(monkeypatch):
    chunks = [
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(content="Hello "))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(content="world!"),
                                         finish_reason="stop")]),
        _FakeChunk(choices=[], usage=_FakeUsage(20, 3)),
    ]
    rt = _patch_client(monkeypatch, chunks)
    evs = _collect(rt, _req())
    # Event sequence: text deltas → private _StreamCompleted (guide.py
    # reads assistant_blocks/usage off it) → TurnDone.
    assert [type(e).__name__ for e in evs] == \
        ["TextDelta", "TextDelta", "_StreamCompleted", "TurnDone"]
    assert evs[0].text == "Hello "
    assert evs[1].text == "world!"
    sc = evs[2]
    assert sc.stop_reason == "end_turn"
    assert sc.usage_delta["input"]  == 20
    assert sc.usage_delta["output"] == 3
    assert sc.assistant_blocks == [{"type": "text", "text": "Hello world!"}]
    assert sc.tool_calls_this_turn == []
    # TurnDone mirrors the same stop_reason + usage for callers that
    # only listen for the public event.
    assert evs[3].stop_reason == "end_turn"
    assert evs[3].usage["input"]  == 20
    assert evs[3].usage["output"] == 3


def test_think_block_stripped_from_visible_text(monkeypatch):
    """Visible text excludes the <think> block. Usage still captured."""
    chunks = [
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(content="<think>"))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(content="reasoning"))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(content="</think>"))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(content="answer"),
                                         finish_reason="stop")]),
    ]
    rt = _patch_client(monkeypatch, chunks)
    evs = _collect(rt, _req())
    visible = "".join(e.text for e in evs if isinstance(e, TextDelta))
    assert visible == "answer"
    assert isinstance(evs[-1], TurnDone)


def test_tool_call_yields_toolusestart_then_toolresult(monkeypatch):
    """First chunk: tool_call id + function.name; subsequent: arguments
    string; final: finish_reason=tool_calls."""
    chunks = [
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[
            _FakeToolCall(index=0, id="call_x", name="search")]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[
            _FakeToolCall(index=0, arguments='{"q":"alpha"}')]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(),
                                         finish_reason="tool_calls")]),
        _FakeChunk(choices=[], usage=_FakeUsage(50, 12)),
    ]
    rt = _patch_client(monkeypatch, chunks)
    evs = _collect(rt, _req(), tool_executor=_noop_tool_exec)
    names = [type(e).__name__ for e in evs]
    # _StreamCompleted fires AFTER the model phase + BEFORE tool
    # dispatch so guide.py can append_message("assistant", blocks)
    # before the tool_result lands in history.
    assert names == ["_StreamCompleted", "ToolUseStart",
                     "ToolResult", "TurnDone"]
    sc = evs[0]
    # finish_reason="tool_calls" maps to Anthropic's "tool_use" so
    # guide.py:1156 (`if _stop_reason != "tool_use": break`) keeps
    # looping after the tool dispatch — critical for multi-turn flow.
    assert sc.stop_reason == "tool_use"
    assert sc.tool_calls_this_turn == ["search"]
    # assistant_blocks must include the tool_use so the next
    # iteration's history has the call paired with its result.
    tu = [b for b in sc.assistant_blocks if b["type"] == "tool_use"]
    assert tu == [{"type": "tool_use", "id": "call_x",
                   "name": "search", "input": {"q": "alpha"}}]
    s = evs[1]; r = evs[2]; d = evs[3]
    assert s.tool_use_id == "call_x"
    assert s.tool_name   == "search"
    assert s.input       == {"q": "alpha"}
    assert r.tool_use_id == "call_x"
    assert r.tool_name   == "search"
    assert r.result["tool"] == "search"
    assert d.stop_reason == "tool_use"


def test_halt_on_tools_yields_turnhalt_before_dispatch(monkeypatch):
    chunks = [
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[
            _FakeToolCall(index=0, id="call_x",
                          name="present_plan")]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[
            _FakeToolCall(index=0, arguments='{"title":"X"}')]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(),
                                         finish_reason="tool_calls")]),
    ]
    called: list = []
    async def trip(name, input_, ctx):
        called.append(name)
        return {"ok": True}
    rt = _patch_client(monkeypatch, chunks)
    evs = _collect(rt, _req(), tool_executor=trip,
                   halt_on_tools=frozenset({"present_plan"}))
    # _StreamCompleted (guide.py captures assistant_blocks here),
    # then ToolUseStart (UI hint), then TurnHalt.
    assert [type(e).__name__ for e in evs] == \
        ["_StreamCompleted", "ToolUseStart", "TurnHalt"]
    halt = evs[2]
    assert halt.reason == "pending_tool"
    assert halt.detail["tool_name"] == "present_plan"
    # Executor must NOT have been called (it's the orchestrator's job).
    assert called == []


def test_malformed_tool_args_yield_error_toolresult(monkeypatch):
    """If the model emits invalid JSON in tool arguments, we surface
    an error tool_result the orchestrator can record + the next turn
    can retry."""
    chunks = [
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[
            _FakeToolCall(index=0, id="call_x", name="search")]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(tool_calls=[
            _FakeToolCall(index=0, arguments='not json')]))]),
        _FakeChunk(choices=[_FakeChoice(_FakeDelta(),
                                         finish_reason="tool_calls")]),
    ]
    called: list = []
    async def never(name, input_, ctx):
        called.append(name); return {}
    rt = _patch_client(monkeypatch, chunks)
    evs = _collect(rt, _req(), tool_executor=never)
    # _StreamCompleted first, then ToolUseStart with empty input
    # (parser failed), then ToolResult err, then TurnDone.
    assert [type(e).__name__ for e in evs] == \
        ["_StreamCompleted", "ToolUseStart", "ToolResult", "TurnDone"]
    r = evs[2]
    assert r.result.get("is_error") is True
    assert "JSON" in r.result.get("content", "")
    assert called == []


def test_runtime_translates_history_before_calling_api(monkeypatch):
    """Sanity: the messages payload the runtime sends to the API
    includes the system + translated history (assistant tool_calls,
    tool messages). We capture by replacing create()."""
    captured: dict = {}

    class _Capture:
        async def create(self, **kw):
            captured.update(kw)
            async def _empty():
                yield _FakeChunk(choices=[_FakeChoice(_FakeDelta(),
                                                      finish_reason="stop")])
                yield _FakeChunk(choices=[], usage=_FakeUsage(1, 1))
            return _empty()

    rt = OpenAICompatibleRuntime(base_url="http://fake", api_key="none")
    rt._client = type("C", (), {"chat": type("X", (),
                                {"completions": _Capture()})()})()

    history = [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1",
             "name": "list_data_files", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1",
             "content": '{"files":[]}'}]},
    ]
    _collect(rt, _req(history=history))
    msgs = captured["messages"]
    roles = [m["role"] for m in msgs]
    assert roles == ["system", "user", "assistant", "tool"]
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "list_data_files"
    assert msgs[3]["tool_call_id"] == "t1"


def test_runtime_uses_default_qwen_model_when_req_model_empty(monkeypatch):
    """When the spec's model is empty string (or None), fall back to
    ABA_OPENAI_MODEL / "qwen3-8b" so a misconfigured spec still talks
    to the right endpoint."""
    monkeypatch.delenv("ABA_OPENAI_MODEL", raising=False)
    captured: dict = {}

    class _Capture:
        async def create(self, **kw):
            captured.update(kw)
            async def _empty():
                yield _FakeChunk(choices=[_FakeChoice(_FakeDelta(),
                                                      finish_reason="stop")])
            return _empty()

    rt = OpenAICompatibleRuntime(base_url="http://fake", api_key="none")
    rt._client = type("C", (), {"chat": type("X", (),
                                {"completions": _Capture()})()})()
    _collect(rt, _req(model=""))
    assert captured["model"] == "qwen3-8b"


def test_runtime_constructor_reads_env(monkeypatch):
    monkeypatch.setenv("ABA_OPENAI_BASE_URL", "http://envset:9999/v1")
    monkeypatch.setenv("ABA_OPENAI_API_KEY",  "sk-env-test")
    rt = OpenAICompatibleRuntime()
    assert rt.base_url == "http://envset:9999/v1"
    assert rt.api_key  == "sk-env-test"


def test_constructor_args_override_env(monkeypatch):
    monkeypatch.setenv("ABA_OPENAI_BASE_URL", "http://envset:9999/v1")
    rt = OpenAICompatibleRuntime(base_url="http://override/v1")
    assert rt.base_url == "http://override/v1"


# ── thinking-mode knob (Qwen3-class default OFF for tool work) ──────
def test_thinking_defaults_off(monkeypatch):
    monkeypatch.delenv("ABA_OPENAI_ENABLE_THINKING", raising=False)
    rt = OpenAICompatibleRuntime()
    assert rt.enable_thinking is False


def test_thinking_env_on(monkeypatch):
    monkeypatch.setenv("ABA_OPENAI_ENABLE_THINKING", "1")
    rt = OpenAICompatibleRuntime()
    assert rt.enable_thinking is True


def test_thinking_constructor_overrides_env(monkeypatch):
    monkeypatch.setenv("ABA_OPENAI_ENABLE_THINKING", "0")
    rt = OpenAICompatibleRuntime(enable_thinking=True)
    assert rt.enable_thinking is True


def test_thinking_flag_passes_through_to_api_call(monkeypatch):
    """The chat_template_kwargs.enable_thinking value must reach the
    API call so vLLM actually honors it."""
    captured: dict = {}

    class _Capture:
        async def create(self, **kw):
            captured.update(kw)
            async def _empty():
                yield _FakeChunk(choices=[_FakeChoice(_FakeDelta(),
                                                      finish_reason="stop")])
            return _empty()

    rt = OpenAICompatibleRuntime(base_url="http://fake", api_key="none",
                                  enable_thinking=False)
    rt._client = type("C", (), {"chat": type("X", (),
                                {"completions": _Capture()})()})()
    _collect(rt, _req())
    eb = captured.get("extra_body") or {}
    ck = eb.get("chat_template_kwargs") or {}
    assert ck.get("enable_thinking") is False


def test_thinking_flag_on_passes_true(monkeypatch):
    captured: dict = {}

    class _Capture:
        async def create(self, **kw):
            captured.update(kw)
            async def _empty():
                yield _FakeChunk(choices=[_FakeChoice(_FakeDelta(),
                                                      finish_reason="stop")])
            return _empty()

    rt = OpenAICompatibleRuntime(base_url="http://fake", api_key="none",
                                  enable_thinking=True)
    rt._client = type("C", (), {"chat": type("X", (),
                                {"completions": _Capture()})()})()
    _collect(rt, _req())
    assert captured["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
