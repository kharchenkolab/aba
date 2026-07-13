"""Codex/ChatGPT subscription runtime — the Responses-API translation
(Anthropic history/tools → Responses input/tools) + responses-mode detection.
Live end-to-end (real subscription) verified separately; these guard the shapes."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.runtime.llm_runtime_openai import (  # noqa: E402
    _history_to_responses_input, _tools_to_responses, OpenAICompatibleRuntime)


def test_tools_to_responses_flat_shape():
    tools = [{"name": "run_python", "description": "run code",
              "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}}}]
    out = _tools_to_responses(tools)
    assert out == [{"type": "function", "name": "run_python", "description": "run code",
                    "parameters": {"type": "object", "properties": {"code": {"type": "string"}}}}]
    # nameless / malformed entries dropped
    assert _tools_to_responses([{"description": "x"}, None]) == []


def test_history_text_and_toolcall_translation():
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "let me run it"},
            {"type": "tool_use", "id": "call_1", "name": "run_python", "input": {"code": "6*7"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1", "content": "42"},
        ]},
    ]
    items = _history_to_responses_input(history)
    # user text → input_text
    assert items[0] == {"role": "user", "content": [{"type": "input_text", "text": "hello"}]}
    # assistant text → output_text (flushed before the function_call)
    assert items[1] == {"role": "assistant", "content": [{"type": "output_text", "text": "let me run it"}]}
    # tool_use → function_call with json arguments
    assert items[2] == {"type": "function_call", "call_id": "call_1",
                        "name": "run_python", "arguments": '{"code": "6*7"}'}
    # tool_result → function_call_output (top-level, no role)
    assert items[3] == {"type": "function_call_output", "call_id": "call_1", "output": "42"}


def test_tool_result_list_content_flattened():
    history = [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]},
    ]}]
    out = _history_to_responses_input(history)
    assert out[0]["output"] == "ab"


def test_responses_mode_detection():
    codex = OpenAICompatibleRuntime(base_url="https://chatgpt.com/backend-api/codex", api_key="sk-x")
    api = OpenAICompatibleRuntime(base_url="https://api.openai.com/v1", api_key="sk-x")
    vllm = OpenAICompatibleRuntime(base_url="http://localhost:8001/v1", api_key="none")
    assert codex._responses_mode is True and codex._real_openai is True
    assert api._responses_mode is False and api._real_openai is True
    assert vllm._responses_mode is False and vllm._real_openai is False


if __name__ == "__main__":
    test_tools_to_responses_flat_shape(); print("ok  tools → responses flat shape")
    test_history_text_and_toolcall_translation(); print("ok  history text + tool-call translation")
    test_tool_result_list_content_flattened(); print("ok  tool_result list content flattened")
    test_responses_mode_detection(); print("ok  responses-mode detection")
    print("all responses-translate tests passed")
