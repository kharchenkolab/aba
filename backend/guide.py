import json
import asyncio
from typing import AsyncGenerator

from config import FAKE_SESSION
from content.bio.prompts.build import build_system, build_recipes_reminder
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
import content.bio.advisors  # noqa: F401  — registers handlers + specs
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


def _is_interrupted_fill(block: dict) -> bool:
    """A reaper-synthesized 'interrupted' tool_result (vs a real one)."""
    c = block.get("content")
    if isinstance(c, str):
        try:
            j = json.loads(c)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(j, dict) and j.get("status") == "interrupted"
    return False


def _dedup_tool_results(messages: list) -> list:
    """Collapse multiple tool_result blocks that share a tool_use_id down to
    one — the Anthropic API rejects duplicates ("each tool_use must have a
    single result"). Duplicates arise when a reaper 'interrupted' synth fill
    coexists with a real result on a long/shared thread (or a resume race).
    Prefer the REAL result over an interrupted synth; otherwise keep the first.
    Idempotent; drops messages left empty."""
    real_ids = {
        b.get("tool_use_id")
        for m in messages for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_result" and not _is_interrupted_fill(b)
    }
    seen: set = set()
    out = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        new_content = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tid = b.get("tool_use_id")
                if tid in seen:
                    continue                      # already kept one for this id
                if tid in real_ids and _is_interrupted_fill(b):
                    continue                      # skip synth; a real result exists elsewhere
                seen.add(tid)
            new_content.append(b)
        if new_content:
            out.append(dict(m, content=new_content))
    return out


def _splice_recipes_reminder(messages: list, reminder: str) -> list:
    """CC-convergence Phase 4: prepend the recipes catalog (wrapped in
    `<system-reminder>` tags) to the latest user-text message of the
    LLM payload. No-op when:
      • `reminder` is empty (registry has no `visibility='local'` skills);
      • the latest message isn't a user-text (i.e. it's a tool_result from
        an in-progress agent loop — the model already saw the catalog at
        the start of this turn, no need to re-show);
      • messages is empty.

    Does not mutate the input list. The injected text block is transient
    (only in the outgoing payload), so we don't have to strip it from
    history later — history doesn't see it."""
    if not reminder or not messages:
        return messages
    last = messages[-1]
    if last.get("role") != "user":
        return messages
    content = last.get("content")
    if isinstance(content, str):
        # Legacy/simple shape: a plain string. Promote to a blocks list with
        # the reminder prepended so we don't lose the user text.
        new_content = [
            {"type": "text", "text": reminder},
            {"type": "text", "text": content},
        ]
    elif isinstance(content, list):
        # Block shape. Only inject if there's a user-text block — refuse to
        # touch tool_result-only messages (mid-loop turns).
        has_text = any(isinstance(b, dict) and b.get("type") == "text" for b in content)
        if not has_text:
            return messages
        new_content = [{"type": "text", "text": reminder}, *content]
    else:
        return messages
    out = list(messages[:-1])
    out.append({**last, "content": new_content})
    return out


def _ensure_tool_pair_completeness(messages: list) -> list:
    """In-memory safety net for the Anthropic API contract that every
    assistant `tool_use` block must be followed by a user `tool_result`
    for the same id, and exactly one.

    A1 made the Turn state machine track pending_tool_ids and the
    reaper writes synthetic tool_results to the message log for crashes
    that left trailing orphans (the common case). This shim still
    handles 'middle orphans' — adjacent assistant→assistant messages in
    seeded/legacy history where the result was never written between
    them. The append-only messages table can't cleanly insert in the
    middle; doing the patch in-memory at request time is the pragmatic
    answer.

    Idempotent; doesn't mutate the input list."""
    # First collapse any duplicate tool_results (dedup may empty a message and
    # create a fresh middle-orphan, which the fill pass below then repairs).
    messages = _dedup_tool_results(messages)
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
                    # JSON-encoded content matches the persistent reaper's
                    # shape, so the frontend filter recognizes both via
                    # ORPHAN_FILL_MARKER / status=='interrupted'.
                    import json as _json
                    interrupted = _json.dumps({
                        "status": "interrupted",
                        "note": "The previous tool call did not complete (run was interrupted).",
                    })
                    synth = [{"type": "tool_result", "tool_use_id": tid,
                              "content": interrupted}
                             for tid in missing]
                    if nxt and nxt["role"] == "user":
                        nxt["content"] = synth + nxt["content"]
                    else:
                        out.insert(i + 1, {"role": "user", "content": synth})
        i += 1

    # Second pass — BACKWARD orphans: a `tool_result` must be preceded by an
    # assistant message carrying the matching `tool_use`. A rolling-summary cut
    # between an assistant tool_use and its tool_result, or a cancel/error that
    # dropped the assistant turn, leaves a leading/orphaned tool_result the API
    # rejects ("tool_result … without a corresponding tool_use"). Drop those.
    repaired: list = []
    for m in out:
        if m["role"] == "user" and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in m["content"]):
            prev = repaired[-1] if repaired else None
            valid_ids = set()
            if prev and prev["role"] == "assistant":
                valid_ids = {b["id"] for b in prev["content"]
                             if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")}
            kept = [b for b in m["content"]
                    if not (isinstance(b, dict) and b.get("type") == "tool_result"
                            and b.get("tool_use_id") not in valid_ids)]
            if not kept:
                continue   # message was only orphaned tool_results — drop it
            m = dict(m, content=kept)
        repaired.append(m)
    return repaired


_CONFIRM_WORDS = {
    "yes", "y", "go", "ok", "okay", "sure", "proceed", "continue", "do it", "go ahead",
    "yes go ahead", "yes please", "yep", "please do", "run it", "sounds good", "go for it",
    "yes, go ahead", "do that", "please proceed",
}


def _effective_intent(user_text: str, history: list) -> str:
    """The intent used to rank the in-prompt recipe slice. On a resume/confirmation
    ('Yes, go ahead') the literal user_text carries no task signal — fall back to
    the last substantive user message so the slice still reflects the real task
    (otherwise the resume turn gets an irrelevant slice). Normal turns return
    user_text unchanged."""
    t = (user_text or "").strip()
    if len(t) > 40 or t.lower().rstrip(".!").strip() not in _CONFIRM_WORDS:
        return user_text
    for m in reversed(history or []):
        if m.get("role") != "user":
            continue
        c = m.get("content")
        txt = c if isinstance(c, str) else " ".join(
            b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"
        ) if isinstance(c, list) else ""
        txt = (txt or "").strip()
        if len(txt) > 40 and txt.lower().rstrip(".!").strip() not in _CONFIRM_WORDS:
            return txt
    return user_text


def _dump_turn_context(run_id, *, user_text, system, history, active_tools,
                       model, thread_id, focus_entity_id) -> None:
    """Best-effort: persist the EXACT context the agent receives this turn (the
    assembled system prompt + the message history + the offered tools) to a file,
    so a human-driven run can be inspected afterward ("what did the agent actually
    see?"). One file per turn under ABA_TURN_LOG_DIR (default /tmp/aba_turnlog);
    set that env to '' / 'off' to disable. Never raises — debug aid only."""
    import os
    d = os.environ.get("ABA_TURN_LOG_DIR", "/tmp/aba_turnlog")
    if d.strip().lower() in ("", "off", "0", "false"):
        return
    try:
        import json as _json, datetime as _dt
        os.makedirs(d, exist_ok=True)
        ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
        tools = [t.get("name") for t in (active_tools or [])]
        body = "\n".join([
            f"# Turn {run_id}  ({ts})",
            f"thread={thread_id}  focus={focus_entity_id}  model={model}",
            f"tools ({len(tools)}): {', '.join(tools)}",
            "", "## USER MESSAGE", (user_text or "").strip(),
            "", "## SYSTEM PROMPT", "```", system or "", "```",
            "", "## MESSAGE HISTORY (input to the model)", "```json",
            _json.dumps(history, indent=2, default=str)[:80000], "```",
        ])
        with open(os.path.join(d, f"{ts}_{run_id}.md"), "w") as f:
            f.write(body)
        # JSON sidecar — same payload, structured, for the drawer's Context tab.
        with open(os.path.join(d, f"{ts}_{run_id}.json"), "w") as f:
            _json.dump({
                "run_id": run_id, "ts": ts, "thread_id": thread_id, "model": model,
                "focus_entity_id": focus_entity_id, "tools": tools,
                "user_text": user_text or "",
                "system": system or "",
                "history": history,
            }, f, default=str)
        # Rolling live transcript: a USER header per turn; events appended by
        # _live_log_event. Tail this file to watch any run (incl. browser) live.
        with open(os.path.join(d, "live.log"), "a") as f:
            f.write(f"\n===== {ts}  run {run_id}  (thread {thread_id}) =====\n"
                    f"👤 {(user_text or '').strip()}\n")
    except Exception:  # noqa: BLE001 — a debug dump must never break a turn
        pass


def _live_log_event(run_id, obj: dict, dtbuf: list) -> None:
    """Append one compact line per SSE event to the rolling live transcript
    (ABA_TURN_LOG_DIR/live.log), so an active run can be watched by tailing it.
    Streamed text 'delta' chunks are buffered and flushed as one line on the
    next non-delta event. Never raises."""
    import os
    d = os.environ.get("ABA_TURN_LOG_DIR", "/tmp/aba_turnlog")
    if d.strip().lower() in ("", "off", "0", "false"):
        return
    try:
        import json as _j
        os.makedirs(d, exist_ok=True)
        t = obj.get("type")
        # Full-fidelity record: append the UNTRUNCATED event (tool inputs +
        # results, plans, errors) to a per-run JSONL, so a run can be fully
        # reconstructed offline for debugging (the truncated live.log hid the
        # evidence when diagnosing the pagoda2 fabrication). 'delta' text chunks
        # are skipped — the prose is in live.log (coalesced) + the message log.
        if t and t != "delta" and t not in ("manifest", "usage", "suggestion_logged"):
            try:
                with open(os.path.join(d, f"{run_id}.jsonl"), "a") as _ff:
                    _ff.write(_j.dumps(obj, default=str) + "\n")
            except Exception:  # noqa: BLE001
                pass
        if t == "delta":
            dtbuf.append(obj.get("text") or "")
            return
        out: list[str] = []
        if dtbuf:
            txt = "".join(dtbuf).strip()
            dtbuf.clear()
            if txt:
                out.append(f"🗣  {txt}")
        if t == "tool_start":
            out.append(f"🔧 {obj.get('name')}  {_j.dumps(obj.get('input') or {}, default=str)[:220]}")
        elif t == "tool_result":
            out.append(f"   ✓ {_j.dumps(obj.get('result') or {}, default=str)[:220]}")
        elif t == "tool_progress":
            out.append(f"   ⏳ {str(obj.get('message'))[:160]}")
        elif t == "plan":
            out.append(f"📋 PLAN: {str(obj.get('title') or '')[:140]}")
        elif t in ("notice", "error", "cancelled", "clarification_pending", "approval_pending"):
            out.append(f"[{t}] {_j.dumps(obj, default=str)[:220]}")
        elif t == "entity_registered":
            e = obj.get("entity") or {}
            out.append(f"📦 {e.get('type')}: {e.get('title')}")
        elif t == "job_submitted":
            out.append(f"⚙ job: {_j.dumps(obj.get('job') or {}, default=str)[:160]}")
        elif t == "done":
            out.append("── turn done ──")
        # manifest / usage / suggestion_logged: skip (noise)
        if not out:
            return
        import datetime as _dt
        ts = _dt.datetime.now().strftime("%H:%M:%S")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "live.log"), "a") as f:
            for ln in out:
                f.write(f"{ts} {ln}\n")
    except Exception:  # noqa: BLE001
        pass


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


def _summarize_tool_input(tool_name: str, tool_input) -> str:
    """One-line summary of a tool's input for the approval bar. Tools with
    opaque inputs (run_python's code blob) get a per-tool shape; others
    get a truncated JSON repr."""
    if not isinstance(tool_input, dict):
        return repr(tool_input)[:200]
    if tool_name == "run_python":
        code = (tool_input.get("code") or "").strip()
        n_lines = code.count("\n") + 1 if code else 0
        head = code.splitlines()[0][:120] if code else ""
        return f"{n_lines} line{'s' if n_lines != 1 else ''} of Python — first line: {head!r}"
    if tool_name == "archive_entity":
        eid = tool_input.get("entity_id") or "?"
        title = "?"
        try:
            ent = get_entity(eid)
            if ent:
                title = f"{ent.get('type', '?')} '{ent.get('title', '?')}'"
        except Exception:  # noqa: BLE001
            pass
        reason = (tool_input.get("reason") or "").strip()
        return f"Archive {title}" + (f" — {reason[:160]}" if reason else "")
    parts = []
    for k, v in tool_input.items():
        s = repr(v)
        if len(s) > 80:
            s = s[:77] + "…"
        parts.append(f"{k}={s}")
    out = ", ".join(parts)
    return out[:300] + ("…" if len(out) > 300 else "")


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
    plan_entity_id: str | None = None,
    run_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """
    Append user message to the workspace thread, run the Guide loop, yield
    event dicts. See aba_arch2.md §2.3 for the focus context model.

    `retry=True` regenerates the reply for the existing last turn without
    appending a new user message — used after a transient API failure, where
    the user turn was already persisted but no assistant reply was produced.

    `run_id` should be allocated by the caller (via core.runtime.turn_executor.
    new_run_id()) so the TurnSink can be created upfront and subscribers can
    attach before the body starts emitting. If omitted (callers that don't
    care about pre-allocation), one is generated internally.

    The yielded dicts are the *payloads* — SSE wire framing happens in
    core.runtime.turn_sink.stream_from_sink (which embeds the seq from the
    sink). Callers running the body via turn_executor.start_turn never
    consume this generator directly; the executor's _drain does the pushing.
    """
    _dtbuf: list[str] = []   # buffers streamed text deltas for the live transcript
    def sse(obj: dict) -> dict:
        # The name is retained for diff-friendliness; the function no longer
        # formats SSE wire frames — it just live-logs and returns the payload.
        _live_log_event(turn.run_id, obj, _dtbuf)
        return obj

    session_id = new_session_id()
    turn_index = 0
    # A2: Guide is now spec-driven. The YAML at bio/advisors/guide.yaml
    # declares the model + role + halt/streaming flags. Full loop-body
    # extraction into Agent.run() is deferred; for now the spec is
    # consulted for model + role only, while the loop body stays here.
    from core.runtime.agent import get_agent_spec
    spec = get_agent_spec("guide")
    guide_model = spec.model if spec else None    # falls back to MODEL default

    # Turn checkpointing (Pass E): create a Turn row at the start; update
    # state through transitions; mark DONE/FAILED at the end. Lets resume-
    # after-restart see what was in flight.
    turn = Turn(
        run_id=run_id or gen_run_id(),
        session_id=session_id,
        turn_index=0,
        agent_spec_name="guide",
        state=TurnState.GENERATING,
        focus_entity_id=focus_entity_id,
        thread_id=thread_id,
        plan_entity_id=plan_entity_id,    # #160: carried forward by /resume so DONE/FAILED can transition lifecycle
    )

    # Per-turn cancellation token. Acquired here, released in the
    # finally block at the very end of stream_response. Any tool that
    # might run for a perceptible duration (subprocess, MCP call, etc.)
    # registers an interrupter against this token via tool_ctx; the
    # /api/turns/{id}/cancel endpoint fires them all.
    from core.runtime import cancellation as _cancel
    cancel_token = _cancel.acquire(turn.run_id)
    # Threads are real lines of inquiry: the Guide reasons within the current
    # thread, not the whole project firehose. "default" resolves to (and
    # materializes) the project's default thread entity.
    store_tid = get_or_create_default_thread() if thread_id == "default" else thread_id
    # C-1: the per-Turn sink is allocated by core.runtime.turn_executor.
    # start_turn BEFORE this body runs, so subscribers can attach before
    # any event is emitted. We don't create or close it here — the
    # executor's _drain owns the sink lifecycle.

    if not retry:
        # Note-FIRST ordering: when the user attached a highlight, lead
        # with the meta-note so the model's attention is framed by the
        # highlight before it reads the question. Without this ordering
        # the model treats the highlight as an afterthought and answers
        # about the broader plot.
        user_blocks: list[dict] = []
        if annotation_note:
            user_blocks.append({"type": "text", "text": annotation_note})
        user_blocks.append({"type": "text", "text": user_text})
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
    # Place the image between the leading meta-note and the user's
    # question (persisted order: [note, user_text]) → final block order:
    # [note, image, user_text]. The model reads "I marked X" → sees X →
    # then the question, framed by the highlight.
    if annotation_image and not FAKE_SESSION and history:
        history = list(history)
        last = history[-1]
        content = list(last["content"])
        # Insert before the LAST text block (the user's actual question).
        # If there's no preceding note for some reason, falls back to
        # appending at the end.
        insert_at = len(content) - 1 if len(content) >= 2 else len(content)
        content.insert(insert_at, {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": annotation_image,
            },
        })
        # Trailer reminder AFTER the user's question — combats recency bias.
        # With a short demonstrative question ("what is this?", "what are
        # these?") the model's attention pools on the last text block; the
        # opening note at position 0 can get skipped. A terse pointer to
        # "demonstratives → the mark" at the tail keeps the directive sticky
        # without absolutizing it (the question may still be about the
        # broader figure, e.g. axes / comparison — that's fine, answer
        # what's asked).
        # NOT persisted (only injected into the live history) — same model
        # as the image itself, so it doesn't leak into later turns.
        content.append({
            "type": "text",
            "text": "[Reminder: a region of the figure is yellow-marked (see "
                    "the first text block for what + where). If this question "
                    "uses 'this'/'these'/'here'/'what is this' it's about the "
                    "marked region; otherwise answer as asked.]",
        })
        history[-1] = {**last, "content": content}

    # Capability set for this turn (disabled tools are neither offered nor
    # advertised). A3: also pass through the spec's tool_allowlist so the
    # capabilities list and the tools sent to the LLM match the agent's
    # declared role. For the Guide (allowlist ('*',)) this is a no-op.
    from core.graph.tool_settings import get_disabled_tools
    from core.runtime.agent import filter_tools_by_allowlist
    disabled = get_disabled_tools()
    active_tools = [t for t in TOOL_SCHEMAS if t["name"] not in disabled]
    # P3 #1 — append tools served by MCP servers (prefixed 'server:tool').
    # Empty when no MCP server is configured/connected.
    try:
        from core.runtime.mcp import list_tools as mcp_list_tools
        active_tools.extend(t for t in mcp_list_tools() if t["name"] not in disabled)
    except Exception:  # noqa: BLE001
        pass    # gateway failure must never block normal tool dispatch
    if spec is not None:
        active_tools = filter_tools_by_allowlist(active_tools, spec.tool_allowlist)

    guide_role = spec.manifest_role if spec else "primary"
    manifest = build_manifest(
        session_id=session_id,
        turn_index=turn_index,
        focus_entity_id=focus_entity_id,
        thread_id=store_tid,
        role=guide_role,
    )
    focus_text, fields_preloaded = render_focus_preamble(manifest)
    thread_text = manifest.thread.text if manifest.thread else ""
    # Phase 1 of the history-compaction redesign (see
    # misc/history_compaction_redesign.md): inject a project-wide
    # entity snapshot at the head of the system prompt. Cheap (~5
    # SQL counts), deterministic, no LLM. Shared cross-thread state
    # lives HERE; the thread chat history stays the conversational
    # record.
    from core.manifest.assembler import render_project_sidebar
    sidebar_text = render_project_sidebar(store_tid)
    eff_intent = _effective_intent(user_text, history)
    # (the user prompt is already written to live.log as `👤 …` at run-header time;
    # no extra USER print here.)
    # Per-turn gate signals for build_system — only inject the scenarios /
    # highlighting blocks when the user is actually acting on a figure (focus is
    # a figure) or just highlighted; otherwise they're dead weight every turn.
    focus_ent = get_entity(focus_entity_id) if focus_entity_id else None
    prompt_ctx = {
        "focus_is_figure": bool(focus_ent and focus_ent.get("type") in ("figure", "view")),
        "highlight_active": bool(annotation_image),
        "thread_id": store_tid,  # for thread-scoped blocks (e.g. declared_recipes — #324 Phase 2)
    }
    stable_sys, dynamic_sys = build_system(
        active_tools, role=guide_role, intent=eff_intent, ctx=prompt_ctx)
    # CC-convergence Phase 4 (cache split): system is sent as TWO blocks at the
    # transport layer — the stable prefix (cache_control: ephemeral) plus the
    # uncached dynamic tail (the BM25 recipes slice). Per-turn intent changes
    # only invalidate the small tail, not the 26K stable prefix.
    system = sidebar_text + focus_text + thread_text + stable_sys
    # The user-message reminder injection (the 'reminder-only catalog' variant)
    # is OFF by default after the 2026-06-02 Haiku+Sonnet study showed both
    # models reject reminder-only catalogs (Haiku can't find them, Sonnet
    # skips planning when recipes sit next to the user request). The helpers
    # stay in the codebase for the future user-invocable verb palette (Phase 5).
    recipes_reminder = ""
    _dump_turn_context(turn.run_id, user_text=user_text, system=system, history=history,
                       active_tools=active_tools, model=guide_model, thread_id=store_tid,
                       focus_entity_id=focus_entity_id)
    entity_id = WORKSPACE_ID
    focus_type = focus_ent["type"] if focus_ent else None

    analysis_ctx: dict = {"analysis_id": None, "turn_index": 0}
    # Per-turn recipe-uptake tracking: read_skill records names here; run_python/
    # run_r nudge (once) if the code uses a library a recipe covers but wasn't read.
    recipe_ctx: dict = {"read": set(), "nudged": False}
    usage_in = usage_out = usage_cr = usage_cw = 0   # Guide tokens this turn (+cache read/write)
    turn.thread_id = store_tid
    turn.entity_id = entity_id   # message-log scope for the reaper
    checkpoint(turn)  # initial Turn row before the loop runs

    # Run save opt-out. A plan's Run is opened server-side when present_plan fires
    # (default-save — robust even if the agent never calls open_run). If the user
    # approved with "Save as a run" UNCHECKED, the Go message carries this marker,
    # so discard the just-opened (still-empty) Run before execution groups under it.
    if "do not save this as a run" in (user_text or "").lower():
        try:
            from content.bio.lifecycle.runs import close_run
            close_run(store_tid)
        except Exception:  # noqa: BLE001
            pass

    # Drawer sidecar: send the structured Manifest snapshot to the client
    # so the right-rail drawer can render what the agent is currently
    # seeing. The model only ever consumes the rendered system string;
    # this is a UI-only stream.
    # Manifest also carries run_id so the frontend knows what to cancel
    # when the user hits Stop (no separate "stream started" event needed).
    yield sse({"type": "manifest", "manifest": manifest.to_dict(), "run_id": turn.run_id})

    try:
        while True:
            # Cancellation check at the iteration boundary. The user may
            # have hit Stop while we were processing the previous turn's
            # tool results, or even before sending. Bail before paying
            # for another LLM call.
            if cancel_token.cancelled:
                yield sse({"type": "cancelled", "reason": cancel_token.reason,
                           "run_id": turn.run_id})
                turn.transition(TurnState.FAILED)
                turn.error = {"type": "Cancelled", "message": cancel_token.reason}
                checkpoint(turn)
                yield sse({"type": "usage", "input": usage_in, "output": usage_out,
                           "cache_read": usage_cr, "cache_write": usage_cw})
                yield sse({"type": "done"})
                return
            turn.transition(TurnState.GENERATING); checkpoint(turn)
            # Request-time safety net for middle-orphan history (assistant
            # → assistant without an intervening user). The Turn reaper
            # handles trailing orphans; this catches the rest.
            #
            # 2026-05-31: effective_history() may make a SYNCHRONOUS Haiku
            # call (rolling.py's _summarize) when the conversation exceeds
            # SUMMARY_THRESHOLD. On the asyncio loop that parked the event
            # loop for the entire 2–3s LLM roundtrip — observed via the
            # off-loop sampler as the cause of "Files tab spins, chat
            # figures don't appear". Offload to a thread so other coroutines
            # (file-tree GET, /artifacts/<pid>/<name>, SSE flush) keep
            # running. The summarize call itself remains sync internally —
            # only the wait is moved off the loop.
            llm_history = _ensure_tool_pair_completeness(
                await asyncio.to_thread(effective_history, store_tid, history)
            )
            # CC-convergence Phase 4: prepend the recipes catalog as a
            # <system-reminder> on the latest user-text message. The splice is a
            # no-op when the latest message is a tool_result (in-progress agent
            # loop) — the catalog was already presented on the first iteration
            # of this turn.
            llm_history = _splice_recipes_reminder(llm_history, recipes_reminder)
            # Compact pre-send fingerprint — matched by `[llm-sent]` printed in
            # core/llm.py at the moment the stream opens. If they agree:
            # what guide.py prepared == what hit the API. If they differ:
            # something between this line and the API mutated the messages
            # (cache_control breakpoints are stripped before hashing so they
            # don't cause spurious mismatches).
            try:
                import hashlib as _h, json as _j
                _canon = _j.dumps(
                    [{"role": m["role"],
                      "content": [{k: v for k, v in b.items() if k != "cache_control"}
                                  if isinstance(b, dict) else b
                                  for b in m["content"]] if isinstance(m["content"], list) else m["content"]}
                     for m in llm_history],
                    sort_keys=True, default=str,
                ).encode("utf-8")
                _hist_sha = _h.sha256(_canon).hexdigest()[:12]
                _sys_sha = _h.sha256((system or "").encode("utf-8")).hexdigest()[:12]
                print(f"[llm-prep] run={turn.run_id} sys_sha={_sys_sha} "
                      f"hist_sha={_hist_sha} n_raw={len(history)} n_eff={len(llm_history)}",
                      flush=True)
            except Exception:  # noqa: BLE001
                pass

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
                    # Async stream (2026-05-31 switch to AsyncAnthropic): `async for`
                    # awaits the underlying HTTP read, so the event loop stays free
                    # for OTHER HTTP requests (Files tab polling, /artifacts image
                    # GETs, etc.) while the model is generating. The pre-fix
                    # sync iteration parked the loop on each `next()` call.
                    async with open_stream(llm_history, active_tools, system,
                                           model=guide_model,
                                           dynamic_system=dynamic_sys) as stream:
                        async for event in stream:
                            # Cancel check inside the streaming loop. Bail
                            # immediately so we stop paying for tokens the
                            # user no longer wants. The 'async with' block
                            # closes the underlying HTTP connection.
                            if cancel_token.cancelled:
                                break
                            if event.type == "content_block_delta":
                                delta = event.delta
                                if delta.type == "text_delta":
                                    emitted = True
                                    yield sse({"type": "delta", "text": delta.text})
                        if cancel_token.cancelled:
                            # Cancelled mid-stream — skip get_final_message
                            # (the partial message isn't usable) and let the
                            # outer loop check pick it up and emit cancelled.
                            break
                        final_msg = await stream.get_final_message()
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

            # If cancellation arrived during streaming, final_msg won't
            # exist — short-circuit to the outer-loop top, which detects
            # cancel and emits the cancelled SSE + returns cleanly.
            if cancel_token.cancelled:
                continue

            assistant_blocks = []
            text_out = ""
            tool_calls_this_turn: list[str] = []
            truncated_tool_uses: list[str] = []
            stop_reason = getattr(final_msg, "stop_reason", None)
            for block in final_msg.content:
                if block.type == "text":
                    assistant_blocks.append({"type": "text", "text": block.text})
                    text_out += block.text
                elif block.type == "tool_use":
                    inp = block.input if isinstance(block.input, dict) else {}
                    # max_tokens hit mid-tool-input → SDK couldn't parse the
                    # partial JSON → block.input == {} for a tool whose schema
                    # requires fields. Flag it so the user isn't left wondering
                    # "where's the draft?" (verified live 2026-06-01).
                    if not inp and stop_reason == "max_tokens":
                        truncated_tool_uses.append(block.name)
                    assistant_blocks.append({
                        "type": "tool_use",
                        "id": block.id,
                        "name": block.name,
                        "input": inp,
                    })
                    tool_calls_this_turn.append(block.name)
            if truncated_tool_uses:
                names = ", ".join(truncated_tool_uses)
                # Tell the AGENT (not just the user) that the cap was hit.
                # The agent has no other way to know — it just sees its own
                # malformed tool_use with empty input and has no signal it
                # was due to a token cap rather than its own mistake (Opus
                # 2026-06-01: diagnosed the empty input correctly and STILL
                # reproduced it on next turn, because it had no cause-and-
                # effect signal). Injecting this as a user-role text block
                # gets it into history; the next agent turn sees it before
                # acting. The same text also goes to the user's chat UI as
                # a notice — transparent + symmetric.
                agent_note = (
                    f"[system notice: your previous turn's tool call(s) ({names}) hit the "
                    "per-turn output token cap before their `input` could finish streaming, "
                    "so the API returned an unparseable partial JSON and the tool dispatch "
                    "was skipped (no tool_result was produced). When retrying, BREAK LARGE "
                    "content into smaller pieces. For text/document content prefer "
                    "`write_file(path, body)` for the first chunk + `write_file(path, body, "
                    "mode='a')` (or `edit_file` for surgical changes) for subsequent pieces "
                    "— `write_file`'s `body` field has no Python string-escape overhead, "
                    "so the same content fits in roughly half the tokens vs `run_python` "
                    "with `open().write(...)`. Do not repeat the same single large call — "
                    "it will hit the cap again."
                )
                append_message("user", [{"type": "text", "text": agent_note}],
                               entity_id=entity_id, focus_entity_id=focus_entity_id,
                               thread_id=store_tid)
                # User-facing version (one-line, no jargon).
                ui_note = (
                    f"⚠ The {names} call was cut off by the per-turn output cap. The agent "
                    "has been told to break the content into smaller writes — ask it to retry."
                )
                yield sse({"type": "notice", "text": ui_note})

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
                manifest=manifest.to_dict(),
            )
            turn_index += 1
            turn.turn_index = turn_index
            turn.usage_in = usage_in; turn.usage_out = usage_out
            turn.usage_cache_read = usage_cr; turn.usage_cache_write = usage_cw

            history = get_messages(entity_id, thread_id=store_tid)

            if final_msg.stop_reason != "tool_use":
                break

            # Record every tool_use id we're about to dispatch so the
            # reaper can synthesize matching tool_results if the process
            # dies mid-loop (A1). Ids are popped as each tool finishes.
            turn.pending_tool_ids = [
                b.id for b in final_msg.content
                if b.type == "tool_use" and getattr(b, "id", None)
            ]
            turn.transition(TurnState.EXECUTING_TOOLS); checkpoint(turn)
            tool_result_blocks = []
            # B1: a tool branch can request an AWAITING_USER halt by setting
            # pending_halt_signal to "plan" or "clarify". Same break-out
            # mechanism as the old halt_for_plan flag, generalized so a
            # second flavor (ask_clarification) doesn't fork the loop.
            pending_halt_signal: str | None = None
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
                    # T2.5: normalize the new structured shape (objects with
                    # title/skill/etc.) and run the validator. Concerns ride
                    # along on the SSE event so the user sees them inline.
                    from core.planning.validator import normalize_plan, validate_plan
                    plan = validate_plan(normalize_plan(inp))

                    # A4: persist the plan as a durable `plan` entity so it
                    # shows up in the Files tree under threads/T/plans/
                    # and is browsable a week later. lifecycle starts at
                    # 'validated' (the validator ran without erroring out);
                    # transitions to executing/completed/aborted land when
                    # the Go/Adjust flow gets a dedicated endpoint.
                    from core.graph.entities import create_entity
                    plan_eid = create_entity(
                        entity_type="plan",
                        title=plan.title or "Plan",
                        parent_entity_id=focus_entity_id,
                        metadata={
                            "thread_id": store_tid,
                            "plan": plan.to_dict(),
                            "plan_lifecycle": "validated",
                        },
                    )
                    # #160: stash on the Turn row so the resume endpoint can
                    # find which plan to transition when the user clicks Go.
                    turn.plan_entity_id = plan_eid
                    # Open the analysis Run for this plan NOW, server-side — the
                    # default-save the user expects, robust even if the agent never
                    # calls open_run. Rotates any prior open Run; an empty one is
                    # discarded on the next rotation or on the unchecked-box opt-out
                    # (handled at turn start). The agent does NOT open for plans.
                    try:
                        from content.bio.lifecycle.runs import open_run as _open_run
                        from content.bio.tools import _feedlog as _fl
                        _rid = _open_run(store_tid, plan.title or "Analysis run",
                                         focus_entity_id=focus_entity_id, plan_entity_id=plan_eid)
                        _fl(f"SERVER open_run @present_plan title={(plan.title or 'Analysis run')!r} "
                            f"plan_eid={plan_eid} -> run={_rid}")
                    except Exception as _e:  # noqa: BLE001
                        try:
                            from content.bio.tools import _feedlog as _fl
                            _fl(f"SERVER open_run @present_plan FAILED: {_e}")
                        except Exception: pass  # noqa: BLE001, E701
                    yield sse({"type": "plan", "entity_id": plan_eid, **plan.to_dict()})
                    ack = {
                        "status": "presented",
                        "plan_entity_id": plan_eid,
                        "note": "Plan shown to the user with Go/Adjust controls. "
                                "Wait for their decision before executing.",
                        "concerns": [c.to_dict() for c in plan.concerns],
                    }
                    tool_result_blocks.append({"type": "tool_result", "tool_use_id": block.id,
                                               "content": json.dumps(ack)})
                    pending_halt_signal = "plan"
                    continue

                # B1 — ask_clarification: pause the turn on a one-line
                # question. Twin of present_plan but lighter weight: no
                # entity, no validator, just the question + a synthetic
                # tool_result so the LLM history stays well-formed.
                if tool_name == "ask_clarification":
                    inp = tool_input if isinstance(tool_input, dict) else {}
                    question = str(inp.get("question") or "").strip()
                    if not question:
                        # Don't halt on an empty question — feed back an
                        # error and let the model continue or retry.
                        err = {"status": "error",
                               "note": "ask_clarification needs a non-empty `question`."}
                        tool_result_blocks.append({"type": "tool_result", "tool_use_id": block.id,
                                                   "content": json.dumps(err)})
                        continue
                    yield sse({
                        "type": "clarification_pending",
                        "question": question,
                        "tool_use_id": block.id,
                        "run_id": turn.run_id,
                    })
                    ack = {"status": "asked",
                           "note": "Question shown to the user. Stop here and "
                                   "wait for their reply before continuing."}
                    tool_result_blocks.append({"type": "tool_result", "tool_use_id": block.id,
                                               "content": json.dumps(ack)})
                    pending_halt_signal = "clarify"
                    continue

                # P1 #3 — per-tool approval gate. If the tool's schema declares
                # an `approval_policy` other than 'never' (default), and the
                # user hasn't already approved it this session, HALT here so
                # the UI can ask. The held tool is stashed on the Turn row
                # (pending_approval) and executed by the resume endpoint
                # after the user approves/rejects. The model NEVER sees an
                # auto-approval — every approval is the user's explicit choice.
                from core.runtime.approval import needs_approval
                tool_schema = next((t for t in active_tools if t["name"] == tool_name), None)
                policy = tool_schema.get("approval_policy") if tool_schema else None
                if needs_approval(policy, store_tid or "default", tool_name):
                    # Don't write a tool_result block — the held tool_use stays
                    # unresolved until resume, same as Phase 2 deferred. The
                    # reaper skip-rule for pending_user_signal='approval'
                    # prevents the orphan-fill scanner from marking it dead.
                    turn.pending_approval = {
                        "tool_name": tool_name,
                        "tool_input": tool_input if isinstance(tool_input, dict) else {},
                        "tool_use_id": block.id,
                        "policy": policy,
                    }
                    summary = _summarize_tool_input(tool_name, tool_input)
                    yield sse({
                        "type": "approval_pending",
                        "tool_name": tool_name,
                        "summary": summary,
                        "tool_use_id": block.id,
                        "run_id": turn.run_id,
                        "policy": policy,
                    })
                    pending_halt_signal = "approval"
                    break    # stop processing further tool_use blocks this turn

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

                # P1 #5 — pass per-turn context to executors that consult it
                # (read_skill checks active_tools for skill-tool linkage).
                # Most executors ignore ctx; execute_tool peeks at the
                # signature and forwards conditionally.
                # Progress channel: long synchronous tools (installs, kernel
                # exec, nextflow) push phase lines onto this queue; we drain it
                # and stream `tool_progress` SSE while the worker thread runs.
                import queue as _queue
                _progress_q: _queue.Queue = _queue.Queue()
                tool_ctx = {
                    "active_tools": active_tools,
                    "thread_id": store_tid,
                    "focus_entity_id": focus_entity_id,
                    "session_id": session_id,
                    "recipe_ctx": recipe_ctx,
                    "intent": eff_intent,
                    # Long-running tools register kill interrupters here so
                    # Stop actually stops the work (not just the UI).
                    "cancel_token": cancel_token,
                    "progress_q": _progress_q,
                }
                # P3 #6 telemetry — wrap dispatch with timing.
                import datetime as _dt
                _t_start = _dt.datetime.now(_dt.timezone.utc)
                loop = asyncio.get_event_loop()
                _fut = loop.run_in_executor(
                    None, execute_tool, tool_name, tool_input, tool_ctx
                )

                def _drain_progress():
                    out = []
                    try:
                        while True:
                            out.append(_progress_q.get_nowait())
                    except _queue.Empty:
                        pass
                    return out

                while not _fut.done():
                    evs = _drain_progress()
                    for ev in evs:
                        yield sse({"type": "tool_progress", "name": tool_name,
                                   "message": ev.get("message"), "phase": ev.get("phase")})
                        # 2026-05-31: explicit event-loop yield between SSE emits.
                        # Without this, chatty cells (R/Seurat progress bars print
                        # dozens of `0%…100%` lines per cell) churn through the
                        # inner for-loop in tight succession — `yield sse(...)` is
                        # a consumer-driven suspension that Starlette pulls fast,
                        # so OTHER coroutines (GET /api/files/tree, /artifacts/…)
                        # never get a slot. The async-LLM fix earlier covered the
                        # LLM-streaming phase; this covers tool-progress streaming.
                        await asyncio.sleep(0)
                    if not evs:
                        await asyncio.sleep(0.2)
                for ev in _drain_progress():   # flush the tail
                    yield sse({"type": "tool_progress", "name": tool_name,
                               "message": ev.get("message"), "phase": ev.get("phase")})
                    await asyncio.sleep(0)
                result_str = await _fut
                _t_end = _dt.datetime.now(_dt.timezone.utc)
                result_obj = json.loads(result_str)
                _telem_status = (
                    "deferred" if isinstance(result_obj, dict) and result_obj.get("deferred")
                    else "error" if isinstance(result_obj, dict) and (result_obj.get("error") or result_obj.get("status") == "error")
                    else "ok"
                )
                _telem_err = None
                if _telem_status == "error" and isinstance(result_obj, dict):
                    _telem_err = str(result_obj.get("error") or result_obj.get("note") or "")[:300]
                try:
                    from core.runtime.tool_telemetry import record as _record_invocation
                    _record_invocation(
                        run_id=turn.run_id,
                        agent_spec=turn.agent_spec_name,
                        tool_name=tool_name,
                        input_=tool_input,
                        started_at=_t_start.isoformat(),
                        ended_at=_t_end.isoformat(),
                        duration_ms=int((_t_end - _t_start).total_seconds() * 1000),
                        status=_telem_status,
                        error_summary=_telem_err,
                    )
                except Exception:  # noqa: BLE001
                    pass    # telemetry must never block real work

                # P2 #4 — deferred tool result. Tool returned `{deferred: true,
                # deferred_id}` instead of a real result. Halt the turn in
                # AWAITING_TOOL_RESULT; the webhook
                # POST /api/turns/{run_id}/tool_result/{tool_use_id}
                # writes the real result later and resumes the loop. We
                # don't write any tool_result block for the deferred tool;
                # the reaper skip-rule for AWAITING_TOOL_RESULT prevents
                # orphan-fill from clobbering it.
                if isinstance(result_obj, dict) and result_obj.get("deferred"):
                    turn.pending_deferred = {
                        "tool_name": tool_name,
                        "tool_use_id": block.id,
                        "deferred_id": result_obj.get("deferred_id"),
                        "started_at": __import__("datetime").datetime.now(
                            __import__("datetime").timezone.utc).isoformat(),
                        "timeout_s": int(result_obj.get("timeout_s") or 0) or None,
                    }
                    yield sse({
                        "type": "deferred_tool_pending",
                        "tool_name": tool_name,
                        "deferred_id": result_obj.get("deferred_id"),
                        "tool_use_id": block.id,
                        "run_id": turn.run_id,
                    })
                    # Write any earlier results, then halt. Don't write the
                    # deferred tool's result — webhook does that on completion.
                    if tool_result_blocks:
                        append_message("user", tool_result_blocks,
                                       entity_id=entity_id, focus_entity_id=focus_entity_id,
                                       thread_id=store_tid)
                    turn.transition(TurnState.AWAITING_TOOL_RESULT)
                    checkpoint(turn)
                    yield sse({"type": "usage", "input": usage_in, "output": usage_out,
                               "cache_read": usage_cr, "cache_write": usage_cw})
                    yield sse({"type": "done"})
                    return

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
                    # B4: hand the Guide's run_id to handlers so async-fired
                    # advisor turns (Methodologist) can record parent_run_id.
                    "parent_run_id": turn.run_id,
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

            # Skip writing an empty user message — happens when the FIRST
            # tool_use this iteration triggered an approval halt and nothing
            # ran. The held tool_use stays unresolved; the resume endpoint
            # writes its result. Reaper skip-rule (pending_user_signal=
            # 'approval') prevents orphan-fill from clobbering it.
            if tool_result_blocks:
                append_message("user", tool_result_blocks,
                               entity_id=entity_id, focus_entity_id=focus_entity_id,
                               thread_id=store_tid)
            # All this iteration's tool_uses have matching tool_results in
            # the message log now — clear the in-flight set (A1). Approval
            # halt leaves the held tool's id in pending_tool_ids; the
            # resume endpoint clears it when the real result is written.
            if pending_halt_signal != "approval":
                turn.pending_tool_ids = []
            checkpoint(turn)
            history = get_messages(entity_id, thread_id=store_tid)

            # A halt-requesting tool (plan / clarify) PAUSES the turn:
            # the on_stop reflection hooks are for natural session ends,
            # not mid-conversation pauses, so we emit usage + done and
            # return without falling through to SUMMARIZING (which would
            # overwrite AWAITING_USER → DONE and break the resume gate).
            if pending_halt_signal:
                turn.transition(TurnState.AWAITING_USER)
                turn.pending_user_signal = pending_halt_signal
                checkpoint(turn)
                yield sse({"type": "usage", "input": usage_in, "output": usage_out,
                           "cache_read": usage_cr, "cache_write": usage_cw})
                yield sse({"type": "done"})
                return

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
            # #160: if this turn was driving a plan's execution, mark the
            # plan completed. Idempotent + safe on a missing entity.
            if turn.plan_entity_id:
                try:
                    from content.bio.lifecycle.plans import set_plan_lifecycle
                    set_plan_lifecycle(turn.plan_entity_id, "completed")
                except Exception:  # noqa: BLE001
                    pass    # plan-tracking is best-effort; never block normal completion
        yield sse({"type": "usage", "input": usage_in, "output": usage_out,
                   "cache_read": usage_cr, "cache_write": usage_cw})
        yield sse({"type": "done"})

    except Exception as e:
        print(f"[guide] stream_response failed: {type(e).__name__}: {e}")
        turn.error = {"type": type(e).__name__, "message": str(e)}
        turn.transition(TurnState.FAILED)
        checkpoint(turn)
        if turn.plan_entity_id:
            try:
                from content.bio.lifecycle.plans import set_plan_lifecycle
                set_plan_lifecycle(turn.plan_entity_id, "failed")
            except Exception:  # noqa: BLE001
                pass
        yield sse({"type": "error", "text": _friendly_error(e),
                   "detail": f"{type(e).__name__}: {e}"})
        yield sse({"type": "usage", "input": usage_in, "output": usage_out,
                   "cache_read": usage_cr, "cache_write": usage_cw})
        yield sse({"type": "done"})
    finally:
        # Always release the cancel token — leaking it would keep stale
        # interrupters reachable and (via the registry) a re-entrant
        # cancel on this run_id would fire them against now-defunct
        # processes/connections. The TurnSink is closed by the executor's
        # `_drain` finally (turn_executor.py); we don't touch it here.
        _cancel.release(turn.run_id)
