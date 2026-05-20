import json
import asyncio
from typing import AsyncGenerator

from config import SYSTEM_PROMPT, FAKE_SESSION
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
from jobs import submit_python_job

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
    annotation_image: str | None = None,
    annotation_note: str | None = None,
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
    if annotation_note:
        # Persist a small marker so later turns know a region was discussed
        # (we don't store the image itself — it'd bloat the DB).
        user_blocks.append({"type": "text", "text": f"[{annotation_note}]"})
    append_message("user", user_blocks,
                   entity_id=WORKSPACE_ID, focus_entity_id=focus_entity_id)
    history = get_messages(WORKSPACE_ID)

    # Vision: inject the annotated figure into the last user turn for THIS
    # call only (not persisted). Skipped in fake mode (no vision).
    if annotation_image and not FAKE_SESSION and history:
        history = list(history)
        last = history[-1]
        history[-1] = {
            **last,
            "content": list(last["content"]) + [{
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": annotation_image,
                },
            }],
        }

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

                # Background path: submit a job and return immediately.
                if tool_name == "run_python" and isinstance(tool_input, dict) \
                        and tool_input.get("background"):
                    job = submit_python_job(
                        code=tool_input.get("code", ""),
                        title=tool_input.get("title") or "Background analysis",
                        focus_entity_id=focus_entity_id,
                        timeout_s=int(tool_input.get("timeout_s") or 300),
                    )
                    result_obj = {
                        "job_id": job["id"],
                        "status": "queued",
                        "note": "Submitted as a background job. Figures will register when it finishes; watch the Queues panel.",
                    }
                    yield sse({"type": "job_submitted", "job": job})
                    yield sse({"type": "tool_result", "name": tool_name, "result": result_obj})
                    tool_result_blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result_obj),
                    })
                    continue

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

                # create_scenario builds its entity inside the tool — surface it
                # to the tree as an entity_registered event.
                if tool_name == "create_scenario" and isinstance(result_obj, dict) \
                        and result_obj.get("scenario"):
                    from db import get_entity as _ge
                    ent = _ge(result_obj["scenario"]["id"])
                    if ent:
                        yield sse({"type": "entity_registered", "entity": ent})

                # Methodologist reviews the run's methods, asynchronously.
                if new_entities and analysis_ctx.get("analysis_id"):
                    from advisors import methodologist_review
                    aid = analysis_ctx["analysis_id"]
                    asyncio.get_event_loop().run_in_executor(
                        None, methodologist_review, aid
                    )

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
