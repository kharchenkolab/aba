import json
import asyncio
from typing import AsyncGenerator

from config import SYSTEM_PROMPT
from db import append_message, get_messages, WORKSPACE_ID
from tools import TOOL_SCHEMAS, execute_tool
from llm import make_open_stream
from context import focus_preamble
from registry import register_artifacts_from_tool_result

open_stream = make_open_stream()


def _api_messages(history: list) -> list:
    return [{"role": m["role"], "content": m["content"]} for m in history]


async def stream_response(
    user_text: str,
    *,
    entity_id: str = WORKSPACE_ID,
) -> AsyncGenerator[str, None]:
    """
    Append user message to the entity's thread, run the Guide loop, stream SSE.

    SSE event format (each line is a JSON string prefixed with "data: "):
      {"type": "delta",       "text": "..."}
      {"type": "tool_start",  "name": "...", "input": {...}}
      {"type": "tool_result", "name": "...", "result": {...}}
      {"type": "entity_registered", "entity": {...}}
      {"type": "done"}
      {"type": "error",       "text": "..."}
    """
    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    user_blocks = [{"type": "text", "text": user_text}]
    append_message("user", user_blocks, entity_id=entity_id)

    history = get_messages(entity_id)

    # Focused-entity preamble in front of the canonical system prompt.
    system = focus_preamble(entity_id) + SYSTEM_PROMPT

    # An analysis entity will be lazily created the first time this turn
    # produces an artifact, and reused for any subsequent artifacts from the
    # same turn.
    analysis_ctx: dict = {"analysis_id": None, "turn_index": 0}

    try:
        while True:
            with open_stream(history, TOOL_SCHEMAS, system) as stream:
                for event in stream:
                    etype = event.type
                    if etype == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield sse({"type": "delta", "text": delta.text})

                final_msg = stream.get_final_message()

            assistant_blocks = []
            for block in final_msg.content:
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })

            append_message("assistant", assistant_blocks, entity_id=entity_id)
            history = get_messages(entity_id)

            if final_msg.stop_reason != "tool_use":
                break

            tool_result_blocks = []
            for block in final_msg.content:
                if block.type != "tool_use":
                    continue

                tool_name = block.name
                tool_input = block.input

                yield sse({"type": "tool_start", "name": tool_name, "input": tool_input})

                loop = asyncio.get_event_loop()
                result_str = await loop.run_in_executor(
                    None, execute_tool, tool_name, tool_input
                )
                result_obj = json.loads(result_str)

                # Auto-register any artifacts this tool produced.
                new_entities = register_artifacts_from_tool_result(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    result_obj=result_obj,
                    focused_entity_id=entity_id,
                    analysis_ctx=analysis_ctx,
                )
                for ent in new_entities:
                    yield sse({"type": "entity_registered", "entity": ent})

                yield sse({"type": "tool_result", "name": tool_name, "result": result_obj})

                tool_result_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                })

            append_message("user", tool_result_blocks, entity_id=entity_id)
            history = get_messages(entity_id)

        yield sse({"type": "done"})

    except Exception as e:
        yield sse({"type": "error", "text": str(e)})
        yield sse({"type": "done"})
