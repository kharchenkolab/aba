"""Runtime parity for volatile-tail placement.

One invariant, three transports: the per-turn volatile context
(`req.system.dynamic`) must be delivered OUTSIDE the cached prefix — after the
conversation history — on every runtime, because every cache involved is
prefix-only (Anthropic prompt caching on the direct lane, vLLM automatic prefix
caching / OpenAI prompt caching on the openai lane, the SDK's system-block cache
on the sdk lane). A volatile byte positioned before the history re-bills (or
re-computes) the whole conversation on every turn it changes.

The direct lane's placement is guarded in tests/test_catalog_caching.py; this
file pins the openai lane and the cross-runtime wrapper format.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.llm_runtime_openai import (  # noqa: E402
    place_volatile_tail_openai, translate_history_to_openai)

pytestmark = pytest.mark.platform

_HIST = [{"role": "user", "content": "hello"},
         {"role": "assistant", "content": "hi"},
         {"role": "user", "content": "latest"}]


def test_chat_lane_tail_rides_after_history():
    msgs = [{"role": "system", "content": "STABLE"},
            *translate_history_to_openai(_HIST)]
    out = place_volatile_tail_openai(msgs, "VOLATILE")
    assert out[-1]["role"] == "user" and "VOLATILE" in out[-1]["content"]
    assert out[:-1] == msgs, "history/system must be untouched ahead of the tail"
    assert "VOLATILE" not in out[0]["content"], "tail leaked into the system message"


def test_responses_lane_tail_rides_after_history():
    items = [{"role": "user", "content": [{"type": "input_text", "text": "q"}]}]
    out = place_volatile_tail_openai(items, "VOLATILE", responses_shape=True)
    assert out[-1]["role"] == "user"
    assert out[-1]["content"] == [{"type": "input_text",
                                   "text": "<system-reminder>\nVOLATILE\n</system-reminder>"}]
    assert out[:-1] == items


def test_empty_tail_is_a_noop():
    msgs = [{"role": "system", "content": "S"}]
    assert place_volatile_tail_openai(msgs, "") == msgs


def test_wrapper_format_matches_the_direct_lane():
    """All transports deliver the tail inside the SAME <system-reminder> wrapper,
    so prompt-pack prose about injected state holds regardless of runtime."""
    from core.llm import place_volatile_tail
    direct_msgs, placed = place_volatile_tail(
        [{"role": "user", "content": [{"type": "text", "text": "q"}]}], "TAIL")
    assert placed
    direct_text = direct_msgs[-1]["content"][-1]["text"]
    openai_text = place_volatile_tail_openai([], "TAIL")[-1]["content"]
    assert direct_text == openai_text == "<system-reminder>\nTAIL\n</system-reminder>"


def test_sdk_lane_tail_is_last_in_source_order():
    """The SDK lane streams query frames; the volatile-tail frame must be
    yielded AFTER the history frames. Structural check against the source (the
    stream builder is a closure, not directly invokable): the history loop must
    precede the catalog_msg yield inside _msg_stream."""
    src = (ROOT / "backend/core/runtime/llm_runtime_sdk.py").read_text()
    body = src.split("async def _msg_stream():", 1)[1].split("async def", 1)[0]
    hist_pos = body.find("for msg in history")
    tail_pos = body.find("catalog_msg is not None")
    assert hist_pos != -1 and tail_pos != -1
    assert hist_pos < tail_pos, \
        "SDK lane yields the volatile tail BEFORE the conversation — prefix poison"
