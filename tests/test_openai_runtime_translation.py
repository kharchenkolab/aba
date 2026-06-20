"""Pure-function translation helpers for the OpenAI-compatible runtime.

Three layers covered:

  1. translate_tools_to_openai — Anthropic tool schema → OpenAI tool
     schema. Round-trips name + description + input_schema. Empty/
     missing pieces normalize without crashing.

  2. translate_history_to_openai — Anthropic message blocks →
     OpenAI messages. Tool exchanges split into assistant+tool_calls
     and role:"tool" pairs. arguments must be JSON strings.

  3. ThinkStripper — stateful streaming filter for `<think>…</think>`.
     Tags straddling delta chunks must work correctly; visible text
     in either side of the block reaches the caller; the thinking
     payload is captured separately.

No I/O — these are platform-tier tests, no bio import needed.
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


pytestmark = pytest.mark.platform


from core.runtime.llm_runtime_openai import (   # noqa: E402
    translate_tools_to_openai,
    translate_history_to_openai,
    ThinkStripper,
    _split_trailing_tag,
    _normalize_stop_reason,
)


# ── 1. translate_tools_to_openai ────────────────────────────────────
def test_tool_schema_roundtrip_basic():
    src = [{
        "name":        "add",
        "description": "Return a + b.",
        "input_schema": {"type": "object",
                         "properties": {"a": {"type": "number"},
                                        "b": {"type": "number"}},
                         "required": ["a", "b"]},
    }]
    out = translate_tools_to_openai(src)
    assert out == [{
        "type":     "function",
        "function": {"name":        "add",
                     "description": "Return a + b.",
                     "parameters":  src[0]["input_schema"]},
    }]


def test_tool_schema_handles_missing_description_and_schema():
    src = [{"name": "noop"}]  # no description, no input_schema
    out = translate_tools_to_openai(src)
    assert out == [{
        "type":     "function",
        "function": {"name":        "noop",
                     "description": "",
                     "parameters":  {"type": "object"}},
    }]


def test_tool_schema_skips_nameless_tools():
    """Without a name there's nothing to call — drop silently."""
    src = [{"description": "useless"}, {"name": "ok"}]
    out = translate_tools_to_openai(src)
    assert [t["function"]["name"] for t in out] == ["ok"]


def test_tool_schema_empty_input():
    assert translate_tools_to_openai([]) == []
    assert translate_tools_to_openai(None) == []        # type: ignore[arg-type]


# ── 2. translate_history_to_openai ──────────────────────────────────
def test_history_plain_user_message():
    src = [{"role": "user", "content": "hi"}]
    out = translate_history_to_openai(src)
    assert out == [{"role": "user", "content": "hi"}]


def test_history_plain_assistant_text_block():
    src = [{"role": "assistant",
            "content": [{"type": "text", "text": "Hello!"}]}]
    out = translate_history_to_openai(src)
    assert out == [{"role": "assistant", "content": "Hello!"}]


def test_history_assistant_text_plus_tool_use_in_one_message():
    """The Anthropic shape often combines a text block and a tool_use
    block in the same assistant message. OpenAI wants both: content
    string + tool_calls list, in ONE message."""
    src = [{"role": "assistant", "content": [
        {"type": "text", "text": "Let me look that up."},
        {"type": "tool_use", "id": "toolu_01",
         "name": "search", "input": {"q": "alpha"}},
    ]}]
    out = translate_history_to_openai(src)
    assert len(out) == 1
    m = out[0]
    assert m["role"] == "assistant"
    assert m["content"] == "Let me look that up."
    assert len(m["tool_calls"]) == 1
    tc = m["tool_calls"][0]
    assert tc == {"id": "toolu_01", "type": "function",
                  "function": {"name": "search",
                               "arguments": '{"q": "alpha"}'}}
    # Arguments MUST be a JSON-encoded string — round-trip.
    assert json.loads(tc["function"]["arguments"]) == {"q": "alpha"}


def test_history_user_tool_result_block_becomes_tool_role_message():
    src = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "toolu_01",
         "content": "result text"},
    ]}]
    out = translate_history_to_openai(src)
    assert out == [{"role": "tool", "tool_call_id": "toolu_01",
                    "content": "result text"}]


def test_history_tool_result_with_nested_text_blocks():
    """Anthropic sometimes nests text blocks inside tool_result.content."""
    src = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "x",
         "content": [{"type": "text", "text": "ok"},
                     {"type": "text", "text": "done"}]},
    ]}]
    out = translate_history_to_openai(src)
    assert out == [{"role": "tool", "tool_call_id": "x",
                    "content": "ok\ndone"}]


def test_history_multiple_tool_results_each_become_one_tool_message():
    src = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "first"},
        {"type": "tool_result", "tool_use_id": "b", "content": "second"},
    ]}]
    out = translate_history_to_openai(src)
    assert out == [
        {"role": "tool", "tool_call_id": "a", "content": "first"},
        {"role": "tool", "tool_call_id": "b", "content": "second"},
    ]


def test_history_user_text_after_tool_results_becomes_separate_user_message():
    """A turn often has tool_results AND a fresh user text in the
    same Anthropic message. Split them into the correct sequence."""
    src = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "a", "content": "r"},
        {"type": "text",        "text": "now plot it"},
    ]}]
    out = translate_history_to_openai(src)
    assert out == [
        {"role": "tool", "tool_call_id": "a", "content": "r"},
        {"role": "user", "content": "now plot it"},
    ]


def test_history_full_round_trip_three_turn_tool_loop():
    src = [
        {"role": "user", "content": "list data files"},
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_1",
             "name": "list_data_files", "input": {}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1",
             "content": '{"files": []}'}]},
        {"role": "assistant", "content": [
            {"type": "text", "text": "No files yet."}]},
    ]
    out = translate_history_to_openai(src)
    assert [m["role"] for m in out] == \
        ["user", "assistant", "tool", "assistant"]


def test_history_empty_input():
    assert translate_history_to_openai([]) == []
    assert translate_history_to_openai(None) == []     # type: ignore[arg-type]


# ── 3. ThinkStripper ────────────────────────────────────────────────
def test_strip_think_no_tag_passes_through():
    s = ThinkStripper()
    v, t = s.feed("hello world")
    assert v == "hello world" and t == ""
    v, t = s.flush()
    assert v == "" and t == ""


def test_strip_think_complete_tag_in_one_chunk():
    s = ThinkStripper()
    v, t = s.feed("hi <think>secret</think> there")
    assert v == "hi  there"
    assert t == "secret"
    assert s.flush() == ("", "")


def test_strip_think_tag_straddles_two_chunks():
    """The classic streaming hazard: `<` arrives in chunk 1, the
    rest of the tag in chunk 2. The buffered prefix must not leak."""
    s = ThinkStripper()
    v1, t1 = s.feed("hi <thi")
    v2, t2 = s.feed("nk>x</think> done")
    assert v1 == "hi "          # buffered "<thi" not emitted yet
    assert t1 == ""
    assert v2 == " done"
    assert t2 == "x"
    assert s.flush() == ("", "")


def test_strip_think_closing_tag_straddles_chunks():
    s = ThinkStripper()
    v1, t1 = s.feed("<think>thought one</thi")
    v2, t2 = s.feed("nk>answer")
    assert v1 == "" and t1 == "thought one"
    assert v2 == "answer" and t2 == ""


def test_strip_think_handles_lt_that_is_not_a_tag_open():
    """`<X` is not '<think>'. After enough chars to decide, the
    buffered text gets released to visible."""
    s = ThinkStripper()
    v, t = s.feed("compare a<b and ")
    # "<b and " in particular — the safe-emit logic must release "<b"
    # once we have enough chars to know it's NOT "<think>".
    assert "compare a<b" in v
    assert t == ""
    s.flush()


def test_strip_think_three_chunks_with_block_spanning_two():
    s = ThinkStripper()
    v, t = s.feed("pre")
    assert v == "pre" and t == ""
    v, t = s.feed("<think>mid")
    assert v == "" and t == "mid"
    v, t = s.feed("dle</think>post")
    assert v == "post" and t == "dle"


def test_strip_think_unclosed_block_flushed_as_thinking():
    """If a stream ends mid-block (truncated by max_tokens), whatever
    we'd buffered as thinking should still be returned, not silently
    dropped."""
    s = ThinkStripper()
    v, t = s.feed("<think>partial idea")
    assert v == "" and t == "partial idea"
    fv, ft = s.flush()
    assert fv == "" and ft == ""    # no remaining buffer


def test_strip_think_buffered_partial_tag_at_end_of_stream():
    s = ThinkStripper()
    v, t = s.feed("ok <thi")
    assert v == "ok " and t == ""    # "<thi" buffered, not emitted
    fv, ft = s.flush()
    # End of stream, never resolved → release as visible text.
    assert fv == "<thi" and ft == ""


# ── 4. _normalize_stop_reason — Anthropic vocabulary mapping ────────
def test_normalize_stop_reason_tool_calls_to_tool_use():
    """The critical mapping: guide.py:1156 only loops when stop_reason
    == 'tool_use'. OpenAI's 'tool_calls' must normalize to that or the
    agent loop ends after one tool dispatch (prj_03090d30 bug)."""
    assert _normalize_stop_reason("tool_calls") == "tool_use"


def test_normalize_stop_reason_stop_to_end_turn():
    assert _normalize_stop_reason("stop") == "end_turn"


def test_normalize_stop_reason_length_to_max_tokens():
    assert _normalize_stop_reason("length") == "max_tokens"


def test_normalize_stop_reason_content_filter_to_end_turn():
    assert _normalize_stop_reason("content_filter") == "end_turn"


def test_normalize_stop_reason_none_defaults_to_end_turn():
    assert _normalize_stop_reason(None) == "end_turn"
    assert _normalize_stop_reason("") == "end_turn"


def test_normalize_stop_reason_unknown_passes_through():
    """An unrecognized finish_reason isn't translated — preserve so a
    debug grep on 'tool_calls' wouldn't lie about what the API
    actually returned in some new edge case."""
    assert _normalize_stop_reason("future_thing") == "future_thing"


# ── 5. _split_trailing_tag (internal helper, lock in semantics) ─────
def test_split_trailing_tag_holds_back_partial_match():
    assert _split_trailing_tag("foo<thi", "<think>")    == ("foo", "<thi")
    assert _split_trailing_tag("foo<",     "<think>")   == ("foo", "<")
    assert _split_trailing_tag("foo",      "<think>")   == ("foo", "")


def test_split_trailing_tag_releases_definitive_mismatch():
    # "<x" is definitely not a prefix of "<think>" — release.
    assert _split_trailing_tag("hi<x more", "<think>") == ("hi<x more", "")
    # Multiple `<`s; only the trailing one matters.
    assert _split_trailing_tag("a<b<", "<think>")      == ("a<b", "<")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
