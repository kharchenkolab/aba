"""Transcript pruning — Layer A of the history-compaction redesign
(misc/history_compaction_redesign.md §4.1).

Pure code, no LLM. Replace verbose tool_result CONTENTS in older turns
with one-line stubs, and drop chatty inter-step assistant prose
("Excellent! Step 1 complete!") that contains no tool_use. Keep tool_use
blocks, user messages, and run-boundary tool calls verbatim — the model
still sees WHAT was called and WHEN, just not the kilobyte of stdout.

Typical reduction on a long thread: 50KB → ~12KB without changing the
conversational shape the model sees. Most threads will never need a
subsequent LLM-based summary at all.

Safety:
- tool_use / tool_result PAIRS are preserved. We replace the result's
  content; we never remove the block itself.
- User messages are NEVER pruned — single source of truth.
- The output messages list still passes _ensure_tool_pair_completeness.
"""
from __future__ import annotations
import json
from typing import Any

# Defaults — tunable per call. K = "the last K of each, keep verbatim".
K_TOOL_KEEP_DEFAULT = 6
K_TEXT_KEEP_DEFAULT = 12

# Tools whose tool_result we ALWAYS keep verbatim regardless of position
# — they carry structural / navigation info the agent needs to find
# files, see what's in the project, etc. Cheap to keep (their results
# are short), high cost to lose.
_ALWAYS_KEEP_TOOLS = frozenset({
    "list_data_files", "list_entities",
    "open_run", "close_run", "present_plan",
    "register_dataset", "register_reference",
})


def _is_text_only_assistant(msg: dict) -> bool:
    """An assistant message whose content list contains ONLY text blocks
    (no tool_use, no image, no tool_result). These are the chatty
    inter-step narrations the agent emits between tool calls."""
    if msg.get("role") != "assistant":
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return True
    if not isinstance(content, list):
        return False
    for b in content:
        if isinstance(b, dict) and b.get("type") != "text":
            return False
    return True


def _tool_use_index(messages: list[dict]) -> dict[str, dict]:
    """Map tool_use_id → {name, input} from all assistant messages.
    Lets the stubber name the tool when looking at the matching
    tool_result, which only carries `tool_use_id`."""
    out: dict[str, dict] = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for b in c:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                tid = b.get("id")
                if tid:
                    out[tid] = {"name": b.get("name", "?"),
                                "input": b.get("input") or {}}
    return out


def _build_stub(tool_name: str, content: Any) -> str:
    """Render a one-line stub from a tool_result's content. Tries to
    pull out returncode + produced artifacts (plots, tables) from a
    JSON payload; falls back to a generic name+ok line."""
    # Decode if it's a JSON string. Most run_python / run_r results land
    # here. Best-effort — if it isn't JSON, treat as opaque text.
    payload: Any = None
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            payload = None
    elif isinstance(content, dict):
        payload = content

    bits = [f"[STUB] {tool_name}"]
    if isinstance(payload, dict):
        # returncode / error / status
        rc = payload.get("returncode")
        if rc is None and "error" in payload:
            bits.append("error")
        elif rc == 0 or payload.get("status") == "ok":
            bits.append("ok")
        elif rc is not None:
            bits.append(f"rc={rc}")
        # Plots: collect filename basenames (skip full /artifacts/<pid>/ URLs)
        plots = payload.get("plots") or []
        if isinstance(plots, list) and plots:
            names = []
            for p in plots[:4]:
                if isinstance(p, dict):
                    n = p.get("original_name") or p.get("url", "").rsplit("/", 1)[-1]
                    if n:
                        names.append(n)
            if names:
                bits.append("plots=[" + ", ".join(names) + "]")
        # Tables
        tables = payload.get("tables") or []
        if isinstance(tables, list) and tables:
            names = []
            for t in tables[:4]:
                if isinstance(t, dict):
                    n = t.get("original_name") or t.get("url", "").rsplit("/", 1)[-1]
                    if n:
                        names.append(n)
            if names:
                bits.append("tables=[" + ", ".join(names) + "]")
        # A short note field, if present
        note = payload.get("note") or payload.get("message")
        if isinstance(note, str) and note.strip():
            n = note.strip()
            if len(n) > 120:
                n = n[:117] + "..."
            bits.append(f"note: {n}")
    else:
        # Opaque content — treat as ok unless empty.
        if content:
            bits.append("ok")
    return " | ".join(bits)


def prune_transcript(
    messages: list[dict],
    *,
    k_tool_keep: int = K_TOOL_KEEP_DEFAULT,
    k_text_keep: int = K_TEXT_KEEP_DEFAULT,
) -> list[dict]:
    """Return a pruned copy of `messages`:
      - The most recent K_TOOL_KEEP tool_result blocks keep their content
        verbatim. Older tool_results get content replaced with a one-line
        STUB. Tools in `_ALWAYS_KEEP_TOOLS` keep their content regardless
        of position (cheap + high-value).
      - Pure-text assistant messages (no tool_use, no images) older than
        the most recent K_TEXT_KEEP such messages are DROPPED entirely.
        Their information is the agent's inter-step narration; the
        tool_use / tool_result pairs around them carry the actual state.
      - User messages, tool_use blocks, and recent tool_results stay
        unchanged.

    No LLM, no DB writes — pure transformation. Caller passes whatever
    `messages` shape it has; the function mutates copies (input list
    untouched)."""
    if not messages:
        return []

    tu_index = _tool_use_index(messages)

    # First pass: enumerate all tool_result blocks (with position) so we
    # can decide which "K most recent" keep their content.
    tool_result_positions: list[tuple[int, int]] = []  # (msg_idx, block_idx)
    for i, m in enumerate(messages):
        c = m.get("content")
        if not isinstance(c, list):
            continue
        for j, b in enumerate(c):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                tool_result_positions.append((i, j))

    # The set of positions whose CONTENT we'll keep (the last K, plus any
    # always-keep tools wherever they appear).
    keep_positions: set[tuple[int, int]] = set(tool_result_positions[-k_tool_keep:])
    for (i, j) in tool_result_positions:
        if (i, j) in keep_positions:
            continue
        tu_id = messages[i]["content"][j].get("tool_use_id")
        tu = tu_index.get(tu_id or "", {})
        if tu.get("name") in _ALWAYS_KEEP_TOOLS:
            keep_positions.add((i, j))

    # Second pass: enumerate pure-text assistant messages. The last
    # K_TEXT_KEEP keep; older ones get dropped.
    text_only_indices = [i for i, m in enumerate(messages) if _is_text_only_assistant(m)]
    drop_text_indices: set[int] = set(text_only_indices[:-k_text_keep] if k_text_keep > 0 else text_only_indices)

    # Build the output.
    out: list[dict] = []
    for i, m in enumerate(messages):
        if i in drop_text_indices:
            continue                                # drop pure-text narration
        c = m.get("content")
        if not isinstance(c, list):
            out.append(m)
            continue
        new_content: list = []
        for j, b in enumerate(c):
            if isinstance(b, dict) and b.get("type") == "tool_result" \
                    and (i, j) not in keep_positions:
                # Stub the content; keep type + tool_use_id so the pair
                # still validates.
                tu_id = b.get("tool_use_id") or ""
                tool_name = tu_index.get(tu_id, {}).get("name", "?")
                stub = _build_stub(tool_name, b.get("content"))
                new_content.append({
                    "type": "tool_result",
                    "tool_use_id": tu_id,
                    "content": stub,
                })
            else:
                new_content.append(b)
        out.append({**m, "content": new_content})

    return out
