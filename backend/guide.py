import json
import asyncio
from typing import AsyncGenerator
import anthropic
from config import API_KEY, MODEL, SYSTEM_PROMPT
from db import append_message, get_all_messages
from tools import TOOL_SCHEMAS, execute_tool

client = anthropic.Anthropic(api_key=API_KEY)


def _api_messages(history: list) -> list:
    """Strip our 'ts' field; return role+content only."""
    return [{"role": m["role"], "content": m["content"]} for m in history]


async def stream_response(user_text: str) -> AsyncGenerator[str, None]:
    """
    Append user message, run the Guide loop (potentially multiple tool call rounds),
    stream SSE-formatted lines back.

    SSE event format (each line is a JSON string prefixed with "data: "):
      {"type": "delta",       "text": "..."}
      {"type": "tool_start",  "name": "...", "input": {...}}
      {"type": "tool_result", "name": "...", "result": {...}}
      {"type": "done"}
      {"type": "error",       "text": "..."}
    """
    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    # Persist user message
    user_blocks = [{"type": "text", "text": user_text}]
    append_message("user", user_blocks)

    history = get_all_messages()

    try:
        while True:
            # Collect full assistant response from this round
            assistant_blocks = []
            text_acc = ""
            tool_calls = []  # list of {id, name, input_json_acc}

            # Open streaming call
            with client.messages.stream(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=TOOL_SCHEMAS,
                messages=_api_messages(history),
            ) as stream:
                for event in stream:
                    etype = event.type

                    if etype == "content_block_start":
                        block = event.content_block
                        if block.type == "text":
                            pass  # will accumulate via deltas
                        elif block.type == "tool_use":
                            tool_calls.append({
                                "id": block.id,
                                "name": block.name,
                                "input_acc": ""
                            })

                    elif etype == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            text_acc += delta.text
                            yield sse({"type": "delta", "text": delta.text})
                        elif delta.type == "input_json_delta":
                            if tool_calls:
                                tool_calls[-1]["input_acc"] += delta.partial_json

                    elif etype == "content_block_stop":
                        # If we just finished a text block, record it
                        if text_acc and not tool_calls:
                            # still accumulating; handled at message_stop
                            pass

                    elif etype == "message_stop":
                        pass

                # After stream ends, get the final message
                final_msg = stream.get_final_message()

            # Build assistant content blocks for DB
            for block in final_msg.content:
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input
                    })

            append_message("assistant", assistant_blocks)
            history = get_all_messages()

            stop_reason = final_msg.stop_reason
            if stop_reason != "tool_use":
                break

            # Execute all requested tools
            tool_result_blocks = []
            for block in final_msg.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input

                yield sse({"type": "tool_start", "name": tool_name, "input": tool_input})

                # Run in thread pool to avoid blocking event loop
                loop = asyncio.get_event_loop()
                result_str = await loop.run_in_executor(
                    None, execute_tool, tool_name, tool_input
                )
                result_obj = json.loads(result_str)

                yield sse({"type": "tool_result", "name": tool_name, "result": result_obj})

                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str
                })

            # Persist tool results as a user message (Claude API convention)
            append_message("user", tool_result_blocks)
            history = get_all_messages()

        yield sse({"type": "done"})

    except Exception as e:
        yield sse({"type": "error", "text": str(e)})
        yield sse({"type": "done"})
