"""Message-history hygiene for the LLM payload (WU-5 extraction).

The agent loop in `guide.py:stream_response` needs to do a few things to the
conversation history BEFORE handing it to the Anthropic API:

  - **Dedupe tool_results** — the API rejects multiple `tool_result`
    blocks sharing the same `tool_use_id`. Duplicates happen when the
    reaper writes a synthetic 'interrupted' fill and the real result
    lands later (long thread, resume race).
  - **Repair orphan pairs** — every assistant `tool_use` must be
    followed by a user `tool_result` with the matching id. Forward
    orphans (assistant→assistant) get a synthetic interrupted fill;
    backward orphans (tool_result with no preceding tool_use, e.g.
    after a rolling-summary cut) get dropped.
  - **Splice the recipes reminder** — CC-convergence Phase 4 prepends
    the recipes catalog (wrapped in `<system-reminder>`) to the latest
    user-text message of the LLM payload, so the model re-sees it
    each turn without it persisting in history.

Pure functions of (list[dict]) → list[dict]. No I/O, no globals.
"""
from __future__ import annotations
import json


# Content blocks that exist ONLY for the web UI and must never reach the model
# (the Anthropic SDK rejects unknown block types). The `attachments` chip block
# is persisted for re-render; the agent gets the files via the ephemeral context
# note + vision blocks injected in guide.py instead.
_UI_ONLY_BLOCK_TYPES = {"attachments"}


def strip_ui_blocks(content):
    """Drop UI-only content blocks (e.g. the `attachments` chip block) from a
    message's content. They exist purely for the web UI and the Anthropic SDK
    rejects unknown block types — so this MUST run at every history→API boundary
    (see core/llm.py)."""
    if not isinstance(content, list):
        return content
    return [b for b in content
            if not (isinstance(b, dict) and b.get("type") in _UI_ONLY_BLOCK_TYPES)]


# The block `type`s the Anthropic Messages API accepts (from the API's own
# rejection message). The validity-guard test asserts api_messages never emits a
# type outside this set — so a UI/internal block can never silently reach the API
# again (the live 2026-06-28 `attachments` 400). Update only when the SDK adds a
# real block type.
ALLOWED_API_BLOCK_TYPES = frozenset({
    "text", "image", "document", "thinking", "redacted_thinking",
    "tool_use", "tool_result", "server_tool_use", "mid_conv_system",
    "search_result", "connector_text", "container_upload",
    "web_search_tool_result", "web_fetch_tool_result", "tool_search_tool_result",
    "code_execution_tool_result", "bash_code_execution_tool_result",
    "text_editor_code_execution_tool_result",
})


def drop_empty_text_blocks(content):
    """Remove empty/whitespace-only text blocks — the Anthropic API rejects them
    ("text content blocks must be non-empty"). They appear e.g. as the bare leading
    text block of an assistant turn that's really just a tool_use (an
    ask_clarification / plan halt), and break the turn on resume. Non-lists pass
    through."""
    if not isinstance(content, list):
        return content
    return [b for b in content
            if not (isinstance(b, dict) and b.get("type") == "text" and not (b.get("text") or "").strip())]


def api_messages(history: list) -> list:
    """THE single history→API transform: strip internal-bookkeeping fields + UI-only
    content blocks + empty text blocks down to the bare {role, content} the Anthropic
    SDK accepts. core/llm.py builds its `messages` from this — so the validity-guard
    test runs here."""
    out = []
    for m in history:
        content = drop_empty_text_blocks(strip_ui_blocks(m.get("content")))
        # If dropping empties the content entirely, keep a minimal non-empty block so
        # the turn (and user/assistant alternation) is preserved — never send [].
        if isinstance(content, list) and not content:
            content = [{"type": "text", "text": "(continuing)"}]
        out.append({"role": m["role"], "content": content})
    return out


def is_interrupted_fill(block: dict) -> bool:
    """A reaper-synthesized 'interrupted' tool_result (vs a real one)."""
    c = block.get("content")
    if isinstance(c, str):
        try:
            j = json.loads(c)
        except (json.JSONDecodeError, TypeError):
            return False
        return isinstance(j, dict) and j.get("status") == "interrupted"
    return False


def dedup_tool_results(messages: list) -> list:
    """Collapse multiple tool_result blocks that share a tool_use_id down to
    one — the Anthropic API rejects duplicates ("each tool_use must have a
    single result"). Duplicates arise when a reaper 'interrupted' synth fill
    coexists with a real result on a long/shared thread (or a resume race).
    Prefer the REAL result over an interrupted synth; otherwise keep the first.
    Idempotent; drops messages left empty."""
    real_ids = {
        b.get("tool_use_id")
        for m in messages for b in (m.get("content") or [])
        if isinstance(b, dict) and b.get("type") == "tool_result" and not is_interrupted_fill(b)
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
                if tid in real_ids and is_interrupted_fill(b):
                    continue                      # skip synth; a real result exists elsewhere
                seen.add(tid)
            new_content.append(b)
        if new_content:
            out.append(dict(m, content=new_content))
    return out


def splice_recipes_reminder(messages: list, reminder: str) -> list:
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


def ensure_tool_pair_completeness(messages: list) -> list:
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
    messages = dedup_tool_results(messages)
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
                    interrupted = json.dumps({
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
