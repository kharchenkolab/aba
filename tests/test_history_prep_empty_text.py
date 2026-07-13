"""Regression (2026-07-12): the Anthropic API rejects empty text blocks ("text content
blocks must be non-empty"). api_messages must drop them (they appear as the bare leading
text of a tool_use-only assistant turn — ask_clarification/plan halt — and 400 the
resume), and never emit a message with empty content.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.history_prep import drop_empty_text_blocks, api_messages   # noqa: E402


def test_drop_empty_text_blocks():
    assert drop_empty_text_blocks([{"type": "text", "text": ""},
                                   {"type": "tool_use", "id": "t", "name": "x", "input": {}}]) == \
        [{"type": "tool_use", "id": "t", "name": "x", "input": {}}]
    assert drop_empty_text_blocks([{"type": "text", "text": "   \n"}]) == []
    assert drop_empty_text_blocks([{"type": "text", "text": "hi"}]) == [{"type": "text", "text": "hi"}]
    assert drop_empty_text_blocks("a plain string") == "a plain string"


def test_api_messages_strips_empty_text_keeps_tool_use():
    hist = [{"role": "assistant", "content": [
        {"type": "text", "text": ""},
        {"type": "tool_use", "id": "t1", "name": "ask_clarification", "input": {"question": "?"}}]}]
    out = api_messages(hist)
    assert out[0]["content"] == [
        {"type": "tool_use", "id": "t1", "name": "ask_clarification", "input": {"question": "?"}}]


def test_api_messages_never_emits_empty_content():
    hist = [{"role": "assistant", "content": [{"type": "text", "text": ""}]}]
    out = api_messages(hist)
    assert out[0]["content"] and out[0]["content"][0]["text"].strip()   # non-empty placeholder


def test_api_messages_no_empty_text_reaches_api():
    hist = [
        {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "text", "text": ""},
                                          {"type": "tool_use", "id": "t", "name": "x", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "ok"},
                                     {"type": "text", "text": ""}]},
    ]
    for m in api_messages(hist):
        for b in m["content"]:
            if isinstance(b, dict) and b.get("type") == "text":
                assert (b.get("text") or "").strip(), f"empty text block leaked: {m}"
