import json
import asyncio
from typing import AsyncGenerator

from config import SYSTEM_PROMPT
from db import (
    append_message, get_messages, get_entity, WORKSPACE_ID,
    log_context_assembly, session_assembly_summary,
    add_context_suggestion,
)
from tools import TOOL_SCHEMAS, execute_tool
from llm import make_open_stream
from context import focus_preamble_with_fields
from registry import register_artifacts_from_tool_result
from adaptive import new_session_id, maybe_reflect

open_stream = make_open_stream()


# One session per stream_response call. The reflection prompt fires at the
# end of the call if enough tool work happened. A longer-lived session
# concept (multi-message) can come later.
def _api_messages(history: list) -> list:
    return [{"role": m["role"], "content": m["content"]} for m in history]


async def stream_response(
    user_text: str,
    *,
    focus_entity_id: str = WORKSPACE_ID,
) -> AsyncGenerator[str, None]:
    """
    Append user message to the workspace thread, run the Guide loop, stream SSE.
    See aba_arch2.md §2.3 for the focus context model.
    """
    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    session_id = new_session_id()
    turn_index = 0

    user_blocks = [{"type": "text", "text": user_text}]
    append_message("user", user_blocks,
                   entity_id=WORKSPACE_ID, focus_entity_id=focus_entity_id)
    history = get_messages(WORKSPACE_ID)

    focus_text, fields_preloaded = focus_preamble_with_fields(focus_entity_id)
    system = focus_text + SYSTEM_PROMPT
    entity_id = WORKSPACE_ID

    focus_ent = get_entity(focus_entity_id) if focus_entity_id else None
    focus_type = focus_ent["type"] if focus_ent else None

    analysis_ctx: dict = {"analysis_id": None, "turn_index": 0}

    try:
        while True:
            with open_stream(history, TOOL_SCHEMAS, system) as stream:
                for event in stream:
                    if event.type == "content_block_delta":
                        delta = event.delta
                        if delta.type == "text_delta":
                            yield sse({"type": "delta", "text": delta.text})

                final_msg = stream.get_final_message()

            assistant_blocks = []
            text_out = ""
            tool_calls_this_turn: list[str] = []
            for block in final_msg.content:
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                    text_out += block.text
                elif block.type == "tool_use":
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": block.input,
                    })
                    tool_calls_this_turn.append(block.name)

            append_message("assistant", assistant_blocks,
                           entity_id=entity_id, focus_entity_id=focus_entity_id)

            # Log this turn's context assembly.
            log_context_assembly(
                session_id=session_id,
                turn_index=turn_index,
                focus_entity_id=focus_entity_id,
                focus_entity_type=focus_type,
                fields_preloaded=fields_preloaded,
                tool_calls=tool_calls_this_turn,
                turn_text_len=len(text_out),
            )
            turn_index += 1

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

                new_entities = register_artifacts_from_tool_result(
                    tool_name=tool_name,
                    tool_input=tool_input,
                    result_obj=result_obj,
                    focused_entity_id=focus_entity_id,
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

            append_message("user", tool_result_blocks,
                           entity_id=entity_id, focus_entity_id=focus_entity_id)
            history = get_messages(entity_id)

        # End-of-session reflection.
        summary = session_assembly_summary(session_id)
        suggestion = maybe_reflect(
            session_id=session_id,
            focus_entity_type=focus_type,
            total_tool_calls=summary["total_tool_calls"],
            history=history,
        )
        if suggestion:
            add_context_suggestion(
                session_id=session_id,
                entity_type=focus_type,
                trigger="end_of_session",
                suggestion=suggestion,
            )
            yield sse({"type": "suggestion_logged",
                       "trigger": "end_of_session",
                       "entity_type": focus_type})

        yield sse({"type": "done"})

    except Exception as e:
        yield sse({"type": "error", "text": str(e)})
        yield sse({"type": "done"})
