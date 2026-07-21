"""Regression (2026-07-12): the message-side caching breakpoints must NOT put
cache_control on an empty text block — Anthropic 400s ("cache_control cannot be
set for empty text blocks"). Hit when a message ends in a bare text block after
an ask_clarification/plan halt+resume. See core/llm._mark_history_cached
(marks the last block of the last TWO user messages; assistant messages are
never marked).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.llm import _mark_history_cached          # noqa: E402


def _cc(block):
    return "cache_control" in block


def test_empty_text_block_not_marked():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "user", "content": [{"type": "text", "text": ""}]}]
    out = _mark_history_cached(msgs)
    assert not _cc(out[-1]["content"][-1])              # empty → skipped (no 400)
    assert _cc(out[0]["content"][-1])                   # the earlier anchor still marks


def test_whitespace_text_block_not_marked():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "   \n"}]}]
    assert not _cc(_mark_history_cached(msgs)[-1]["content"][-1])


def test_nonempty_text_block_is_marked():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "please continue"}]}]
    assert _cc(_mark_history_cached(msgs)[-1]["content"][-1])


def test_tool_result_block_is_marked():
    msgs = [{"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}]
    assert _cc(_mark_history_cached(msgs)[-1]["content"][-1])


def test_last_two_user_messages_marked_assistant_never():
    msgs = [{"role": "user", "content": [{"type": "text", "text": "q1"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            {"role": "user", "content": [{"type": "text", "text": "q2"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "a2"}]},
            {"role": "user", "content": [{"type": "text", "text": "q3"}]}]
    out = _mark_history_cached(msgs)
    marked = [i for i, m in enumerate(out) if _cc(m["content"][-1])]
    assert marked == [2, 4], "exactly the last TWO user messages carry marks"


def test_empty_messages_noop():
    assert _mark_history_cached([]) == []
    assert _mark_history_cached([{"role": "user", "content": []}])[-1]["content"] == []
