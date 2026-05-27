import json
import asyncio
from typing import AsyncGenerator

from config import FAKE_SESSION
from content.bio.prompts.build import build_system
from core.graph._schema import WORKSPACE_ID
from core.graph.audit import log_context_assembly, session_assembly_summary, add_context_suggestion
from core.graph.entities import get_entity, update_entity
from core.graph.messages import append_message, get_messages
from core.graph.threads import get_or_create_default_thread
from content.bio.tools import TOOL_SCHEMAS, execute_tool
from core.llm import make_open_stream
from core.manifest.assembler import build_manifest, render_focus_preamble
import content.bio.cards  # noqa: F401  — registers per-type card builders
from core.hooks.dispatcher import dispatch
from core.runtime.turn import Turn, TurnState, gen_run_id
from core.runtime.checkpoint import checkpoint
from content.bio.lifecycle.adaptive import new_session_id
# Bio modules registering hook handlers at import — keep these imports even
# though their names aren't used directly: the side effect is registration.
import content.bio.lifecycle.registry  # noqa: F401  — on_post_tool: register artifacts
import advisors  # noqa: F401          — on_post_tool: methodologist trigger
import content.bio.lifecycle.adaptive  # noqa: F401  — on_stop: maybe_reflect
import content.bio.proposals.scheduler  # noqa: F401 — on_stop: evaluate_thread
from core.jobs.runner import submit_python_job
from core.summarize.rolling import effective_history

open_stream = make_open_stream()


# One session per stream_response call. The reflection prompt fires at the
# end of the call if enough tool work happened. A longer-lived session
# concept (multi-message) can come later.
def _api_messages(history: list) -> list:
    return [{"role": m["role"], "content": m["content"]} for m in history]


def _repair_tool_pairs(messages: list) -> list:
    """Anthropic requires every assistant `tool_use` to be followed by a user
    message containing the matching `tool_result`. An interrupted run (crash,
    transient error, client disconnect) can persist a `tool_use` without its
    result, which then poisons every subsequent request. Repair the history
    in-flight by injecting a synthetic tool_result for any unmatched tool_use."""
    out = [dict(m, content=list(m.get("content") or [])) for m in messages]
    i = 0
    while i < len(out):
        m = out[i]
        if m["role"] == "assistant":
            tool_ids = [b["id"] for b in m["content"]
                        if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")]
            if tool_ids:
                nxt = out[i + 1] if i + 1 < len(out) else None
                present = set()
                if nxt and nxt["role"] == "user":
                    present = {b.get("tool_use_id") for b in nxt["content"]
                               if isinstance(b, dict) and b.get("type") == "tool_result"}
                missing = [tid for tid in tool_ids if tid not in present]
                if missing:
                    synth = [{"type": "tool_result", "tool_use_id": tid,
                              "content": "[tool result unavailable — the run was interrupted]"}
                             for tid in missing]
                    if nxt and nxt["role"] == "user":
                        nxt["content"] = synth + nxt["content"]
                    else:
                        out.insert(i + 1, {"role": "user", "content": synth})
        i += 1
    return out


# Transient API conditions worth retrying: 429 (rate limit), 5xx, 529
# (overloaded), and connection/timeouts. We match on the SDK's status code
# when present and fall back to a string check.
_TRANSIENT_TOKENS = ("overloaded", "rate_limit", "timeout", "connection",
                     "502", "503", "504", "529", "500")


def _is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in (408, 429, 500, 502, 503, 504, 529):
        return True
    return any(tok in str(exc).lower() for tok in _TRANSIENT_TOKENS)


def _friendly_error(exc: Exception) -> str:
    s = str(exc).lower()
    if "overloaded" in s or getattr(exc, "status_code", None) == 529:
        return ("The model is overloaded right now and didn't respond after a "
                "few retries. Please try again in a moment.")
    if "rate_limit" in s or getattr(exc, "status_code", None) == 429:
        return "Hit the model's rate limit. Please wait a moment and try again."
    if "timeout" in s or "connection" in s:
        return "Lost the connection to the model. Please try again."
    return "Something went wrong talking to the model. Please try again."


def _derive_thread_title(text: str) -> str:
    """Heuristic thread title from the opening message (no LLM — Phase 1).
    LLM-quality naming is deferred to Phase 4."""
    t = " ".join((text or "").strip().split())
    for sep in (". ", "? ", "! ", "\n"):
        if sep in t:
            t = t.split(sep)[0]
            break
    t = t[:48].rstrip()
    return (t[:1].upper() + t[1:]) if t else "Investigation"


async def stream_response(
    user_text: str,
    *,
    focus_entity_id: str = WORKSPACE_ID,
    thread_id: str = "default",
    annotation_image: str | None = None,
    annotation_note: str | None = None,
    retry: bool = False,
) -> AsyncGenerator[str, None]:
    """
    Append user message to the workspace thread, run the Guide loop, stream SSE.
    See aba_arch2.md §2.3 for the focus context model.

    `retry=True` regenerates the reply for the existing last turn without
    appending a new user message — used after a transient API failure, where
    the user turn was already persisted but no assistant reply was produced.
    """
    def sse(obj: dict) -> str:
        return f"data: {json.dumps(obj)}\n\n"

    session_id = new_session_id()
    turn_index = 0
    # Turn checkpointing (Pass E): create a Turn row at the start; update
    # state through transitions; mark DONE/FAILED at the end. Lets resume-
    # after-restart see what was in flight. The state machine itself is
    # still the while-loop below; Pass F drives it explicitly off TurnState.
    turn = Turn(
        run_id=gen_run_id(),
        session_id=session_id,
        turn_index=0,
        agent_spec_name="guide",
        state=TurnState.GENERATING,
        focus_entity_id=focus_entity_id,
        thread_id=thread_id,
    )
    # Threads are real lines of inquiry: the Guide reasons within the current
    # thread, not the whole project firehose. "default" resolves to (and
    # materializes) the project's default thread entity.
    store_tid = get_or_create_default_thread() if thread_id == "default" else thread_id

    if not retry:
        user_blocks = [{"type": "text", "text": user_text}]
        if annotation_note:
            # Persist a small marker so later turns know a region was discussed
            # (we don't store the image itself — it'd bloat the DB).
            user_blocks.append({"type": "text", "text": f"[{annotation_note}]"})
        append_message("user", user_blocks, entity_id=WORKSPACE_ID,
                       focus_entity_id=focus_entity_id, thread_id=store_tid)
    history = get_messages(WORKSPACE_ID, thread_id=store_tid)

    # Seed a freshly created thread's title + question from its opening message
    # (heuristic; LLM-quality suggestion is Phase D). Both stay user-editable.
    if not retry and store_tid and len(history) == 1:
        thr = get_entity(store_tid)
        if thr:
            fields: dict = {}
            if thr.get("title") in ("New investigation", "Untitled investigation"):
                fields["title"] = _derive_thread_title(user_text)
            meta = dict(thr.get("metadata") or {})
            if not meta.get("question"):
                meta["question"] = " ".join(user_text.strip().split())[:200]
                meta["question_source"] = "guide"
                fields["metadata"] = meta
            if fields:
                update_entity(store_tid, **fields)

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

    # Capability set for this turn (disabled tools are neither offered nor
    # advertised), then assemble the system prompt from composable blocks.
    from core.graph.tool_settings import get_disabled_tools
    disabled = get_disabled_tools()
    active_tools = [t for t in TOOL_SCHEMAS if t["name"] not in disabled]

    manifest = build_manifest(
        session_id=session_id,
        turn_index=turn_index,
        focus_entity_id=focus_entity_id,
        thread_id=store_tid,
    )
    focus_text, fields_preloaded = render_focus_preamble(manifest)
    thread_text = manifest.thread.text if manifest.thread else ""
    system = focus_text + thread_text + build_system(active_tools)
    entity_id = WORKSPACE_ID

    focus_ent = get_entity(focus_entity_id) if focus_entity_id else None
    focus_type = focus_ent["type"] if focus_ent else None

    analysis_ctx: dict = {"analysis_id": None, "turn_index": 0}
    usage_in = usage_out = usage_cr = usage_cw = 0   # Guide tokens this turn (+cache read/write)
    turn.thread_id = store_tid
    checkpoint(turn)  # initial Turn row before the loop runs

    try:
        while True:
            turn.transition(TurnState.GENERATING); checkpoint(turn)
            llm_history = _repair_tool_pairs(effective_history(WORKSPACE_ID, history))

            # Open + consume the stream, retrying transient API failures
            # (e.g. 529 overloaded) with exponential backoff. We only retry
            # while no text has been emitted this turn — otherwise a retry
            # would duplicate the partial reply.
            final_msg = None
            attempt = 0
            max_retries = 4
            while True:
                emitted = False
                try:
                    with open_stream(llm_history, active_tools, system) as stream:
                        for event in stream:
                            if event.type == "content_block_delta":
                                delta = event.delta
                                if delta.type == "text_delta":
                                    emitted = True
                                    yield sse({"type": "delta", "text": delta.text})
                        final_msg = stream.get_final_message()
                    if getattr(final_msg, "usage", None):
                        u = final_msg.usage
                        usage_in += u.input_tokens or 0
                        usage_out += u.output_tokens or 0
                        usage_cr += getattr(u, "cache_read_input_tokens", 0) or 0
                        usage_cw += getattr(u, "cache_creation_input_tokens", 0) or 0
                    break
                except Exception as e:
                    if emitted or attempt >= max_retries or not _is_transient(e):
                        raise
                    attempt += 1
                    backoff = min(2 ** attempt, 8)
                    print(f"[guide] transient API error (attempt {attempt}/{max_retries}), "
                          f"retrying in {backoff}s: {e}")
                    yield sse({"type": "notice",
                               "text": f"Model is busy — retrying ({attempt}/{max_retries})…"})
                    await asyncio.sleep(backoff)

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

            append_message("assistant", assistant_blocks, entity_id=entity_id,
                           focus_entity_id=focus_entity_id, thread_id=store_tid)

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
            turn.turn_index = turn_index
            turn.usage_in = usage_in; turn.usage_out = usage_out
            turn.usage_cache_read = usage_cr; turn.usage_cache_write = usage_cw

            history = get_messages(entity_id, thread_id=store_tid)

            if final_msg.stop_reason != "tool_use":
                break

            turn.transition(TurnState.EXECUTING_TOOLS); checkpoint(turn)
            tool_result_blocks = []
            halt_for_plan = False
            for block in final_msg.content:
                if block.type != "tool_use":
                    continue
                tool_name = block.name
                tool_input = block.input

                # present_plan: surface the plan to the UI and HALT the turn —
                # the user approves ("go") or adjusts, and their next message
                # resumes. We still record an ack tool_result so history stays
                # well-formed (no dangling tool_use).
                if tool_name == "present_plan":
                    inp = tool_input if isinstance(tool_input, dict) else {}
                    raw_steps = inp.get("steps")
                    if isinstance(raw_steps, list):
                        steps = [str(s) for s in raw_steps if str(s).strip()]
                    elif isinstance(raw_steps, str) and raw_steps.strip():
                        steps = [ln.strip() for ln in raw_steps.splitlines() if ln.strip()]
                    else:
                        steps = []
                    yield sse({"type": "plan", "title": inp.get("title"),
                               "steps": steps, "rationale": inp.get("rationale")})
                    ack = {"status": "presented",
                           "note": "Plan shown to the user with Go/Adjust controls. "
                                   "Wait for their decision before executing."}
                    tool_result_blocks.append({"type": "tool_result", "tool_use_id": block.id,
                                               "content": json.dumps(ack)})
                    halt_for_plan = True
                    continue

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

                # Post-tool hook: bio's registry handler adds new entities
                # under ctx['new_entities']; advisors' methodologist handler
                # may fire the Methodologist asynchronously.
                hook_ctx = {
                    "tool_name": tool_name,
                    "tool_input": tool_input,
                    "result_obj": result_obj,
                    "focus_entity_id": focus_entity_id,
                    "analysis_ctx": analysis_ctx,
                    "thread_id": store_tid,
                    "new_entities": [],
                }
                dispatch("on_post_tool", hook_ctx)
                for ent in hook_ctx["new_entities"]:
                    yield sse({"type": "entity_registered", "entity": ent})

                # create_scenario builds its entity inside the tool — surface it
                # to the tree as an entity_registered event. (The scenario tool
                # doesn't go through the artifact registrar, so this stays inline.)
                if tool_name == "create_scenario" and isinstance(result_obj, dict) \
                        and result_obj.get("scenario"):
                    from core.graph.entities import get_entity as _ge
                    ent = _ge(result_obj["scenario"]["id"])
                    if ent:
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

            # A presented plan ends the turn: stop and wait for the user's
            # Go/Adjust rather than executing the steps now.
            if halt_for_plan:
                turn.transition(TurnState.AWAITING_USER)
                turn.pending_user_signal = "plan"
                checkpoint(turn)
                break

        # End-of-turn hooks: reflection (bio.adaptive) + proposals
        # evaluation (bio.proposals.scheduler). Handlers may set
        # ctx['suggestion']; if they do, surface as an SSE event.
        turn.transition(TurnState.SUMMARIZING); checkpoint(turn)
        summary = session_assembly_summary(session_id)
        stop_ctx = {
            "session_id": session_id,
            "focus_entity_type": focus_type,
            "focus_entity_id": focus_entity_id,
            "total_tool_calls": summary["total_tool_calls"],
            "history": history,
            "thread_id": store_tid,
            "suggestion": None,
        }
        dispatch("on_stop", stop_ctx)
        if stop_ctx.get("suggestion"):
            add_context_suggestion(
                session_id=session_id,
                entity_type=focus_type,
                trigger="end_of_session",
                suggestion=stop_ctx["suggestion"],
            )
            yield sse({"type": "suggestion_logged",
                       "trigger": "end_of_session",
                       "entity_type": focus_type})

        if turn.state != TurnState.AWAITING_USER:
            turn.transition(TurnState.DONE)
            checkpoint(turn)
        yield sse({"type": "usage", "input": usage_in, "output": usage_out,
                   "cache_read": usage_cr, "cache_write": usage_cw})
        yield sse({"type": "done"})

    except Exception as e:
        print(f"[guide] stream_response failed: {type(e).__name__}: {e}")
        turn.error = {"type": type(e).__name__, "message": str(e)}
        turn.transition(TurnState.FAILED)
        checkpoint(turn)
        yield sse({"type": "error", "text": _friendly_error(e),
                   "detail": f"{type(e).__name__}: {e}"})
        yield sse({"type": "usage", "input": usage_in, "output": usage_out,
                   "cache_read": usage_cr, "cache_write": usage_cw})
        yield sse({"type": "done"})
