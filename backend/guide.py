import json
import asyncio
import os
from typing import AsyncGenerator

from core import config
from core.config import FAKE_SESSION
from core.graph._schema import WORKSPACE_ID
from core.graph.audit import log_context_assembly, session_assembly_summary, add_context_suggestion
from core.graph.entities import get_entity, update_entity
from core.graph.messages import append_message, get_messages
from core.graph.threads import get_or_create_default_thread
from core.runtime.llm_runtime import (
    RuntimeRequest, SystemSpec, TextDelta, ToolResult,
    ToolUseStart, TurnDone, TurnHalt,
)
from core.runtime.llm_runtime_direct import (
    _RetryNotice,
    _StreamCompleted,
    _ToolProgress,
    DirectAPIRuntime,
)
from core.manifest.assembler import build_manifest, render_focus_preamble
from core.hooks.dispatcher import dispatch
from core.runtime import wire
from core.runtime.turn import Turn, TurnState, gen_run_id
from core.runtime.checkpoint import checkpoint
from core.runtime.content_pack import active_pack
from core.jobs.runner import submit_python_job
from core.summarize.rolling import effective_history
# Wave 2 A.3: bio content (prompts, tools, cards, hooks, session-id
# factory) is reached via core.runtime.content_pack.active_pack() —
# NOT direct imports. The pack is registered by main.py startup.
# Previously the noqa: F401 imports of bio.lifecycle.registry / .advisors
# / .lifecycle.adaptive / .proposals.scheduler were here to trigger
# hook registration as a side-effect; now BIO_PACK.register_hooks()
# does that explicitly.

# W1-A.2 phase 2: the stream open + retry loop moved to
# core.runtime.llm_runtime_direct.open_and_consume_stream. guide.py
# iterates its events; behavior is invariant.


# WU-5: per-concern helpers extracted into sibling modules under
# core/runtime/. stream_response below imports them via the underscore
# aliases (preserves diff with pre-WU-5 in the loop body — only the
# import lines change).
from core.runtime.history_prep import (
    api_messages as _api_messages,
    is_interrupted_fill as _is_interrupted_fill,
    dedup_tool_results as _dedup_tool_results,
    splice_recipes_reminder as _splice_recipes_reminder,
    ensure_tool_pair_completeness as _ensure_tool_pair_completeness,
)
from core.runtime.turn_intent import (
    effective_intent as _effective_intent,
    derive_thread_title as _derive_thread_title,
)
from core.runtime.turn_telemetry import (
    dump_turn_context as _dump_turn_context,
    live_log_event as _live_log_event,
)
from core.runtime.llm_errors import (
    is_transient as _is_transient,
    friendly_error as _friendly_error,
)


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


def _previous_user_focus(history: list[dict]) -> str | None:
    """focus_entity_id of the most-recent user message OTHER than the
    current one (history[-1]). Returns None when no prior user message
    exists in this thread (we're on the first turn — nothing to compare
    against). Caller must have already appended the current user
    message to history before calling.
    """
    if len(history) < 2:
        return None
    for m in reversed(history[:-1]):
        if m.get("role") == "user":
            return m.get("focus_entity_id")
    return None


def _build_focus_change_marker(prev_focus_id: str | None,
                               current_focus_id: str | None) -> str | None:
    """Synthesize the one-line '[Focus changed: …]' marker prepended
    to the current user message's content when focus has changed
    since the prior user message.

    Returns None when:
      - no prior user message in the thread (first turn)
      - focus is unchanged (treating None / WORKSPACE_ID as equal)

    The marker tells the model EXACTLY what changed, with both old +
    new entity names and ids, so it re-anchors reasoning on the new
    context. Defeats the recency-bias failure mode where the agent's
    'active entity' pointer (accumulated across many tool calls)
    refuses to follow a navigation event hidden in the focus preamble.

    For a Result-typed focus, names the member figure/table id(s)
    explicitly — the agent needs the right entity_id for make_revision
    on the new focus's contents, not the prior chain's last entry.

    Mirrors trailer + annotation_note: in-memory only, not persisted.
    """
    # First user message in the thread — nothing to compare against, no
    # transition to announce. (Even if the user is "going from workspace
    # to entity" on their opening message, they didn't navigate FROM
    # workspace — they started there. Cleaner not to invent a transition.)
    if prev_focus_id is None:
        return None
    prev = prev_focus_id or WORKSPACE_ID
    cur = current_focus_id or WORKSPACE_ID
    if prev == cur:
        return None

    def _describe_brief(eid: str) -> str:
        """Title + id only. Used for the OLD focus to keep the marker
        signal focused on the NEW focus's members."""
        if eid == WORKSPACE_ID:
            return "the workspace (no specific entity focused)"
        e = get_entity(eid)
        if not e:
            return f"entity {eid} (no longer exists)"
        return f"{e.get('type','entity')} {(e.get('title') or '').strip()!r} (id {e['id']})"

    def _describe_full(eid: str) -> str:
        """Title + id + member listing (for Result types). Used for the
        NEW focus so the agent sees exactly which entity_ids to use for
        tool calls on the current context."""
        if eid == WORKSPACE_ID:
            return "the workspace (no specific entity focused)"
        e = get_entity(eid)
        if not e:
            return f"entity {eid} (no longer exists)"
        base = f"{e.get('type','entity')} {(e.get('title') or '').strip()!r} (id {e['id']})"
        if e.get("type") == "result":
            members = (e.get("metadata") or {}).get("members") or []
            member_bits: list[str] = []
            for m in members:
                if not isinstance(m, dict):
                    continue
                kind = m.get("kind") or "?"
                ref = m.get("ref")
                if not ref:
                    continue
                cell = get_entity(ref)
                if not cell:
                    continue
                # Content packs may override displayed_id via the
                # on_resolve_displayed_id hook (bio uses figure_history
                # to show the latest revision instead of the anchor).
                _dctx = {"ref": ref, "displayed_id": cell["id"]}
                dispatch("on_resolve_displayed_id", _dctx)
                displayed_id = _dctx["displayed_id"]
                t = (cell.get("title") or "").strip()
                member_bits.append(f"{kind} {t!r} (id {displayed_id})")
            if member_bits:
                base += " holding " + "; ".join(member_bits)
        return base

    return (f"[Focus changed: previously the user was viewing {_describe_brief(prev)}. "
            f"They've now navigated to {_describe_full(cur)}. Treat subsequent "
            f"references in their next message as referring to the NEW focus, "
            f"not the previously-discussed entity. For tool calls (entity_id "
            f"arg to make_revision/reproduce), use ids from the new focus.]")


def _build_focus_trailer(focus_entity_id: str) -> str | None:
    """One-line, very compact 'currently focused on X' reminder appended
    AFTER the user's text in the in-memory history (not persisted).
    Recency-bias counterweight to the focus preamble at the top of the
    system prompt — see the call site in stream_response for the
    failure mode it addresses.

    For a Result, names the figure/table members + their ids so the
    agent has the exact entity_id to pass to make_revision (the live
    failure was passing a stale conversation-history id instead).

    Returns None when the focused entity can't be loaded (caller skips
    the trailer; nothing to lose vs. today's behavior).
    """
    e = get_entity(focus_entity_id)
    if not e:
        return None
    bits = [f"focused on {e.get('type','entity')} {(e.get('title') or '').strip()!r} (id {e['id']})"]
    if e.get("type") == "result":
        members = (e.get("metadata") or {}).get("members") or []
        member_bits = []
        for m in members:
            if not isinstance(m, dict): continue
            kind = m.get("kind") or "?"
            ref = m.get("ref")
            if not ref:
                continue
            cell = get_entity(ref)
            if not cell: continue
            # If the figure/table has a revision chain, the panel shows
            # chain[0] (latest), not the anchor — so cite the displayed id.
            _dctx = {"ref": ref, "displayed_id": cell["id"]}
            dispatch("on_resolve_displayed_id", _dctx)
            displayed_id = _dctx["displayed_id"]
            t = (cell.get("title") or "").strip()
            member_bits.append(f"{kind} {t!r} (id {displayed_id})")
        if member_bits:
            bits.append("holding " + "; ".join(member_bits))
    msg = ", ".join(bits)
    return (f"[Reminder: {msg}. For 'this figure'/'this result'/'this table' "
            f"and for tool calls (entity_id arg to make_revision/reproduce), "
            f"use these ids — even if the conversation history just discussed "
            f"a different entity.]")


# The priority set (tools whose FULL docstring survives a 'summary' mode) lives HERE,
# not in YAML, because membership is a runtime tuning concern (the most-called tools
# per turn), adjusted as we learn from real sessions.
_PRIORITY_TOOLS: tuple[str, ...] = (
    "run_python", "run_r",
    "Skill", "search_skills",
    "present_plan", "ask_clarification",
    "register_dataset", "list_data_files",
    "ensure_capability", "describe_tool",
)
# find_files rides summary-rendering: its full docstring is the catalog's
# largest and list_data_files covers the common case; full prose stays one
# describe_tool away. This set is the budget lever for the lean half-window
# ceiling (tests/test_lean_summary_budget.py) — trim HERE, never the
# tool_allowlist (full-surface parity is guarded).


def _assemble_active_tools(tools_all: list, spec) -> list:
    """The capability set offered this turn (Item 2B extraction of the inline
    tool-catalog assembly). Pack tools minus disabled, plus MCP-served tools
    (prefixed 'server:tool', rendered per `spec.prompt_mode` via the single-source
    presentation policy — only prose shrinks per mode; the calling contract is
    identical, see .claude/CLAUDE.md), filtered to the spec's tool_allowlist
    (a no-op for the Guide's ('*',) allowlist). Disabled tools are neither offered
    nor advertised. Gateway failure never blocks normal dispatch."""
    from core.graph.tool_settings import get_disabled_tools
    from core.runtime.agent import filter_tools_by_allowlist
    disabled = get_disabled_tools()
    active = [t for t in tools_all if t["name"] not in disabled]
    try:
        from core.runtime.mcp import list_tools as mcp_list_tools
        mcp_tools = mcp_list_tools(
            mode=(spec.prompt_mode if spec else "full"),
            priority_tools=_PRIORITY_TOOLS,
        )
        active.extend(t for t in mcp_tools if t["name"] not in disabled)
    except Exception:  # noqa: BLE001
        pass    # gateway failure must never block normal tool dispatch
    if spec is not None:
        active = filter_tools_by_allowlist(active, spec.tool_allowlist)
    return active


def _summary_budget(spec) -> int | None:
    """Tier-2 summary budget precedence: the DEDICATED override knob
    (ABA_HISTORY_SUMMARY_BUDGET_OVERRIDE_CHARS, >0) is explicit operator/
    harness intent and wins over the spec's class default (grounded_guide
    pins 100k), which wins over the global default (None →
    effective_history falls through to HISTORY_SUMMARY_THRESHOLD_CHARS).
    Deliberately a DIFFERENT env var from the global threshold: reusing it
    made tuning the fall-through default silently clobber every spec pin
    (review F5); and before any knob existed, harness overrides were
    silently inert against pinned specs (vacuous compaction-study round)."""
    try:
        from core.config import HISTORY_SUMMARY_BUDGET_OVERRIDE
        ov = int(HISTORY_SUMMARY_BUDGET_OVERRIDE.get() or 0)
        if ov > 0:
            return ov
    except Exception:  # noqa: BLE001 — a broken knob must not break turns
        pass
    return spec.summary_budget_chars if spec else None


def _resolve_turn_spec(thread_id: str | None, spec_override: str | None):
    """Resolve the agent spec + model for this turn (Item 2B extraction).

    Model precedence (resolved PER PROJECT at the turn boundary, not import time, so
    the Settings model selector takes effect next turn without a restart): env override
    > the project's selected model > config.env > bundle default > snapshot. The SPEC
    follows the chosen model via the install-wide catalog unless a request/thread
    override pins it: request_override → thread.metadata.spec → catalog[model].spec →
    bundle primary_spec → "guide". A2/A3: guide is spec-driven (model + role only); the
    loop body stays in stream_response. Returns (spec, spec_name, guide_model)."""
    from core.runtime.agent import get_agent_spec, resolve_spec_for_turn
    from core.config import current_model_for_project
    from core.projects import current_project_id
    from core.llm_catalog import spec_for_model
    # Thread's pinned spec only if a real thread id was passed; "default" is a
    # sentinel the chat handler may not have materialized yet — no side effects here.
    thread_spec: str | None = None
    if thread_id and thread_id != "default":
        try:
            from core.graph.threads import get_thread_spec
            thread_spec = get_thread_spec(thread_id)
        except Exception:                                    # noqa: BLE001
            thread_spec = None
    guide_model = current_model_for_project(current_project_id())
    spec_name = resolve_spec_for_turn(
        request_override=spec_override, thread_spec=thread_spec,
        project_default=spec_for_model(guide_model))
    spec = get_agent_spec(spec_name)
    if spec is None and spec_name != "guide":
        print(f"[guide] WARNING: spec={spec_name!r} "
              f"is not registered; falling back to 'guide'", flush=True)
        spec_name = "guide"
        spec = get_agent_spec(spec_name)
    if not guide_model and spec:
        guide_model = spec.model
    return spec, spec_name, guide_model


def _assemble_turn_history(*, user_text, attachments, focus_entity_id, store_tid,
                           retry, annotation_note, annotation_image):
    """Persist the user's turn (unless retry) and assemble the EFFECTIVE in-memory
    history for this call (Item 2B extraction). Reads persisted history, then applies
    the ephemeral, NON-persisted injections in order: framing note, thread title/
    question seed (this one persists), focus-change marker, focus trailer, vision
    image, and attachment context note. Returns the mutated history list. Behavior is
    guarded by test_focus_change_marker / test_focus_trailer / test_annotation_note_
    ephemeral + the golden-context guard + a live turn."""
    if not retry:
        # PERSIST ONLY the user's actual text. The annotation note (a
        # system-generated framing hint authored by the frontend, e.g.
        # 'The user is asking about run output "X" (entity_id="fig_Y") -
        # the attached image is that plot') is intentionally NOT
        # appended here -- it's injected ephemerally below, mirroring
        # annotation_image's lifecycle. Persisting it would leak the
        # SplitButton's implementation detail into the conversation as
        # a user statement and bias all subsequent turns toward
        # whatever figure the user happened to click last (focus
        # regression found 2026-06-07 in thread thr_806a2ced).
        # Empty text is valid when the user just paperclips a file with no words.
        _text = user_text or ("(see attached)" if attachments else "")
        user_blocks: list[dict] = [{"type": "text", "text": _text}]
        if attachments:
            # UI-only chip/thumbnail block (stripped before the model by
            # history_prep.api_messages); the agent gets the files via the
            # ephemeral context note + vision blocks injected below.
            from core.runtime.attachments import ui_item
            user_blocks.append({"type": "attachments",
                                "items": [ui_item(a) for a in attachments]})
        append_message("user", user_blocks, entity_id=WORKSPACE_ID,
                       focus_entity_id=focus_entity_id, thread_id=store_tid)
    history = get_messages(WORKSPACE_ID, thread_id=store_tid)

    # Inject the framing note ephemerally so it leads the user turn in
    # the LLM call but does NOT enter persisted history. Same lifecycle
    # as annotation_image (see the image-injection block below for the
    # mirroring rationale); together they yield in-memory block order
    # [note, image, user_text] without poisoning future turns.
    if annotation_note and not FAKE_SESSION and history and not retry:
        history = list(history)
        last = dict(history[-1])
        last["content"] = [{"type": "text", "text": annotation_note}, *list(last["content"])]
        history[-1] = last

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

    # Focus-CHANGE marker — prepended to the current user message's
    # content when focus has changed since the prior user message. Fix
    # B for the focus-handling regression (2026-06-07/08 thr_b80bc612):
    # the agent kept chaining tool calls on the previously-focused
    # entity's id (UMAP), ignoring that focus had switched to a new
    # Result (heatmap). The deixis rule + trailer help but they don't
    # NAME the transition; this marker does, with both old and new
    # entity ids + members, exactly at the boundary. Fires at most
    # once per effective change — re-rendering the same prompt yields
    # the same marker (the comparison is between two stored fields,
    # not a transient event). Skipped on image turns (image is
    # dominant context; markers/trailers stand down).
    if (focus_entity_id and history and not retry
            and not annotation_image):
        prev_focus = _previous_user_focus(history)
        marker = _build_focus_change_marker(prev_focus, focus_entity_id)
        if marker:
            history = list(history)
            last = dict(history[-1])
            last["content"] = [{"type": "text", "text": marker},
                               *list(last["content"])]
            history[-1] = last

    # Focus-trailer reminder — appended AFTER the user's question for
    # the same recency-bias reason the image trailer below exists.
    # Combats the "agent keeps treating focus as the entity discussed
    # earlier in the conversation, not the entity currently focused"
    # failure mode found 2026-06-07 in thr_b80bc612 (the agent passed
    # entity_id=<UMAP-from-prior-turn> to make_revision while focus was
    # the heatmap Result). The focus preamble at the top of the system
    # prompt already carries this information, but a heavily-biased
    # conversation history can outweigh it; restating at the tail
    # gives the directive a fighting chance against recency.
    #
    # Skipped when an annotation_image is attached — that case already
    # gets its own trailer below; stacking trailers is noisy. Skipped
    # when focus is workspace (no specific entity to anchor on).
    if (focus_entity_id and focus_entity_id != WORKSPACE_ID
            and not annotation_image and history):
        trailer = _build_focus_trailer(focus_entity_id)
        if trailer:
            history = list(history)
            last = dict(history[-1])
            last["content"] = [*list(last["content"]),
                               {"type": "text", "text": trailer}]
            history[-1] = last

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

    # Attachments (composer paperclip / clipboard paste): inject an ephemeral
    # context note + (for images) vision blocks into THIS user turn so the agent
    # is well-contextualized to follow up. NOT persisted — the persisted
    # `attachments` chip block (stripped before the model) handles re-render.
    # Same lifecycle as annotation_image: leads the turn, gone on later turns.
    if attachments and history:
        from core.runtime.attachments import build_injection
        note = build_injection(attachments)
        if note:
            history = list(history)
            last = dict(history[-1])
            history[-1] = {**last,
                           "content": [{"type": "text", "text": note}, *list(last["content"])]}

    return history


def _build_system_prompt(prompts, active_tools, spec, guide_role, eff_intent,
                         prompt_ctx, sidebar_text, focus_text, thread_text):
    """Assemble the turn's system prompt (Item 2B). Returns (system, dynamic_sys):
    `system` = the pack's STABLE system block ALONE (sent at the transport layer with
    cache_control: ephemeral); `dynamic_sys` = everything that varies per turn — the
    project sidebar, focus + thread preambles, the pack's BM25 recipes slice and the
    live compute-env line (mode + node capacity + Slurm landscape). The split is a
    CACHING contract, not a formatting one: `system` must be byte-identical across
    turns or the messages breakpoint misses too (see core.llm.place_volatile_tail)."""
    import time as _time
    _debug_timing = config.settings.debug_timing.get()
    _t0 = _time.perf_counter()
    stable_sys, dynamic_sys = prompts["system"](
        active_tools, role=guide_role, intent=eff_intent, ctx=prompt_ctx,
        mode=(spec.prompt_mode if spec else "full"))
    if _debug_timing:
        print(f"[guide-timing] prompt_assembly={(_time.perf_counter()-_t0)*1000:.0f}ms "
              f"sys_chars={len(stable_sys or '') + len(dynamic_sys or '')}", flush=True)
    # Auto-surface the compute environment into the per-turn dynamic block so the
    # agent plans placement with current facts (20s-cached; empty on a bare box).
    try:
        from core.exec.compute_env import context_line as _compute_line
        _cl = _compute_line()
        if _cl:
            dynamic_sys = (dynamic_sys + "\n\n" if dynamic_sys else "") + _cl
    except Exception:  # noqa: BLE001
        pass
    # The project sidebar, focus preamble and thread preamble are per-turn STATE —
    # the sidebar is by construction an "always-fresh snapshot" that moves the moment
    # a dataset/run/figure is created. They used to be concatenated onto the FRONT of
    # the cached system block, which made the "stable" prefix change on the very turns
    # the session was productive; the cache prefix runs tools → system → messages, so
    # each such change re-sent the whole conversation as fresh input. They ride the
    # volatile tail now (delivered at the END of the prompt by place_volatile_tail),
    # ahead of the recipe slice and compute-env line to keep their relative order.
    volatile = sidebar_text + focus_text + thread_text
    if volatile and dynamic_sys:
        dynamic_sys = volatile + "\n\n" + dynamic_sys
    else:
        dynamic_sys = volatile + dynamic_sys
    return stable_sys, dynamic_sys


async def stream_response(
    user_text: str,
    *,
    focus_entity_id: str = WORKSPACE_ID,
    focus_member_id: str | None = None,
    thread_id: str = "default",
    annotation_image: str | None = None,
    annotation_note: str | None = None,
    attachments: list[dict] | None = None,
    retry: bool = False,
    plan_entity_id: str | None = None,
    run_id: str | None = None,
    spec_override: str | None = None,
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

    # Wave 2 A.3: the content pack is the single seam to bio. Cache the
    # accessors per-turn — calling pack methods is cheap but the dict
    # lookup beats re-fetching.
    pack = active_pack()
    _prompts = pack.prompts()
    _tools_all = pack.tools()
    _exec_tool = pack.execute_tool()
    session_id = pack.new_session_id()
    turn_index = 0
    # A2: Guide is spec-driven (model + role); resolve spec + model at the turn
    # boundary via the module-level helper (Item 2B). Loop body stays inline.
    spec, spec_name, guide_model = _resolve_turn_spec(thread_id, spec_override)

    # Turn checkpointing (Pass E): create a Turn row at the start; update
    # state through transitions; mark DONE/FAILED at the end. Lets resume-
    # after-restart see what was in flight.
    turn = Turn(
        run_id=run_id or gen_run_id(),
        session_id=session_id,
        turn_index=0,
        agent_spec_name=spec_name,
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

    # Persist the user turn + assemble the effective in-memory history (ephemeral
    # injections: framing note, focus marker/trailer, vision, attachments) — Item 2B.
    history = _assemble_turn_history(
        user_text=user_text, attachments=attachments,
        focus_entity_id=focus_entity_id, store_tid=store_tid, retry=retry,
        annotation_note=annotation_note, annotation_image=annotation_image)
    # Capability set for this turn — assembled by the module-level helper (Item 2B).
    active_tools = _assemble_active_tools(_tools_all, spec)

    guide_role = spec.manifest_role if spec else "primary"
    manifest = build_manifest(
        session_id=session_id,
        turn_index=turn_index,
        focus_entity_id=focus_entity_id,
        focus_member_id=focus_member_id,
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
    # Assemble the system prompt (stable block + dynamic tail) — Item 2B helper.
    system, dynamic_sys = _build_system_prompt(
        _prompts, active_tools, spec, guide_role, eff_intent, prompt_ctx,
        sidebar_text, focus_text, thread_text)
    import time as _time   # per-iteration timing in the loop below
    _debug_timing = config.settings.debug_timing.get()
    # The user-message reminder injection (the 'reminder-only catalog' variant)
    # is OFF by default after the 2026-06-02 Haiku+Sonnet study showed both
    # models reject reminder-only catalogs (Haiku can't find them, Sonnet
    # skips planning when recipes sit next to the user request). The helpers
    # stay in the codebase for the future user-invocable verb palette (Phase 5).
    recipes_reminder = ""
    # Per-turn discovery reminder (lean_small / Qwen3-class only). Empty
    # for every other mode. Rides next to the user message via the same
    # splice path as recipes_reminder — Qwen3's documented recency bias
    # makes this position particularly salient.
    discovery_reminder = _prompts["discovery_reminder"](
        spec.prompt_mode if spec else "full",
        user_text or "")
    # NOTE: the dump moved INTO the loop (below, gated on `_ctx_dumped`)
    # so it captures the POST-effective_history view — the actual
    # message list the LLM sees, not the raw pre-Tier-2 history. The
    # pre-fix endpoint silently misled debugging (prj_a6f40e94
    # 2026-06-19: dev tab showed 42 unsummarized msgs while the model
    # was actually receiving the Tier-2-collapsed 7-msg version).
    _ctx_dumped = False
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
        dispatch("on_run_save_opt_out", {"thread_id": store_tid})

    # Drawer sidecar: send the structured Manifest snapshot to the client
    # so the right-rail drawer can render what the agent is currently
    # seeing. The model only ever consumes the rendered system string;
    # this is a UI-only stream.
    # Manifest also carries run_id so the frontend knows what to cancel
    # when the user hits Stop (no separate "stream started" event needed).
    yield sse(wire.manifest(manifest=manifest.to_dict(), run_id=turn.run_id))

    try:
        _empty_retry_done = False       # degenerate-empty turn defense (below)
        _turn_produced_output = False   # ANY generation this turn spoke/acted
        while True:
            # Cancellation check at the iteration boundary. The user may
            # have hit Stop while we were processing the previous turn's
            # tool results, or even before sending. Bail before paying
            # for another LLM call.
            if cancel_token.cancelled:
                yield sse(wire.cancelled(reason=cancel_token.reason,
                                         run_id=turn.run_id))
                turn.transition(TurnState.FAILED)
                turn.error = {"type": "Cancelled", "message": cancel_token.reason}
                # durable stop marker: the thread must RECORD the stop — for
                # the user after a reload (an empty turn reads as a glitch,
                # ux_findings F4 follow-up) and for the agent's own history
                # (later turns should know this one was interrupted)
                try:
                    append_message("assistant",
                                   [{"type": "text",
                                     "text": "*(stopped by user)*"}],
                                   entity_id=entity_id,
                                   focus_entity_id=focus_entity_id,
                                   thread_id=store_tid)
                except Exception:  # noqa: BLE001 — marker never blocks the bail
                    pass
                checkpoint(turn)
                yield sse(wire.usage(input=usage_in, output=usage_out,
                                     cache_read=usage_cr, cache_write=usage_cw))
                yield sse(wire.done())
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
                await asyncio.to_thread(
                    effective_history, store_tid, history,
                    _summary_budget(spec),
                    (spec.summary_tail_keep    if spec else None),
                )
            )
            # Dump the EFFECTIVE context (post-Tier-1 prune + Tier-2
            # summary substitution) once per turn. Earlier code dumped
            # the raw `history` before this point, which silently
            # misled debugging — see the note above where the flag is
            # initialized.
            if not _ctx_dumped:
                _dump_turn_context(turn.run_id,
                                   user_text=user_text, system=system,
                                   history=llm_history,
                                   active_tools=active_tools,
                                   model=guide_model, thread_id=store_tid,
                                   focus_entity_id=focus_entity_id)
                _ctx_dumped = True
            # CC-convergence Phase 4: prepend the recipes catalog as a
            # <system-reminder> on the latest user-text message. The splice is a
            # no-op when the latest message is a tool_result (in-progress agent
            # loop) — the catalog was already presented on the first iteration
            # of this turn.
            # Discovery reminder rides next to the user message via the
            # same splice path. Concatenating preserves order: discovery
            # first (highest recency salience), then any recipes_reminder
            # that may be re-enabled later.
            _combined_reminder = (discovery_reminder + "\n\n"
                                  + recipes_reminder).strip()
            llm_history = _splice_recipes_reminder(llm_history, _combined_reminder)
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
                from core.llm import _RECENT_PREP_SHAS
                _RECENT_PREP_SHAS.append(_hist_sha)   # arms the wire tripwire
            except Exception:  # noqa: BLE001
                pass

            # #13 — never send a request whose last message is an assistant
            # turn. The Anthropic API treats that as a prefill ("continue this
            # assistant turn"), which the OAuth/Claude-Code model rejects with
            # a 400 ("the conversation must end with a user message"). It also
            # means there is nothing for the model to answer — the loop only
            # reaches here after a user/tool_result, so a trailing assistant
            # turn is upstream history corruption (the 2026-06 cross-project
            # race, #15). Halt cleanly instead of crashing: the existing
            # assistant message IS the reply. Loud log so any NEW source of
            # this state stays visible after #15.
            if llm_history and llm_history[-1].get("role") != "user":
                print(f"[guide] WARN halting run={turn.run_id}: history ends "
                      f"with role={llm_history[-1].get('role')!r} — nothing to "
                      f"answer (see #13/#15)", flush=True)
                turn.transition(TurnState.DONE); checkpoint(turn)
                yield sse(wire.usage(input=usage_in, output=usage_out,
                                     cache_read=usage_cr, cache_write=usage_cw))
                yield sse(wire.done())
                return

            # Per-turn phase: open the model stream + dispatch tools
            # via DirectAPIRuntime.run_turn. The closure below maps
            # the four halt types (plan / clarify / approval /
            # deferred) to the SSE wire shapes the frontend speaks.
            # Tool execution + progress streaming go through the
            # runtime; guide.py just consumes events + translates.
            # R-2.2: pick the runtime from the agent spec (default 'direct').
            # ABA_FAKE_SESSION / ABA_RUNTIME_OVERRIDE env vars take priority.
            from core.runtime.agent import make_runtime
            _runtime = make_runtime(spec, model=guide_model)
            # Reset per-turn state each iteration of the outer while.
            _tool_input_by_id: dict[str, dict] = {}
            _tool_name_by_id: dict[str, str] = {}
            _tool_result_blocks: list[dict] = []
            _pending_halt_signal: str | None = None
            _stop_reason: str | None = None
            _assistant_blocks: list[dict] = []
            _tool_calls_this_turn: list[str] = []
            _emitted_tool_start: set[str] = set()

            # tool_executor closure — owns approval + background +
            # present_plan + ask_clarification + normal dispatch.
            # Returns the result envelope; runtime catches the halt
            # markers (_runtime_halt_before / _after / deferred).
            async def _tool_executor(name: str, tool_input: dict, ctx: dict) -> dict:
                tool_use_id = ctx["tool_use_id"]

                # present_plan: validate + persist plan entity +
                # fire on_plan_presented hook. Return ack envelope
                # + halt-after.
                if name == "present_plan":
                    from core.planning.validator import normalize_plan, validate_plan
                    from core.graph.entities import create_entity
                    _inp = tool_input if isinstance(tool_input, dict) else {}
                    plan = validate_plan(normalize_plan(_inp))
                    from core.graph.derivation import manual
                    from content.bio.lifecycle.runs import agent_actor_for_thread
                    plan_eid = create_entity(
                        entity_type="plan",
                        title=plan.title or "Plan",
                        parent_entity_id=focus_entity_id,
                        derivation=manual(),   # Phase 2B
                        actor=agent_actor_for_thread(store_tid),
                        metadata={
                            "thread_id": store_tid,
                            "plan": plan.to_dict(),
                            "plan_lifecycle": "validated",
                        },
                    )
                    turn.plan_entity_id = plan_eid
                    dispatch("on_plan_presented", {
                        "thread_id": store_tid,
                        "plan_title": plan.title or "Analysis run",
                        "focus_entity_id": focus_entity_id,
                        "plan_entity_id": plan_eid,
                    })
                    # The Run (and its cwd) just opened above. Compute the workspace
                    # orientation NOW — canonical dataset paths + cwd — and attach it
                    # to the result, so the agent has it BEFORE its first run_python
                    # on resume, instead of guessing the path, erroring, and only
                    # THEN seeing the orientation prepended to that run's output.
                    # Orientation is a CONTENT computation (bio run-workspace paths):
                    # ask for it through the core/services seam so the orchestrator
                    # doesn't import content privates. Best-effort — "" if no pack /
                    # it raises (modularity_audit3 Item 1, Phase 2a).
                    from core.services import call_service
                    from core import projects as _projects
                    _pid = _projects.current()
                    _orient = call_service(
                        "plan_orientation_preamble", str(_pid), str(store_tid), default="")
                    _note = ("Plan shown to the user with Go/Adjust controls. "
                             "Wait for their decision before executing.")
                    if _orient:
                        _note += ("\n\nWhen you resume, your first run_python runs in the "
                                  "new Run's working dir — use these canonical paths verbatim "
                                  "(do NOT guess the directory):\n" + _orient)
                    # Pipeline steps → enrich with a schema-derived editable
                    # param_form so the plan card renders a launch form inline
                    # (merged launch-form, nfcore.md §7c). Best-effort; the live
                    # card reads ev.steps from this emitted payload.
                    _plan_dict = plan.to_dict()
                    try:
                        from core.exec.nextflow_schema import enrich_plan_steps
                        _plan_dict["steps"] = enrich_plan_steps(_plan_dict.get("steps") or [])
                    except Exception:  # noqa: BLE001 — enrichment is best-effort
                        pass
                    return {
                        "status": "presented",
                        "plan_entity_id": plan_eid,
                        "note": _note,
                        "concerns": [c.to_dict() for c in plan.concerns],
                        "_runtime_halt_after": "plan",
                        "_emit_sse_post": wire.plan(entity_id=plan_eid, **_plan_dict),
                    }

                # ask_clarification: question validate, ack envelope.
                if name == "ask_clarification":
                    _inp = tool_input if isinstance(tool_input, dict) else {}
                    question = str(_inp.get("question") or "").strip()
                    if not question:
                        return {"status": "error",
                                "note": "ask_clarification needs a non-empty `question`."}
                    _post = wire.clarification_pending(question=question,
                                                       tool_use_id=tool_use_id,
                                                       run_id=turn.run_id)
                    # Option 1 enable-flow (misc/modules.md): when the question is about a
                    # turned-off module, carry structured Enable options so the UI renders
                    # one-click buttons (the USER enables — the agent never can). The turn
                    # resumes when the user picks.
                    _em = str(_inp.get("enable_module") or "").strip()
                    if _em:
                        try:
                            from core.modules import registry as _mreg
                            _ms = _mreg.get(_em)
                            if _ms:
                                _post["enable"] = {
                                    "module": _ms.id, "title": _ms.title,
                                    "options": [{"mode": "on", "label": "Enable · On"},
                                                {"mode": "first_use", "label": "Enable · First use"}],
                                }
                        except Exception:  # noqa: BLE001
                            pass
                    return {
                        "status": "asked",
                        "note": "Question shown to the user. Stop here and "
                                "wait for their reply before continuing.",
                        "_runtime_halt_after": "clarify",
                        "_emit_sse_post": _post,
                    }

                # Approval gate — halt BEFORE (no tool_result block;
                # held tool runs only after user approves at /resume).
                from core.runtime.approval import needs_approval
                tool_schema = next((t for t in active_tools if t["name"] == name), None)
                policy = tool_schema.get("approval_policy") if tool_schema else None
                if needs_approval(policy, store_tid or "default", name):
                    turn.pending_approval = {
                        "tool_name": name,
                        "tool_input": tool_input if isinstance(tool_input, dict) else {},
                        "tool_use_id": tool_use_id,
                        "policy": policy,
                    }
                    summary = _summarize_tool_input(name, tool_input)
                    return {
                        "_runtime_halt_before": "approval",
                        "_emit_sse_at_halt": wire.approval_pending(
                            tool_name=name, summary=summary,
                            tool_use_id=tool_use_id, run_id=turn.run_id,
                            policy=policy),
                    }

                # Background run_python: submit job, return queued
                # result. The job runner's webhook fills the real
                # result later; in the meantime the model sees the
                # queued ack so it can decide what to do next.
                if (name == "run_python" and isinstance(tool_input, dict)
                        and tool_input.get("background")):
                    from core import projects as _projects
                    from content.bio.tools.run_exec import bg_submit_kwargs
                    _pid = _projects.current() or "default"
                    _arid_ctx = {"thread_id": str(store_tid), "active_run_id": None}
                    dispatch("on_background_job_submit", _arid_ctx)
                    # Thread the agent's resource estimate (est_gpu/cores/mem/runtime),
                    # execution target, isolated env, and an estimate-sized timeout —
                    # via the SAME helper the non-intercept run_python path uses. Before
                    # this, the intercept dropped the estimate, so a GPU-flagged job
                    # (est_gpu=true) couldn't be GPU-placed (prj_6d986f40).
                    # Submission runs OFF the main loop: it may block (sbatch
                    # I/O; in pack mode a session snapshot+realize for the
                    # job's frozen env — W3.4).
                    import functools as _ft
                    try:
                        job = await asyncio.get_running_loop().run_in_executor(
                            None, _ft.partial(
                                submit_python_job,
                                code=tool_input.get("code", ""),
                                title=tool_input.get("title") or "Background analysis",
                                focus_entity_id=focus_entity_id,
                                project_id=str(_pid),
                                thread_id=str(store_tid),
                                run_id=_arid_ctx.get("active_run_id"),
                                **bg_submit_kwargs(tool_input, _pid)))
                    except Exception as e:  # noqa: BLE001
                        # same hardening as run_exec's own background branch:
                        # an unknown site / substrate error must come back as
                        # a TOOL result the agent can act on, not crash the
                        # turn (the row, if created, is marked failed by
                        # submit.py's _mark_submit_failed)
                        return {"status": "error",
                                "note": f"background submit failed: "
                                        f"{getattr(e, 'detail', None) or e}"}
                    return {
                        "job_id": job["id"],
                        "status": "queued",
                        "note": "Submitted as a background job. Figures "
                                "will register when it finishes; watch "
                                "the Queues panel.",
                        "_emit_sse_pre": wire.job_submitted(job=job),
                    }

                # Normal dispatch: ensure stream buffer, run in
                # thread, parse result, record telemetry.
                try:
                    from core.runtime import tool_stream_buffer as _tsb_pre
                    _tsb_pre.ensure(turn.run_id, tool_use_id)
                except Exception:  # noqa: BLE001
                    pass
                tool_ctx = {**ctx,
                            "active_tools": active_tools,
                            "thread_id": store_tid,
                            "focus_entity_id": focus_entity_id,
                            "session_id": session_id,
                            "recipe_ctx": recipe_ctx,
                            "intent": eff_intent,
                            "cancel_token": cancel_token}
                import datetime as _dt
                _t_start = _dt.datetime.now(_dt.timezone.utc)
                _loop = asyncio.get_running_loop()
                result_str = await _loop.run_in_executor(
                    None, _exec_tool, name, tool_input, tool_ctx)
                _t_end = _dt.datetime.now(_dt.timezone.utc)
                result_obj = json.loads(result_str)
                # Telemetry record (best-effort; mirrors legacy path).
                _telem_status = (
                    "deferred" if isinstance(result_obj, dict) and result_obj.get("deferred")
                    else "error" if isinstance(result_obj, dict) and (result_obj.get("error") or result_obj.get("status") == "error")
                    else "ok"
                )
                _telem_err = None
                if _telem_status == "error" and isinstance(result_obj, dict):
                    _telem_err = str(result_obj.get("error") or result_obj.get("note") or "")[:300]
                    try:
                        from core.runtime import tool_stream_buffer as _tsb_err
                        _tsb_err.record_error(turn.run_id, tool_use_id,
                                              f"[tool error] {_telem_err}")
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    from core.runtime.tool_telemetry import record as _record_invocation
                    _record_invocation(
                        run_id=turn.run_id, agent_spec=turn.agent_spec_name,
                        tool_name=name, input_=tool_input,
                        started_at=_t_start.isoformat(),
                        ended_at=_t_end.isoformat(),
                        duration_ms=int((_t_end - _t_start).total_seconds() * 1000),
                        status=_telem_status, error_summary=_telem_err,
                    )
                except Exception:  # noqa: BLE001
                    pass
                return result_obj

            _req = RuntimeRequest(
                history=llm_history,
                tools=active_tools,
                system=SystemSpec(stable=system, dynamic=dynamic_sys),
                model=guide_model,
                max_tokens=8192,
                ctx={},
                cancel=cancel_token,
            )

            _t_iter_begin = _time.perf_counter()
            _t_first_delta: float | None = None
            _t_first_tool:  float | None = None
            async for ev in _runtime.run_turn(_req, _tool_executor,
                                               halt_on_tools=frozenset()):
                if _debug_timing and _t_first_delta is None and isinstance(ev, TextDelta):
                    _t_first_delta = _time.perf_counter()
                    print(f"[guide-timing] iter_TTFT={(_t_first_delta-_t_iter_begin)*1000:.0f}ms",
                          flush=True)
                if _debug_timing and _t_first_tool is None and isinstance(ev, ToolUseStart):
                    _t_first_tool = _time.perf_counter()
                    print(f"[guide-timing] iter_TTFTool={(_t_first_tool-_t_iter_begin)*1000:.0f}ms",
                          flush=True)
                # Capture tool_use_id → input/name for progress event
                # translation (which only has tool_use_id).
                if hasattr(ev, "tool_use_id"):
                    if hasattr(ev, "input"):
                        _tool_input_by_id[ev.tool_use_id] = ev.input
                    if hasattr(ev, "tool_name"):
                        _tool_name_by_id[ev.tool_use_id] = ev.tool_name

                if isinstance(ev, TextDelta):
                    yield sse(wire.delta(text=ev.text))
                elif isinstance(ev, ToolUseStart):
                    # Emit tool_start as soon as the model issues the
                    # tool_use block so the UI renders the running chip
                    # + has a streaming block for incoming tool_progress
                    # lines to attach to. The ToolResult-time emit below
                    # stays as an idempotent backstop (guarded by
                    # _emitted_tool_start) for halt-before paths that
                    # never reach a normal ToolResult.
                    if ev.tool_use_id not in _emitted_tool_start:
                        yield sse(wire.tool_start(name=ev.tool_name,
                                                  input=ev.input,
                                                  tool_use_id=ev.tool_use_id))
                        _emitted_tool_start.add(ev.tool_use_id)
                elif isinstance(ev, _RetryNotice):
                    print(f"[guide] transient API error (attempt {ev.attempt}/{ev.max_retries}), "
                          f"retrying in {ev.backoff_s}s: {ev.error}")
                    yield sse(wire.notice(
                        text=f"Model is busy — retrying ({ev.attempt}/{ev.max_retries})…"))
                elif isinstance(ev, _ToolProgress):
                    _tname = _tool_name_by_id.get(ev.tool_use_id, "")
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    if payload.get("type") == "chunk":
                        from core.runtime import tool_stream_buffer as _tsb
                        _tsb.record_chunk(
                            run_id=turn.run_id, tool_use_id=ev.tool_use_id,
                            stream=payload.get("stream", "stdout"),
                            text=payload.get("text", ""),
                            bytes_total=payload.get("bytes_total", 0),
                            elapsed_s=payload.get("elapsed_s", 0.0),
                        )
                        yield sse(wire.tool_chunk(
                            tool_use_id=ev.tool_use_id,
                            stream=payload.get("stream", "stdout"),
                            text=payload.get("text", ""),
                            bytes_total=payload.get("bytes_total", 0),
                            elapsed_s=payload.get("elapsed_s", 0.0)))
                    else:
                        yield sse(wire.tool_progress(
                            name=_tname, tool_use_id=ev.tool_use_id,
                            message=payload.get("message"),
                            phase=payload.get("phase")))
                    await asyncio.sleep(0)
                elif isinstance(ev, _StreamCompleted):
                    final_msg = ev.final_msg
                    _assistant_blocks = ev.assistant_blocks
                    _tool_calls_this_turn = ev.tool_calls_this_turn
                    _stop_reason = ev.stop_reason
                    if ev.usage_delta:
                        usage_in += ev.usage_delta.get("input", 0)
                        usage_out += ev.usage_delta.get("output", 0)
                        usage_cr += ev.usage_delta.get("cache_read", 0)
                        usage_cw += ev.usage_delta.get("cache_write", 0)
                    # truncated_tool_uses inference + agent_note,
                    # mirrors legacy (post-block-extraction logic).
                    _truncated: list[str] = [
                        b["name"] for b in _assistant_blocks
                        if b["type"] == "tool_use" and not b["input"]
                        and _stop_reason == "max_tokens"
                    ]
                    if _truncated:
                        _names = ", ".join(_truncated)
                        agent_note = (
                            f"[system notice: your previous turn's tool call(s) ({_names}) hit the "
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
                                       entity_id=entity_id,
                                       focus_entity_id=focus_entity_id,
                                       thread_id=store_tid)
                        ui_note = (
                            f"⚠ The {_names} call was cut off by the per-turn output cap. The agent "
                            "has been told to break the content into smaller writes — ask it to retry."
                        )
                        yield sse(wire.notice(text=ui_note))
                    # Degenerate-empty generation (observed live: a 5-token
                    # emission with no text and no tool_use) must not land
                    # an empty assistant message in history — that's the
                    # blocks-less shape the renderer's F4 guard degrades,
                    # and it poisons later turns' history shape. Whitespace-
                    # only text counts as empty (review F4: a "\n\n" block
                    # otherwise persists AND dead-ends the retry on the
                    # trailing-assistant guard).
                    _gen_has_output = any(
                        b.get("type") == "tool_use"
                        or (b.get("type") == "text"
                            and (b.get("text") or "").strip())
                        for b in _assistant_blocks)
                    if _gen_has_output:
                        _turn_produced_output = True
                        append_message("assistant", _assistant_blocks,
                                       entity_id=entity_id,
                                       focus_entity_id=focus_entity_id,
                                       thread_id=store_tid)
                    text_out = "".join(b["text"] for b in _assistant_blocks
                                       if b["type"] == "text")
                    log_context_assembly(
                        session_id=session_id,
                        turn_index=turn_index,
                        focus_entity_id=focus_entity_id,
                        focus_entity_type=focus_type,
                        fields_preloaded=fields_preloaded,
                        tool_calls=_tool_calls_this_turn,
                        turn_text_len=len(text_out),
                        manifest=manifest.to_dict(),
                    )
                    turn_index += 1
                    turn.turn_index = turn_index
                    turn.usage_in = usage_in; turn.usage_out = usage_out
                    turn.usage_cache_read = usage_cr; turn.usage_cache_write = usage_cw
                    # a1: one metric row per LLM generation (round-trip) — round-trips,
                    # parallelism (tool_use blocks this gen), and per-gen cache tokens.
                    try:
                        from core.runtime.tool_telemetry import record_generation
                        _ud = ev.usage_delta or {}
                        record_generation(
                            run_id=turn.run_id, agent_spec=turn.agent_spec_name,
                            gen_index=turn_index,
                            n_tool_uses=sum(1 for b in _assistant_blocks
                                            if b.get("type") == "tool_use"),
                            input_tokens=_ud.get("input", 0), output_tokens=_ud.get("output", 0),
                            cache_read=_ud.get("cache_read", 0), cache_write=_ud.get("cache_write", 0),
                            stop_reason=_stop_reason)
                    except Exception:  # noqa: BLE001 — telemetry never breaks a turn
                        pass
                    history = get_messages(entity_id, thread_id=store_tid)
                    if _stop_reason != "tool_use":
                        # No tools — outer loop will break via the
                        # legacy fall-through after this `continue`.
                        # Actually: we need an end-of-turn break. We
                        # signal it by leaving _stop_reason captured
                        # and breaking after the event loop.
                        pass
                    else:
                        # About to dispatch tools — track the in-flight
                        # set so the reaper can synthesize tool_results
                        # if the process dies (A1).
                        turn.pending_tool_ids = [
                            b["id"] for b in _assistant_blocks
                            if b["type"] == "tool_use"
                        ]
                        turn.transition(TurnState.EXECUTING_TOOLS); checkpoint(turn)
                elif isinstance(ev, ToolResult):
                    # Emit pre-SSE (job_submitted for background path).
                    _envelope = ev.result if isinstance(ev.result, dict) else {}
                    if "_emit_sse_pre" in _envelope:
                        yield sse(_envelope.pop("_emit_sse_pre"))
                    # tool_start fires here, AFTER halt-before checks
                    # in the closure have had their say. Idempotent
                    # via _emitted_tool_start guard.
                    if ev.tool_use_id not in _emitted_tool_start:
                        yield sse(wire.tool_start(
                            name=ev.tool_name,
                            input=_tool_input_by_id.get(ev.tool_use_id, {}),
                            tool_use_id=ev.tool_use_id))
                        _emitted_tool_start.add(ev.tool_use_id)
                    # on_post_tool hook + entity_registered emit.
                    hook_ctx = {
                        "tool_name": ev.tool_name,
                        "tool_input": _tool_input_by_id.get(ev.tool_use_id, {}),
                        "result_obj": _envelope,
                        "focus_entity_id": focus_entity_id,
                        "analysis_ctx": analysis_ctx,
                        "thread_id": store_tid,
                        "new_entities": [],
                        "parent_run_id": turn.run_id,
                    }
                    dispatch("on_post_tool", hook_ctx)
                    for _ent in hook_ctx["new_entities"]:
                        yield sse(wire.entity_registered(entity=_ent))
                    # create_scenario surfaces its entity outside the
                    # artifact registrar path.
                    if (ev.tool_name == "create_scenario"
                            and isinstance(_envelope, dict)
                            and _envelope.get("scenario")):
                        from core.graph.entities import get_entity as _ge
                        _ent = _ge(_envelope["scenario"]["id"])
                        if _ent:
                            yield sse(wire.entity_registered(entity=_ent))
                    # Mark live-tail buffer done so TTL drops to 5min.
                    try:
                        from core.runtime import tool_stream_buffer as _tsb
                        _tsb.mark_done(turn.run_id, ev.tool_use_id)
                    except Exception:  # noqa: BLE001
                        pass
                    yield sse(wire.tool_result(name=ev.tool_name, result=_envelope,
                                               tool_use_id=ev.tool_use_id))
                    # Vision-blocks envelope (view_figure, etc.):
                    # passthrough as the tool_result's `content`.
                    if (isinstance(_envelope, dict)
                            and isinstance(_envelope.get("_vision_blocks"), list)):
                        _tool_result_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": ev.tool_use_id,
                            "content": _envelope["_vision_blocks"],
                        })
                    else:
                        _tool_result_blocks.append({
                            "type": "tool_result",
                            "tool_use_id": ev.tool_use_id,
                            "content": json.dumps(_envelope),
                        })
                    # Post-SSE for halt-after envelopes (plan /
                    # clarification_pending). The TurnHalt event for
                    # the same halt-after comes RIGHT after this.
                    if "_emit_sse_post" in _envelope:
                        yield sse(_envelope["_emit_sse_post"])
                elif isinstance(ev, TurnHalt):
                    if ev.reason in ('plan', 'clarify'):
                        _pending_halt_signal = ev.reason
                    elif ev.reason == 'approval':
                        _pending_halt_signal = "approval"
                        if isinstance(ev.detail, dict) and "_emit_sse_at_halt" in ev.detail:
                            yield sse(ev.detail["_emit_sse_at_halt"])
                    elif ev.reason == 'deferred':
                        turn.pending_deferred = {
                            "tool_name": ev.detail.get("tool_name"),
                            "tool_use_id": ev.detail.get("tool_use_id"),
                            "deferred_id": ev.detail.get("deferred_id"),
                            "started_at": __import__("datetime").datetime.now(
                                __import__("datetime").timezone.utc).isoformat(),
                            "timeout_s": int(ev.detail.get("timeout_s") or 0) or None,
                        }
                        yield sse(wire.deferred_tool_pending(
                            tool_name=ev.detail.get("tool_name"),
                            deferred_id=ev.detail.get("deferred_id"),
                            tool_use_id=ev.detail.get("tool_use_id"),
                            run_id=turn.run_id))
                        if _tool_result_blocks:
                            append_message("user", _tool_result_blocks,
                                           entity_id=entity_id,
                                           focus_entity_id=focus_entity_id,
                                           thread_id=store_tid)
                        turn.transition(TurnState.AWAITING_TOOL_RESULT)
                        checkpoint(turn)
                        yield sse(wire.usage(input=usage_in, output=usage_out,
                                             cache_read=usage_cr, cache_write=usage_cw))
                        yield sse(wire.done())
                        return
                    elif ev.reason == 'cancelled':
                        # Cancel path — the outer-loop top will see
                        # cancel_token.cancelled and emit cancelled
                        # SSE on the next iteration's pre-check.
                        break
                    elif ev.reason == 'error':
                        # A runtime-level model error (e.g. an OpenAI/Codex 400).
                        # Without this branch it was silently swallowed → the UI
                        # rendered an empty message ("couldn't be displayed").
                        _d = ev.detail if isinstance(ev.detail, dict) else {}
                        _raw = _d.get("message") or "The model call failed."
                        yield sse(wire.error(text=_raw, detail=_d.get("type") or ""))
                        turn.transition(TurnState.FAILED)
                        turn.error = {"type": _d.get("type") or "ModelError", "message": _raw}
                        checkpoint(turn)
                        yield sse(wire.usage(input=usage_in, output=usage_out,
                                             cache_read=usage_cr, cache_write=usage_cw))
                        yield sse(wire.done())
                        return
                elif isinstance(ev, TurnDone):
                    # Phase ended; do nothing here (we already
                    # updated state in _StreamCompleted).
                    pass

            # End of event loop. Persist tool_result_blocks (mirrors
            # legacy 1199-1207). Skip if empty (= approval held the
            # first tool, nothing ran).
            if _tool_result_blocks:
                append_message("user", _tool_result_blocks,
                               entity_id=entity_id,
                               focus_entity_id=focus_entity_id,
                               thread_id=store_tid)
            if _pending_halt_signal != "approval":
                turn.pending_tool_ids = []
            checkpoint(turn)
            history = get_messages(entity_id, thread_id=store_tid)
            # End-of-turn break: no tool_use means the model is done.
            if _stop_reason != "tool_use":
                # Degenerate-empty defense: a TURN that produced no output
                # at all (no tool_use, no non-whitespace text in ANY
                # generation — observed live 2026-07-19: user asked for a
                # pin, model emitted 5 tokens of nothing, turn ended in
                # silence) gets ONE fresh retry; a second emptiness lands
                # an honest marker — the user must never get wordless
                # silence. A turn that DID speak or run tools earlier and
                # merely closed on an empty generation is normal (the
                # empty close is simply not persisted) — no retry, no
                # marker (review F3: the marker would be a lie there).
                if not _turn_produced_output:
                    if not _empty_retry_done:
                        _empty_retry_done = True
                        continue
                    try:
                        append_message(
                            "assistant",
                            [{"type": "text",
                              "text": "*(I produced no response — "
                                      "please ask that again.)*"}],
                            entity_id=entity_id,
                            focus_entity_id=focus_entity_id,
                            thread_id=store_tid)
                        yield sse(wire.notice(
                            text="The agent produced an empty response; "
                                 "ask again to retry."))
                    except Exception:  # noqa: BLE001
                        pass
                break
            # Halt-signal break-out (same shape as legacy 1217+).
            if _pending_halt_signal:
                turn.transition(TurnState.AWAITING_USER)
                turn.pending_user_signal = _pending_halt_signal
                checkpoint(turn)
                yield sse(wire.usage(input=usage_in, output=usage_out,
                                     cache_read=usage_cr, cache_write=usage_cw))
                yield sse(wire.done())
                return
            continue   # next outer iteration

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
            yield sse(wire.suggestion_logged(trigger="end_of_session",
                                             entity_type=focus_type))

        if turn.state != TurnState.AWAITING_USER:
            turn.transition(TurnState.DONE)
            checkpoint(turn)
            # #160: if this turn was driving a plan's execution, mark the
            # plan completed. Idempotent + safe on a missing entity.
            if turn.plan_entity_id:
                dispatch("on_plan_complete", {"plan_entity_id": turn.plan_entity_id,
                                              "thread_id": getattr(turn, "thread_id", None)})
        yield sse(wire.usage(input=usage_in, output=usage_out,
                             cache_read=usage_cr, cache_write=usage_cw))
        yield sse(wire.done())

    except Exception as e:
        print(f"[guide] stream_response failed: {type(e).__name__}: {e}")
        turn.error = {"type": type(e).__name__, "message": str(e)}
        turn.transition(TurnState.FAILED)
        checkpoint(turn)
        if turn.plan_entity_id:
            dispatch("on_plan_failed", {"plan_entity_id": turn.plan_entity_id})
        # Turn-end reconciliation must run on the FAILED path too — a turn that
        # crashed AFTER producing outputs is exactly when the durable keep
        # matters (on_stop only fires on the success branch).
        dispatch("on_turn_failed", {"thread_id": getattr(turn, "thread_id", None),
                                    "plan_entity_id": turn.plan_entity_id})
        yield sse(wire.error(text=_friendly_error(e),
                             detail=f"{type(e).__name__}: {e}"))
        yield sse(wire.usage(input=usage_in, output=usage_out,
                             cache_read=usage_cr, cache_write=usage_cw))
        yield sse(wire.done())
    finally:
        # Always release the cancel token — leaking it would keep stale
        # interrupters reachable and (via the registry) a re-entrant
        # cancel on this run_id would fire them against now-defunct
        # processes/connections. The TurnSink is closed by the executor's
        # `_drain` finally (turn_executor.py); we don't touch it here.
        _cancel.release(turn.run_id)


# --- Reasoning-plane continuation port ---------------------------------------------------
# Register this module's turn loop as the continuation handler so a finished background job
# can re-enter the agent loop WITHOUT core importing guide — dissolving the core.jobs→guide
# up-edge (modularity_audit3 Item 1; see core/reasoning_port.py + docs/arch/jobs-and-hpc.md).
# Registered at import: main.py imports guide before startup(), so the port is live before
# reconcile_jobs / the worker / the Slurm poll loop can fire any continuation.
from core.reasoning_port import register_continuation as _register_continuation


def _continuation_turn_body(cont_text, *, focus_entity_id, thread_id, run_id):
    """The reasoning-plane continuation handler: hand core.jobs the turn body generator."""
    return stream_response(cont_text, focus_entity_id=focus_entity_id,
                           thread_id=thread_id, run_id=run_id)


_register_continuation(_continuation_turn_body)
