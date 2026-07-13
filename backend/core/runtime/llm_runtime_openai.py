"""OpenAI-compatible runtime (Qwen3-8B on vLLM, etc.).

Mirrors DirectAPIRuntime's interface but talks to any OpenAI
ChatCompletions endpoint over the official `openai` SDK. The
intended target is a self-hosted vLLM serving a small open model
(Qwen3-8B today) at `localhost:8001`, but any compliant endpoint
works.

Three things this module owns:

  (1) Shape translation — Anthropic-style tool schemas and content
      blocks ↔ OpenAI's tool/message shape. Pure functions, no I/O.
  (2) The runtime adapter — implements LLMRuntime by streaming
      `client.chat.completions.create(stream=True)` and translating
      each chunk back into RuntimeEvent subclasses.
  (3) Reasoning-tag handling — Qwen3 emits `<think>…</think>` inline
      in `content`. We strip it from user-visible text (the model's
      tool_call still fires after the closing `</think>`, verified in
      the prj_phase0 smoke 2026-06-19). A stateful stripper handles
      tags that straddle delta chunks.

Phase 1 of the local-LLM integration. Phase 0 (viability smoke)
already established that Qwen3-8B is reliable at tool calling and
that `<think>` closes before `tool_calls` — that's the assumption
we encode here.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, AsyncIterator

from core import config
from core.runtime.llm_runtime import (
    LLMRuntime,
    RuntimeEvent,
    RuntimeRequest,
    TextDelta,
    ToolExecutor,
    ToolResult,
    ToolUseStart,
    TurnDone,
    TurnHalt,
)
# Private sentinel shared with DirectAPIRuntime. guide.py reads
# `assistant_blocks` / `stop_reason` / `usage_delta` off it — without
# it the model's tool_use blocks never land in history and the agent
# loop short-circuits after one dispatch. We MUST emit this for
# parity (prj_03090d30 2026-06-20: lookup_sra_runinfo returned
# wrong_tool, no follow-up turn happened, _stop_reason was None at
# guide.py:1156 → break before continue).
from core.runtime.llm_runtime_direct import _StreamCompleted


# ─── 1. Tool schema translation ─────────────────────────────────────


def translate_tools_to_openai(tools: list[dict]) -> list[dict]:
    """Anthropic-shape tool schemas → OpenAI ChatCompletions shape.

    Anthropic:  {"name": ..., "description": ..., "input_schema": {...}}
    OpenAI:     {"type": "function",
                 "function": {"name": ..., "description": ...,
                              "parameters": {...}}}

    Empty input_schema becomes `{"type": "object"}` (OpenAI rejects
    missing parameters). Empty description becomes "" (OpenAI accepts).
    """
    out: list[dict] = []
    for t in tools or []:
        name = t.get("name")
        if not name:
            continue
        params = t.get("input_schema") or {"type": "object"}
        out.append({
            "type": "function",
            "function": {
                "name":        name,
                "description": t.get("description") or "",
                "parameters":  params,
            },
        })
    return out


# ─── 1.5. qwen3_coder XML rescue parser ─────────────────────────────
# vLLM's `--tool-call-parser qwen3_coder` reliably translates simple
# tool calls into OpenAI `tool_calls`, but has a known leak when the
# model mixes prose with a call (or the chat-template wrapping gets
# half-eaten). In those cases the raw model output —
#   <function=name>
#     <parameter=key>value</parameter>
#   </function>
#   </tool_call>    ← stray closer from the chat template
# — lands in `content` and `tool_calls` is empty. This rescue parser
# recovers those calls so guide.py never sees a "model clearly wanted
# to call X but no tool_use block landed" turn. Reproduced 2026-06-20
# against Qwen3-Coder-30B-A3B-Instruct on the lean catalog (4/8 → 0/8
# parser leaks on P1).

_QWEN_FUNCTION_RE  = re.compile(
    r"<function=([\w.\-]+)>(.*?)</function>", re.DOTALL)
_QWEN_PARAMETER_RE = re.compile(
    r"<parameter=([\w.\-]+)>(.*?)</parameter>", re.DOTALL)
_QWEN_STRAY_TAG_RE = re.compile(r"</?tool_call>")


def _rescue_qwen3_coder_xml(content: str) -> tuple[list[dict], str]:
    """Extract qwen3_coder-format tool calls from raw content. Returns
    (calls, cleaned_content) where calls is [{"name", "args_json"}].

    Parameters parse as STRINGS (qwen3_coder XML carries no types).
    Tools whose JSON schema expects a number/bool still receive the
    string here; the MCP layer or tool body is responsible for any
    coercion. This matches how the model's own training represents
    these values, so empirically it works for our catalog.
    """
    rescued: list[dict] = []
    for fn_match in _QWEN_FUNCTION_RE.finditer(content):
        name = fn_match.group(1)
        body = fn_match.group(2)
        args: dict = {}
        for p in _QWEN_PARAMETER_RE.finditer(body):
            args[p.group(1)] = p.group(2).strip()
        rescued.append({"name": name, "args_json": json.dumps(args)})
    if not rescued:
        return [], content
    cleaned = _QWEN_FUNCTION_RE.sub("", content)
    cleaned = _QWEN_STRAY_TAG_RE.sub("", cleaned).strip()
    return rescued, cleaned


# ─── 2. Message history translation ─────────────────────────────────


# H2 experiment knob — frame tool_result content to nudge small
# models (Qwen3-class) toward acting on results instead of narrating
# them. Read at translation time (per request) so the live server
# picks up env changes after a bounce.
#
# Values:
#   "none"           — default; raw body, OpenAI-canonical shape
#   "v1_suffix"      — append an explicit action-prompt suffix
#   "v2_observation" — prepend "OBSERVATION: " (ReAct-style)
#   "v3_followup"    — emit raw body, then inject a synthetic user
#                      message after the tool-message run
#
# Anthropic backend is unaffected — this lives in the OpenAI adapter.

_V1_SUFFIX = ("\n\n[Reply with your next tool call. Do not summarize "
              "this result.]")
_V3_FOLLOWUP = ("Based on the tool result above, what is your next "
                "tool call?")


def _frame_tool_result_body(body: str, mode: str) -> str:
    if mode == "v1_suffix":
        return body + _V1_SUFFIX
    if mode == "v2_observation":
        return "OBSERVATION: " + body
    if mode == "v4_combo":
        # v1+v2 stacked: observation prefix + action-prompt suffix.
        return "OBSERVATION: " + body + _V1_SUFFIX
    return body


def translate_history_to_openai(messages: list[dict]) -> list[dict]:
    """Anthropic-shape `messages` → OpenAI-shape `messages`.

    Anthropic represents tool exchanges as content blocks inside a
    single message:
      {"role":"assistant","content":[{"type":"text","text":...},
                                      {"type":"tool_use","id":...,"name":...,"input":...}]}
      {"role":"user",    "content":[{"type":"tool_result","tool_use_id":...,"content":...}]}

    OpenAI splits these into separate messages keyed by tool_call_id:
      {"role":"assistant","content":"...","tool_calls":[
          {"id":..., "type":"function", "function":{"name":..., "arguments":"json-str"}}]}
      {"role":"tool","tool_call_id":..., "content":"..."}

    Rules applied here:
      - assistant text + tool_use → one assistant message with both
        `content` (joined text) and `tool_calls` (list)
      - user tool_result block → its OWN message with role:"tool"
        (one per tool_result; OpenAI requires one tool message per
        tool_call_id)
      - bare user/assistant text → role:"user"/"assistant" with
        content as string

    `arguments` MUST be a JSON-encoded string per the OpenAI spec.
    """
    framing_mode = config.settings.openai_tool_result_framing.get().strip().lower()
    out: list[dict] = []
    for msg in messages or []:
        role = msg.get("role")
        content = msg.get("content")
        # String content — pass through.
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            # Unknown shape — best-effort serialize.
            out.append({"role": role, "content": json.dumps(content,
                                                            default=str)})
            continue

        # Block-shaped content. The two role-specific lanes:
        if role == "assistant":
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            for b in content:
                t = b.get("type")
                if t == "text":
                    text_parts.append(b.get("text") or "")
                elif t == "tool_use":
                    tool_calls.append({
                        "id":       b.get("id") or "",
                        "type":     "function",
                        "function": {
                            "name":      b.get("name") or "",
                            "arguments": json.dumps(b.get("input") or {},
                                                    default=str),
                        },
                    })
                # other block types (thinking, etc.) — drop here; the
                # outbound prompt doesn't replay model thoughts.
            assistant_msg: dict = {"role": "assistant",
                                   "content": "\n".join(p for p in text_parts
                                                          if p)}
            if tool_calls:
                assistant_msg["tool_calls"] = tool_calls
            out.append(assistant_msg)
            continue

        if role == "user":
            # tool_result blocks become their own role:"tool" messages.
            # Bare text blocks coalesce into one user message at the
            # END (after any preceding tool messages), matching the
            # Anthropic ordering where tool_result blocks come BEFORE
            # any new user text in a turn.
            text_parts = []
            emitted_tool_msg = False
            for b in content:
                t = b.get("type")
                if t == "tool_result":
                    tc = b.get("content")
                    # Stringify tool_result content — OpenAI tool
                    # message content must be a string.
                    if isinstance(tc, str):
                        body = tc
                    elif isinstance(tc, list):
                        # Anthropic sometimes nests text blocks inside.
                        bits = []
                        for c in tc:
                            if isinstance(c, dict) and c.get("type") == "text":
                                bits.append(c.get("text") or "")
                            else:
                                bits.append(json.dumps(c, default=str))
                        body = "\n".join(b for b in bits if b)
                    else:
                        body = json.dumps(tc, default=str)
                    body = _frame_tool_result_body(body, framing_mode)
                    out.append({"role":         "tool",
                                "tool_call_id": b.get("tool_use_id") or "",
                                "content":      body})
                    emitted_tool_msg = True
                elif t == "text":
                    text_parts.append(b.get("text") or "")
            # v3 — once per user turn that contained tool_results,
            # inject a single synthetic user nudge AFTER the tool run.
            if emitted_tool_msg and framing_mode == "v3_followup":
                out.append({"role": "user", "content": _V3_FOLLOWUP})
            if text_parts:
                out.append({"role":    "user",
                            "content": "\n".join(p for p in text_parts if p)})
            continue

        # Other roles (system) — flatten text blocks into a string.
        text_parts = [b.get("text", "")
                      for b in content if isinstance(b, dict)
                      and b.get("type") == "text"]
        out.append({"role":    role,
                    "content": "\n".join(p for p in text_parts if p)})
    return out


# ─── 3. <think> stream stripper ─────────────────────────────────────


class ThinkStripper:
    """Filter `<think>…</think>` blocks from a streaming text feed.

    Use one instance per assistant turn. Feed it deltas as they arrive
    via `feed(chunk) → (visible_text, thinking_text)`. The split tracks
    state across chunk boundaries:
      - outside a think block: text passes through
      - inside a think block: text accumulates into the thinking
        return value, NOT the visible one
      - tag straddles two chunks: we buffer the partial tag until we
        can decide whether it's `<think>` or just `<`

    Qwen3-8B always emits `<think>…</think>` BEFORE `tool_calls`
    (verified in Phase 0 smoke), so by the time the model issues a
    tool the stripper has already seen `</think>` and gone back to
    the outside state.
    """

    def __init__(self) -> None:
        self._mode: str = "outside"     # "outside" | "inside"
        self._buf:  str = ""            # partial-tag buffer

    def feed(self, chunk: str) -> tuple[str, str]:
        """Push a delta chunk. Returns (visible, thinking) — either may
        be empty. The stripper is stateful; do not reuse across turns."""
        if not chunk:
            return "", ""
        visible:  list[str] = []
        thinking: list[str] = []
        data = self._buf + chunk
        self._buf = ""
        i = 0
        while i < len(data):
            if self._mode == "outside":
                # Look for the next "<think>" tag.
                j = data.find("<think>", i)
                if j == -1:
                    # No tag in remaining text — but the tail might be
                    # a partial tag. Buffer up to len("<think>")-1
                    # trailing chars if they could start one.
                    safe_end, buffered = _split_trailing_tag(data[i:], "<think>")
                    visible.append(safe_end)
                    self._buf = buffered
                    break
                # Emit everything before the tag.
                visible.append(data[i:j])
                i = j + len("<think>")
                self._mode = "inside"
            else:  # inside
                k = data.find("</think>", i)
                if k == -1:
                    # No closing tag yet — accumulate as thinking
                    # except a possible trailing partial tag.
                    safe_end, buffered = _split_trailing_tag(data[i:], "</think>")
                    thinking.append(safe_end)
                    self._buf = buffered
                    break
                thinking.append(data[i:k])
                i = k + len("</think>")
                self._mode = "outside"
        return "".join(visible), "".join(thinking)

    def flush(self) -> tuple[str, str]:
        """At end of stream, flush any buffered partial-tag text. If
        still inside a think block, the partial buffer was thinking;
        otherwise it was visible text we hadn't decided about yet."""
        if not self._buf:
            return "", ""
        out = self._buf
        self._buf = ""
        if self._mode == "inside":
            return "", out
        return out, ""


def _tools_to_responses(tools: list[dict]) -> list[dict]:
    """Anthropic tool schemas → Responses API function tools (flat shape:
    the function fields are top-level, not nested under a `function` key)."""
    out: list[dict] = []
    for t in tools or []:
        if not isinstance(t, dict) or not t.get("name"):
            continue
        out.append({"type": "function", "name": t["name"],
                    "description": t.get("description") or "",
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}}})
    return out


def _history_to_responses_input(messages: list[dict]) -> list[dict]:
    """Anthropic-shape history → Responses API `input` items: user/assistant text
    → message items (input_text / output_text); assistant tool_use → function_call;
    user tool_result → function_call_output (a top-level item, no role)."""
    import json as _json
    items: list[dict] = []
    for m in messages or []:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            ctype = "output_text" if role == "assistant" else "input_text"
            if content:
                items.append({"role": role, "content": [{"type": ctype, "text": content}]})
            continue
        if not isinstance(content, list):
            continue
        text_parts: list[str] = []

        def _flush():
            if text_parts:
                ctype = "output_text" if role == "assistant" else "input_text"
                items.append({"role": role, "content": [{"type": ctype, "text": "".join(text_parts)}]})
                text_parts.clear()

        for b in content:
            if not isinstance(b, dict):
                continue
            bt = b.get("type")
            if bt == "text":
                text_parts.append(b.get("text") or "")
            elif bt == "tool_use":
                _flush()
                items.append({"type": "function_call", "call_id": b.get("id") or "",
                              "name": b.get("name") or "",
                              "arguments": _json.dumps(b.get("input") or {})})
            elif bt == "tool_result":
                _flush()
                out = b.get("content")
                if isinstance(out, list):
                    out = "".join(x.get("text", "") if isinstance(x, dict) else str(x) for x in out)
                items.append({"type": "function_call_output", "call_id": b.get("tool_use_id") or "",
                              "output": out if isinstance(out, str) else _json.dumps(out)})
        _flush()
    return items


def _normalize_stop_reason(finish_reason: str | None) -> str:
    """Map OpenAI's finish_reason vocabulary onto the Anthropic-flavored
    stop_reason vocabulary guide.py's outer loop expects.

    OpenAI:    "stop" | "length" | "tool_calls" | "content_filter" | None
    Anthropic: "end_turn" | "max_tokens" | "tool_use" | "stop_sequence"

    Critical: guide.py:1156 only loops back to the model when
    stop_reason == "tool_use" — so a turn that dispatched tools but
    reported "tool_calls" would silently end before the model could
    react to the tool_result. Observed in prj_03090d30 2026-06-20:
    Qwen3 called lookup_sra_runinfo on a GEO accession, the tool
    returned 'wrong_tool', and the loop broke without giving the
    model a chance to retry.
    """
    m = {
        "stop":           "end_turn",
        "length":         "max_tokens",
        "tool_calls":     "tool_use",
        "content_filter": "end_turn",
    }
    return m.get((finish_reason or "").lower(), finish_reason or "end_turn")


def _split_trailing_tag(text: str, tag: str) -> tuple[str, str]:
    """Split `text` into (committed, maybe-partial-tag-tail).

    If the tail of `text` could be the start of `tag`, hold those
    chars in the buffer until we see more. e.g. for tag '<think>':
      text = "...<thi"  → ("...", "<thi")
      text = "...<x"    → ("...<x", "")  (definitely not '<think>')
      text = "...foo<"  → ("...foo", "<")
    """
    if not text:
        return "", ""
    # The longest suffix of `text` that is a proper prefix of `tag`.
    max_partial = len(tag) - 1
    start = max(0, len(text) - max_partial)
    for s in range(start, len(text)):
        candidate = text[s:]
        if tag.startswith(candidate):
            return text[:s], candidate
    return text, ""


# ─── 4. The runtime ─────────────────────────────────────────────────



async def _emit_completion_and_dispatch(
        req, visible_text_buf, tool_calls_state, finish_reason, usage,
        tool_executor, halt_on_tools):
    """Shared tail for both OpenAI paths (ChatCompletions + Codex Responses):
    build the assistant blocks, emit _StreamCompleted, then dispatch tools +
    emit ToolResult/TurnHalt/TurnDone. Populated locals are provider-specific."""
    # 3. Build the assistant's blocks in Anthropic shape — text +
    # tool_use blocks — so guide.py can append_message("assistant",
    # blocks) and the NEXT outer iteration's history includes the
    # tool_use to which the tool_result corresponds. Without this
    # the loop sees a dangling tool_result and the model can never
    # react to its own tool call.
    assistant_blocks: list[dict] = []
    full_text = "".join(visible_text_buf).strip()
    if full_text:
        assistant_blocks.append({"type": "text", "text": full_text})
    tool_calls_in_order: list[str] = []
    for idx in sorted(tool_calls_state.keys()):
        tc = tool_calls_state[idx]
        tool_name = tc["name"]
        tool_use_id = tc["id"] or f"toolu_local_{idx}"
        args_str = tc["args"] or "{}"
        try:
            tool_input = json.loads(args_str)
        except json.JSONDecodeError:
            # Keep the block in history with empty input so guide.py
            # can still pair the eventual error tool_result with it.
            tool_input = {}
        assistant_blocks.append({"type":  "tool_use",
                                 "id":    tool_use_id,
                                 "name":  tool_name,
                                 "input": tool_input})
        tool_calls_in_order.append(tool_name)
    normalized_stop = _normalize_stop_reason(finish_reason)

    # Emit the private sentinel guide.py expects (parity with
    # DirectAPIRuntime). `final_msg=None` because we don't have a
    # provider-side terminal Message object — guide.py only uses
    # this for cache-key debugging on the Anthropic path.
    yield _StreamCompleted(
        final_msg=None,
        usage_delta=usage,
        assistant_blocks=assistant_blocks,
        stop_reason=normalized_stop,
        tool_calls_this_turn=tool_calls_in_order,
    )

    # 4. No tool calls → natural end of turn.
    if not tool_calls_state:
        yield TurnDone(stop_reason=normalized_stop, usage=usage)
        return

    # 4. Tool dispatch loop. Mirrors DirectAPIRuntime envelope
    #    handling so guide.py doesn't care which runtime served the
    #    turn.
    for idx in sorted(tool_calls_state.keys()):
        tc = tool_calls_state[idx]
        tool_name   = tc["name"]
        tool_use_id = tc["id"] or f"toolu_local_{idx}"
        args_str    = tc["args"] or "{}"
        try:
            tool_input = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError as e:
            # Model emitted malformed JSON. Surface as an error
            # tool_result so the orchestrator records the round-
            # trip and the next turn can let the model retry.
            yield ToolUseStart(tool_use_id=tool_use_id,
                               tool_name=tool_name, input={})
            yield ToolResult(tool_use_id=tool_use_id,
                             tool_name=tool_name,
                             result={"is_error": True,
                                     "content":
                                     f"invalid JSON in tool arguments: {e}"})
            continue

        yield ToolUseStart(tool_use_id=tool_use_id,
                           tool_name=tool_name, input=tool_input)

        if tool_name in halt_on_tools:
            yield TurnHalt(reason="pending_tool", detail={
                "tool_use_id": tool_use_id,
                "tool_name":   tool_name,
                "input":       tool_input,
            })
            return

        exec_ctx = {**req.ctx, "tool_use_id": tool_use_id}
        try:
            result_obj = await tool_executor(tool_name, tool_input, exec_ctx)
        except Exception as e:                              # noqa: BLE001
            yield ToolResult(tool_use_id=tool_use_id,
                             tool_name=tool_name,
                             result={"is_error": True,
                                     "content": f"tool exec failed: {e}"})
            continue

        halt_after: str | None = None
        if isinstance(result_obj, dict):
            if result_obj.get("deferred"):
                yield TurnHalt(reason="deferred", detail={
                    "tool_use_id": tool_use_id,
                    "tool_name":   tool_name,
                    "deferred_id": result_obj.get("deferred_id"),
                    "timeout_s":   result_obj.get("timeout_s"),
                })
                return
            if "_runtime_halt_before" in result_obj:
                reason = result_obj.pop("_runtime_halt_before")
                yield TurnHalt(reason=reason, detail={
                    **result_obj,
                    "tool_use_id": tool_use_id,
                    "tool_name":   tool_name,
                })
                return
            halt_after = result_obj.pop("_runtime_halt_after", None)

        yield ToolResult(tool_use_id=tool_use_id,
                         tool_name=tool_name, result=result_obj)

        if halt_after:
            yield TurnHalt(reason=halt_after, detail={
                **(result_obj if isinstance(result_obj, dict) else {}),
                "tool_use_id": tool_use_id,
                "tool_name":   tool_name,
            })
            return

    yield TurnDone(stop_reason=_normalize_stop_reason(finish_reason),
                   usage=usage)


class OpenAICompatibleRuntime:
    """LLMRuntime backed by any OpenAI-compatible Chat Completions
    endpoint. Today's target: a self-hosted vLLM serving Qwen3-8B on
    `http://localhost:8001/v1` (the Phase 0 setup).

    Construction reads three knobs from env (overridable via __init__):
      ABA_OPENAI_BASE_URL — default "http://localhost:8001/v1"
      ABA_OPENAI_API_KEY  — default "none" (vLLM doesn't enforce auth)
      ABA_OPENAI_MODEL    — only used as a fallback if req.model is
                             empty; the spec's model normally wins.

    What it owns:
      • shape translation outbound (tools → OpenAI; history → OpenAI)
      • streaming consumption (chunked SSE → public events)
      • `<think>…</think>` stripping (via ThinkStripper)
      • tool dispatch loop (mirrors DirectAPIRuntime so envelope
        semantics — deferred / _runtime_halt_before/after — are
        identical for guide.py)

    What it intentionally does NOT do (yet):
      • prompt-cache reasoning (vLLM APC is automatic + prefix-only;
        making the system prefix APC-friendly is a guide.py-side
        change deferred to Phase 3)
      • progress_q drain pattern (run_python doesn't currently push
        ticks to OpenAI-backend tools; will lift from DirectAPI when
        we wire run_python in)
      • retries on transient errors (the local endpoint is on the
        same host; the SSH tunnel is the failure mode and there's
        nothing to retry on its other side)
    """

    def __init__(self, *,
                 base_url: str | None = None,
                 api_key:  str | None = None,
                 enable_thinking: bool | None = None) -> None:
        self.base_url = (base_url
                         or config.settings.openai_base_url.get()
                         or "http://localhost:8001/v1")
        # Real OpenAI (api.openai.com / chatgpt.com backend) rejects vLLM-only
        # request extensions (chat_template_kwargs) — detect it (any openai.com /
        # chatgpt.com host) so we send a clean request there.
        self._real_openai = ("api.openai.com" in self.base_url
                             or "chatgpt.com" in self.base_url)
        # Codex/ChatGPT subscription: chatgpt.com/backend-api/codex speaks the
        # RESPONSES API (not ChatCompletions), needs originator + OpenAI-Beta
        # headers, and only accepts the Codex model slugs (gpt-5.x).
        self._responses_mode = "/backend-api/codex" in self.base_url
        # Subscription uses the OAuth Bearer as the key + a ChatGPT-Account-Id
        # header; a plain API key otherwise. Explicit api_key arg wins (tests).
        self._account_id = config.settings.openai_account_id.get() or None
        self.api_key  = (api_key
                         or os.environ.get("OPENAI_OAUTH_TOKEN")
                         or config.settings.openai_api_key.get()
                         or os.environ.get("OPENAI_API_KEY")
                         or "none")
        # Qwen3-class models emit <think>…</think> reasoning by default,
        # spending ~5× more completion tokens than the actual answer
        # AND introducing noticeable latency. Tool-driven agent loops
        # don't benefit from CoT (the model's reasoning shows up as
        # "let me think step by step" prose that we then have to strip),
        # so default OFF. Opt back in via ABA_OPENAI_ENABLE_THINKING=1
        # or by constructing with enable_thinking=True.
        if enable_thinking is None:
            env = (config.settings.openai_enable_thinking.get() or "").lower()
            enable_thinking = env in ("1", "true", "yes", "on")
        self.enable_thinking = enable_thinking
        self._client: Any | None = None      # lazy

    def _get_client(self) -> Any:
        if self._client is None:
            import openai
            headers: dict = {}
            if self._account_id:
                headers["ChatGPT-Account-Id"] = self._account_id
            if self._responses_mode:
                # What the Codex CLI sends so the ChatGPT backend accepts the call.
                headers["originator"] = "codex_cli"
                headers["OpenAI-Beta"] = "responses=experimental"
            self._client = openai.AsyncOpenAI(base_url=self.base_url,
                                              api_key=self.api_key,
                                              default_headers=headers or None)
        return self._client

    async def _run_turn_responses(
        self,
        req: RuntimeRequest,
        tool_executor: ToolExecutor,
        halt_on_tools: frozenset[str] = frozenset(),
    ) -> AsyncIterator[RuntimeEvent]:
        """Codex/ChatGPT subscription path — the OpenAI RESPONSES API against
        chatgpt.com/backend-api/codex. Translates the Anthropic-shape history/tools
        into Responses `input`/`instructions`/`tools`, streams, and reuses the
        shared completion+dispatch tail so guide.py sees identical events."""
        instructions = req.system.stable or ""
        if req.system.dynamic:
            instructions = (instructions + "\n\n" + req.system.dynamic
                            if instructions else req.system.dynamic)

        input_items = _history_to_responses_input(req.history)
        tools = _tools_to_responses(req.tools)

        kwargs: dict = {
            "model":       req.model,
            "instructions": instructions,
            "input":       input_items,
            "stream":      True,
            "store":       False,
            "reasoning":   {"summary": "auto"},
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
            kwargs["parallel_tool_calls"] = True

        client = self._get_client()
        visible_text_buf: list[str] = []
        tool_calls_state: dict[int, dict] = {}    # output_index → {id, name, args}
        usage: dict = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}
        try:
            stream = await client.responses.create(**kwargs)
        except Exception as e:                                  # noqa: BLE001
            msg = str(e)
            # The most common cause is a model/credential mismatch: an API-key model
            # (gpt-4o/4.1) picked while on a ChatGPT subscription (or vice-versa).
            if "not supported when using Codex" in msg or "is not supported" in msg:
                msg = (f"The model '{req.model}' isn't available on your ChatGPT/Codex "
                       f"subscription. In Settings → Agent, pick a Codex model "
                       f"(e.g. gpt-5.4-mini / gpt-5.5), or connect an OpenAI API key to "
                       f"use gpt-4o / gpt-4.1.")
            yield TurnHalt(reason="error", detail={"message": msg, "type": type(e).__name__})
            return
        try:
            async for ev in stream:
                if req.cancel is not None and getattr(req.cancel, "cancelled", False):
                    yield TurnHalt(reason="cancelled", detail={})
                    return
                et = getattr(ev, "type", "")
                if et == "response.output_text.delta":
                    d = getattr(ev, "delta", "") or ""
                    if d:
                        visible_text_buf.append(d)
                        yield TextDelta(text=d)
                elif et == "response.output_item.added":
                    item = getattr(ev, "item", None)
                    if item is not None and getattr(item, "type", "") == "function_call":
                        tool_calls_state[getattr(ev, "output_index", len(tool_calls_state))] = {
                            "id": getattr(item, "call_id", "") or getattr(item, "id", ""),
                            "name": getattr(item, "name", ""), "args": ""}
                elif et == "response.function_call_arguments.delta":
                    st = tool_calls_state.get(getattr(ev, "output_index", None))
                    if st is not None:
                        st["args"] += getattr(ev, "delta", "") or ""
                elif et in ("response.completed", "response.incomplete"):
                    u = getattr(getattr(ev, "response", None), "usage", None)
                    if u is not None:
                        usage["input"] = getattr(u, "input_tokens", 0) or 0
                        usage["output"] = getattr(u, "output_tokens", 0) or 0
                        det = getattr(u, "input_tokens_details", None)
                        usage["cache_read"] = getattr(det, "cached_tokens", 0) or 0 if det else 0
                elif et in ("response.failed", "error"):
                    resp = getattr(ev, "response", ev)
                    yield TurnHalt(reason="error", detail={"message": str(resp)[:300], "type": "responses_error"})
                    return
        except Exception as e:                                  # noqa: BLE001
            yield TurnHalt(reason="error", detail={"message": f"stream error: {e}",
                                                   "type": type(e).__name__})
            return

        finish_reason = "tool_calls" if tool_calls_state else "stop"
        async for _ev in _emit_completion_and_dispatch(
                req, visible_text_buf, tool_calls_state, finish_reason, usage,
                tool_executor, halt_on_tools):
            yield _ev

    async def run_turn(
        self,
        req: RuntimeRequest,
        tool_executor: ToolExecutor,
        halt_on_tools: frozenset[str] = frozenset(),
    ) -> AsyncIterator[RuntimeEvent]:
        """One model phase against the OpenAI endpoint.

        Yields (in order):
          - TextDelta for visible text (post `<think>` strip)
          - ToolUseStart once per tool call (after stream finishes
            and we have valid JSON args)
          - ToolResult for each dispatched tool (or TurnHalt envelope)
          - TurnDone at natural end

        Tool dispatch is post-stream: OpenAI returns the FULL tool_call
        list in one assistant message (finish_reason='tool_calls'), so
        we collect all calls during streaming, then loop dispatch.
        """
        # Codex/ChatGPT subscription speaks the Responses API — a different
        # request/stream shape — so route to the dedicated path.
        if self._responses_mode:
            async for _ev in self._run_turn_responses(req, tool_executor, halt_on_tools):
                yield _ev
            return
        # 1. Build the outgoing payload.
        system_text = req.system.stable or ""
        if req.system.dynamic:
            system_text = (system_text + "\n\n" + req.system.dynamic
                           if system_text else req.system.dynamic)
        messages: list[dict] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.extend(translate_history_to_openai(req.history))
        openai_tools = translate_tools_to_openai(req.tools)

        model = req.model or config.settings.openai_model.get() or "qwen3-8b"
        kwargs: dict = {
            "model":      model,
            "messages":   messages,
            "max_tokens": req.max_tokens,
            "stream":     True,
            "stream_options": {"include_usage": True},
        }
        if openai_tools:
            kwargs["tools"]       = openai_tools
            kwargs["tool_choice"] = "auto"
        # vLLM extension: Qwen3 thinking-mode toggle. When False (our default) the
        # model skips the `<think>…</think>` CoT entirely — cheaper/faster, visible
        # answer + tool calls unaffected. ONLY valid on a vLLM/local endpoint —
        # api.openai.com rejects unknown body fields (400), so skip it there. The
        # ThinkStripper still runs (defense in depth) and no-ops when no tags appear.
        if not self._real_openai:
            kwargs["extra_body"] = {
                "chat_template_kwargs": {"enable_thinking": self.enable_thinking},
            }

        # 2. Open the stream + consume.
        client    = self._get_client()
        stripper  = ThinkStripper()
        # tool_call accumulator: index → {"id": str, "name": str, "args": str}
        tool_calls_state: dict[int, dict] = {}
        # Accumulate visible (post-think-strip) text so we can build
        # the assistant text block for _StreamCompleted.
        visible_text_buf: list[str] = []
        finish_reason: str | None = None
        usage: dict = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

        import time as _time
        _t_create_begin = _time.perf_counter()
        try:
            stream = await client.chat.completions.create(**kwargs)
        except Exception as e:                                  # noqa: BLE001
            yield TurnHalt(reason="error", detail={
                "message": str(e), "type": type(e).__name__})
            return
        _t_create_done = _time.perf_counter()

        _t_first_chunk: float | None = None
        _n_chunks = 0
        try:
            async for chunk in stream:
                if _t_first_chunk is None:
                    _t_first_chunk = _time.perf_counter()
                _n_chunks += 1
                # Cancellation: cooperative at the chunk boundary.
                if req.cancel is not None \
                        and getattr(req.cancel, "cancelled", False):
                    yield TurnHalt(reason="cancelled", detail={})
                    return

                # Usage chunks (vLLM emits a final chunk with usage when
                # stream_options.include_usage=True) may have empty
                # `choices`. Capture usage and continue.
                if getattr(chunk, "usage", None):
                    u = chunk.usage
                    usage["input"]  = getattr(u, "prompt_tokens", 0) or 0
                    usage["output"] = getattr(u, "completion_tokens", 0) or 0

                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta  = choice.delta

                if getattr(delta, "content", None):
                    visible, _ = stripper.feed(delta.content)
                    if visible:
                        visible_text_buf.append(visible)
                        yield TextDelta(text=visible)

                if getattr(delta, "tool_calls", None):
                    for tc in delta.tool_calls:
                        idx = tc.index
                        state = tool_calls_state.setdefault(
                            idx, {"id": "", "name": "", "args": ""})
                        if getattr(tc, "id", None):
                            state["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                state["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                state["args"] += fn.arguments

                if choice.finish_reason:
                    finish_reason = choice.finish_reason
        except Exception as e:                                  # noqa: BLE001
            yield TurnHalt(reason="error", detail={
                "message": f"stream error: {e}",
                "type":    type(e).__name__})
            return

        # Per-call timing breakdown — emitted as one log line, gated
        # by ABA_DEBUG_TIMING. The log can be grepped for
        # `[openai-timing]` to reconstruct what happened on each turn.
        if config.settings.debug_timing.get():
            _t_stream_done = _time.perf_counter()
            _create_ms = (_t_create_done - _t_create_begin) * 1000
            _ttft_ms = ((_t_first_chunk or _t_stream_done) - _t_create_done) * 1000
            _gen_ms  = (_t_stream_done - (_t_first_chunk or _t_create_done)) * 1000
            print(f"[openai-timing] create={_create_ms:.0f}ms "
                  f"TTFT={_ttft_ms:.0f}ms "
                  f"gen={_gen_ms:.0f}ms "
                  f"chunks={_n_chunks} "
                  f"in={usage.get('input',0)}t "
                  f"out={usage.get('output',0)}t",
                  flush=True)

        # Final stripper flush.
        v, _ = stripper.flush()
        if v:
            visible_text_buf.append(v)
            yield TextDelta(text=v)

        # 2.5. qwen3_coder XML rescue. If vLLM's parser failed to
        # translate (tool_calls is empty but the content has
        # `<function=…>` markers), recover the calls client-side and
        # populate tool_calls_state as if the parser had succeeded.
        # We can't unsend the TextDelta events for the XML we already
        # streamed, but stripping it from `visible_text_buf` keeps it
        # out of the assistant_blocks we hand back to guide.py — so
        # history doesn't echo the leak on the next turn.
        if not tool_calls_state:
            staged_text = "".join(visible_text_buf)
            if "<function=" in staged_text:
                rescued, cleaned = _rescue_qwen3_coder_xml(staged_text)
                if rescued:
                    for idx, r in enumerate(rescued):
                        tool_calls_state[idx] = {
                            "id":   f"toolu_rescue_{idx}",
                            "name": r["name"],
                            "args": r["args_json"],
                        }
                    visible_text_buf.clear()
                    if cleaned:
                        visible_text_buf.append(cleaned)
                    if finish_reason in (None, "stop"):
                        finish_reason = "tool_calls"

        async for _ev in _emit_completion_and_dispatch(
                req, visible_text_buf, tool_calls_state, finish_reason, usage,
                tool_executor, halt_on_tools):
            yield _ev
        return


def _conforms_to_protocol() -> bool:
    """Cheap runtime protocol check — used by the unit test. Structural
    typing in Protocol means an isinstance() check needs
    @runtime_checkable on the Protocol; we don't add that to the
    domain-neutral protocol module, so we inline a method-presence
    check here. Keeps the protocol definition clean (same pattern as
    DirectAPIRuntime._conforms_to_protocol)."""
    required = {"run_turn"}
    return all(hasattr(OpenAICompatibleRuntime, m) for m in required)
