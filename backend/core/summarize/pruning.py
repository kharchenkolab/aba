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

# Defaults sourced from core.config (single tunable source — env-overridable).
# K = "the last K of each, keep verbatim". Bumped from K=6 → K=30 (2026-06-03)
# to stop aging out tool_results inside the cached prefix during normal recipe
# execution. See core/config.py:HISTORY_K_TOOL_KEEP for the cache-interaction
# rationale.
from core.config import HISTORY_K_TOOL_KEEP, HISTORY_K_TEXT_KEEP
K_TOOL_KEEP_DEFAULT = HISTORY_K_TOOL_KEEP
K_TEXT_KEEP_DEFAULT = HISTORY_K_TEXT_KEEP

# Tools whose tool_result we ALWAYS keep verbatim regardless of position
# — they carry structural / navigation info the agent needs to find
# files, see what's in the project, etc. Cheap to keep (their results
# are short), high cost to lose. Skill/read_skill bodies are reference
# material the recipe executes against turn after turn — aging them out
# breaks recipe execution and dirties the cache prefix.
_ALWAYS_KEEP_TOOLS = frozenset({
    "list_data_files", "list_entities",
    "open_run", "close_run", "present_plan",
    "register_dataset", "register_reference",
    "Skill",
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
    """Render a one-line summary from an older tool_result's content. Tries
    to pull out returncode + produced artifacts (plots, tables) from a JSON
    payload; falls back to a generic name+ok line.

    The output marker is `[earlier]`, not `[STUB]` — the latter collides
    with eval-mode training in current frontier models and triggered Opus
    to refuse execution mid-session (2026-06-01)."""
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

    # NB: don't say "STUB" / "benchmark" / "fixture" in this marker — those
    # tokens collide with the model's eval-mode training and have caused Opus
    # to refuse plan execution mid-session, emitting things like "[STUB] Plan
    # execution suspended for this benchmark turn." (verified live 2026-06-01).
    # This is a real summary of a real prior call, not a test stub.
    bits = [f"[earlier] {tool_name}"]
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


def _quantized_stub_count(n: int, keep: int, batch: int) -> int:
    """How many of the OLDEST items to demote: 0 until n exceeds `keep`, then
    the largest multiple of `batch` ≤ (n - keep). Monotonic in n, so over an
    append-only history previously-demoted items stay demoted and the output
    changes only at batch boundaries (prefix-stability between epochs)."""
    if keep <= 0:
        return n
    over = n - keep
    if over <= 0 or batch <= 0:
        return max(0, over)
    return (over // batch) * batch


def _demote_image_blocks(content: list, tool_name: str, tu_input: dict) -> list:
    """Replace image blocks in a tool_result's content with a small text stub
    naming how to re-view. A vision payload is consumed by the model exactly
    once (the generation that looks at it); retained verbatim it costs its
    full base64 weight on EVERY subsequent request — the live incident held a
    ~1.3MB image in history, saturating the Tier-2 budget for ~11 generations.
    The reference (tool + path/id) keeps it one call away."""
    ref = ""
    for k in ("path", "entity_id", "name"):
        v = (tu_input or {}).get(k)
        if isinstance(v, str) and v:
            ref = f", {k}={v[:120]!r}"
            break
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "image":
            media = ((b.get("source") or {}).get("media_type")) or "image"
            out.append({"type": "text",
                        "text": f"[image demoted from context ({media}) — "
                                f"re-view via {tool_name}({ref.lstrip(', ')})]"})
        else:
            out.append(b)
    return out


def prune_transcript(
    messages: list[dict],
    *,
    k_tool_keep: int = K_TOOL_KEEP_DEFAULT,
    k_text_keep: int = K_TEXT_KEEP_DEFAULT,
    stub_batch: int | None = None,
    drop_batch: int | None = None,
    k_image_keep: int = 4,
) -> list[dict]:
    """Return a pruned copy of `messages`:
      - The most recent K_TOOL_KEEP tool_result blocks keep their content
        verbatim. Older tool_results get content replaced with a one-line
        summary (`[earlier] tool_name | …`). Tools in `_ALWAYS_KEEP_TOOLS`
        keep their content regardless of position (cheap + high-value).
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
    if stub_batch is None:
        stub_batch = max(1, k_tool_keep // 3)
    if drop_batch is None:
        drop_batch = max(1, k_text_keep // 2)

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

    # The set of positions whose CONTENT we'll keep: everything except the
    # QUANTIZED count of oldest results (plus always-keep tools anywhere).
    # Quantization is the caching fix (bug #2b): a plain `[-k:]` recency
    # window moves every generation, flipping one result verbatim→stub per
    # generation — a mid-list rewrite that re-bills the cached suffix on
    # every call once the history holds >k results. Stubbing in batches of
    # `stub_batch` makes the boundary advance in rare jumps: between jumps
    # the output is byte-identical over an append-only input (prefix-stable,
    # the property prompt caching needs), and each jump is one sanctioned
    # epoch rewrite. Costs at most `stub_batch` extra verbatim results held
    # beyond k — bounded, and far cheaper than the per-generation rebill.
    n_stub = _quantized_stub_count(len(tool_result_positions), k_tool_keep,
                                   stub_batch)
    keep_positions: set[tuple[int, int]] = \
        set(tool_result_positions[n_stub:])
    # Image payloads age out on a much shorter window than text (they're
    # consumed once, cost their full base64 weight per request thereafter, and
    # can single-handedly saturate the Tier-2 budget). Demoting happens near
    # the list TAIL, so each image rewrites exactly once with a tiny
    # invalidation radius — no batch quantization needed (unlike deep stubs).
    # Applies to ALL results beyond the image window, always-keep tools included
    # (the always-keep contract is about textual content, not payload bytes).
    img_demote: set[tuple[int, int]] = \
        set(tool_result_positions[:-k_image_keep] if k_image_keep > 0
            else tool_result_positions)
    for (i, j) in tool_result_positions:
        if (i, j) in keep_positions:
            continue
        tu_id = messages[i]["content"][j].get("tool_use_id")
        tu = tu_index.get(tu_id or "", {})
        if tu.get("name") in _ALWAYS_KEEP_TOOLS:
            keep_positions.add((i, j))

    # Second pass: pure-text assistant messages — same quantized boundary
    # (dropping a message shifts every later position, so an unquantized drop
    # is an even harsher prefix break than a stub flip).
    text_only_indices = [i for i, m in enumerate(messages) if _is_text_only_assistant(m)]
    n_drop = _quantized_stub_count(len(text_only_indices), k_text_keep,
                                   drop_batch)
    drop_text_indices: set[int] = set(text_only_indices[:n_drop])

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
            elif isinstance(b, dict) and b.get("type") == "tool_result" \
                    and (i, j) in img_demote \
                    and isinstance(b.get("content"), list) \
                    and any(isinstance(x, dict) and x.get("type") == "image"
                            for x in b["content"]):
                tu_id = b.get("tool_use_id") or ""
                tu = tu_index.get(tu_id, {})
                new_content.append({**b, "content": _demote_image_blocks(
                    b["content"], tu.get("name", "view_file"),
                    tu.get("input") or {})})
            else:
                new_content.append(b)
        out.append({**m, "content": new_content})

    return out
