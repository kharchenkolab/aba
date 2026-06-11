"""Agent abstraction — Pass F (arch3_plan.md).

AgentSpec is the configurable declaration of an agent: model, prompt
path, role hint passed to the manifest assembler, allowed tools,
streaming/halt flags, iteration cap. Today's Guide and the per-advisor
sub-agents both use this; the bio/advisors/<name>.yaml file is the
spec.

For Pass F the Guide loop body still lives in guide.py (a full state-
machine extraction lands when product needs the resume path). What's
new: the spec object exists, advisor configurations move to YAML, and
loading is a `load_agent_spec(name)` call.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import yaml


@dataclass(frozen=True)
class AgentSpec:
    name:             str                          # "guide", "skeptic", ...
    role:             str                          # "primary" | "advisor" | "background"
    model:            str
    system_prompt:    str                          # rendered text (from prompt MD file)
    manifest_role:    str                          # passed to build_manifest(role=…)
    tool_allowlist:   tuple[str, ...] = ()         # empty = no tools; ('*',) = all
    streaming:        bool = True
    halts_allowed:    bool = True
    max_iterations:   int = 20
    timeout_s:        int = 120
    fake_text:        Optional[str] = None          # canned reply for FAKE_SESSION
    # W1-A.2 / R-2.2: which LLMRuntime serves this agent's turns.
    #   "direct" (default) — DirectAPIRuntime; raw anthropic.messages.stream
    #   "sdk"               — AgentSDKRuntime; claude_agent_sdk wrapper
    #   "fake"              — FakeRuntime; scripted JSONL replay (eval/tests)
    # Existing specs default to "direct" so this field is backwards-
    # compatible with the per-spec YAMLs. The env var ABA_FAKE_SESSION
    # still globally forces "fake" — same kill-switch as before.
    runtime:          str = "direct"


def _resolve_prompt(prompt_field: str, anchor_dir: Path) -> str:
    """A spec's `system_prompt` may be inline text or a path relative to the
    spec file (e.g. ../prompts/skeptic.md). Resolve and read."""
    p = (anchor_dir / prompt_field).resolve()
    if p.is_file():
        return p.read_text().strip()
    # Treat as inline if it doesn't look like a path.
    return prompt_field


def load_agent_spec(spec_path: str | Path) -> AgentSpec:
    """Load an AgentSpec from a YAML file. Path is absolute, or relative
    to the caller's content directory (callers know their layout).

    Primary-agent model can be overridden at runtime via env vars:
      ABA_PRIMARY_MODEL — applies ONLY to role: primary (the chat-handling
                          agent). Use this to swap the live chat model.
      ABA_MODEL         — same effect; kept as a back-compat alias.
    Advisor models stay on their YAML-declared values (typically Haiku) —
    overriding ALL agents on a primary swap would 5×-cost every side task.
    """
    import os as _os
    p = Path(spec_path)
    raw = yaml.safe_load(p.read_text()) or {}
    tools = raw.get("tool_allowlist", ())
    role = raw.get("role", "advisor")
    model = raw.get("model", "claude-haiku-4-5-20251001")
    if role == "primary":
        yaml_model = model
        override = (_os.environ.get("ABA_PRIMARY_MODEL")
                    or _os.environ.get("ABA_MODEL"))
        if override:
            model = override
        src = "env override" if override else "yaml"
        print(f"[agent-spec] {raw.get('name','?')} (primary): model={model} ({src}, yaml={yaml_model})",
              flush=True)
    runtime = (raw.get("runtime") or "direct").strip()
    if runtime not in ("direct", "sdk", "fake"):
        raise ValueError(
            f"AgentSpec {raw.get('name','?')!r}: runtime={runtime!r} must "
            "be one of: direct, sdk, fake"
        )
    return AgentSpec(
        name=raw["name"],
        role=role,
        model=model,
        system_prompt=_resolve_prompt(raw.get("system_prompt", ""), p.parent),
        manifest_role=raw.get("manifest_role", raw.get("name", "advisor")),
        tool_allowlist=tuple(tools) if isinstance(tools, (list, tuple)) else (tools,),
        streaming=bool(raw.get("streaming", False)),
        halts_allowed=bool(raw.get("halts_allowed", False)),
        max_iterations=int(raw.get("max_iterations", 8)),
        timeout_s=int(raw.get("timeout_s", 60)),
        fake_text=raw.get("fake_text"),
        runtime=runtime,
    )


def make_runtime(spec: AgentSpec):
    """Pick the LLMRuntime implementation for this agent.

    Selection precedence:
      1. env var ABA_FAKE_SESSION (any truthy value) → FakeRuntime,
         regardless of spec. Same global override as the legacy
         core.llm.make_open_stream() path.
      2. env var ABA_RUNTIME_OVERRIDE (one of direct/sdk/fake) → that
         runtime, regardless of spec. For per-process A/B testing.
      3. spec.runtime field (default "direct").

    Returns an instance implementing the LLMRuntime protocol. Lazy
    imports keep agent.py free of llm_runtime_* module weight on the
    paths that don't actually run turns.
    """
    import os
    if os.environ.get("ABA_FAKE_SESSION"):
        chosen = "fake"
    else:
        chosen = (os.environ.get("ABA_RUNTIME_OVERRIDE")
                  or spec.runtime or "direct").strip().lower()
    if chosen == "direct":
        from core.runtime.llm_runtime_direct import DirectAPIRuntime
        return DirectAPIRuntime()
    if chosen == "sdk":
        from core.runtime.llm_runtime_sdk import AgentSDKRuntime
        return AgentSDKRuntime()
    if chosen == "fake":
        from core.runtime.llm_runtime_fake import FakeRuntime
        return FakeRuntime()
    raise ValueError(f"unknown runtime: {chosen!r}")


# Spec registry — populated by content at startup via register_agent_spec.
_SPECS: dict[str, AgentSpec] = {}


def register_agent_spec(spec: AgentSpec) -> None:
    _SPECS[spec.name] = spec


def get_agent_spec(name: str) -> Optional[AgentSpec]:
    return _SPECS.get(name)


def list_agent_specs() -> list[str]:
    return sorted(_SPECS)


def filter_tools_by_allowlist(tools: list[dict], allowlist: tuple[str, ...]) -> list[dict]:
    """Respect AgentSpec.tool_allowlist:
      ()          → no tools (advisor with no tool access)
      ("*",)      → all tools pass through
      ("a","b")   → only tools whose name is in the set

    The Guide's spec uses ('*',); the existing one-shot advisors use ()
    today (they don't call tools). A future advisor that needs e.g.
    only `query_db` would set tool_allowlist: ['query_db']."""
    if not allowlist:
        return []
    if "*" in allowlist:
        return list(tools)
    keep = set(allowlist)
    return [t for t in tools if t.get("name") in keep]


def _advisor_via_runtime(spec: "AgentSpec", user_prompt: str,
                          max_tokens: int, chosen: str) -> tuple[str, int, int]:
    """Drive an advisor one-shot through `make_runtime(spec)` for the
    SDK (and any future non-direct) path. Returns (text, usage_in,
    usage_out).

    Why sync→async bridge: advisor callers are all sync today. The
    SDK runtime is an async generator. We spin a fresh asyncio loop
    per call. This is fine because:
      - Advisors are not streaming — one turn, one consumer, no
        cancellation requirement.
      - `run_advisor_one_shot` is only called from sync paths
        (FastAPI sync handlers, runner.py side effects) — never from
        inside a running event loop.
    """
    import asyncio
    from core.runtime.llm_runtime import (
        RuntimeRequest, SystemSpec, TextDelta, TurnDone,
    )

    rt = make_runtime(spec)
    req = RuntimeRequest(
        history=[{"role": "user", "content": user_prompt}],
        tools=[],
        system=SystemSpec(stable=spec.system_prompt, dynamic=""),
        model=spec.model,
        max_tokens=max_tokens,
        ctx={},
    )

    async def _no_tools(name, args, ctx):
        return {"error": f"advisor {spec.name!r} called unexpected tool "
                          f"{name!r} — advisors run with no tool surface"}

    async def _drive() -> tuple[str, int, int]:
        chunks: list[str] = []
        in_tok = 0
        out_tok = 0
        async for ev in rt.run_turn(req, _no_tools):
            if isinstance(ev, TextDelta):
                chunks.append(ev.text)
            elif isinstance(ev, TurnDone):
                u = ev.usage or {}
                in_tok = int(u.get("input") or 0)
                out_tok = int(u.get("output") or 0)
        return "".join(chunks).strip(), in_tok, out_tok

    return asyncio.run(_drive())


def run_advisor_one_shot(
    spec: AgentSpec,
    *,
    user_prompt: str,
    max_tokens: int = 400,
    parent_run_id: Optional[str] = None,
    focus_entity_id: Optional[str] = None,
    thread_id: Optional[str] = None,
) -> str:
    """Single-shot advisor turn — non-streaming, no tools. Mirrors the
    one-shot pattern in today's advisors.py:_ask but driven by an
    AgentSpec instead of hardcoded constants.

    B4: every invocation gets a persisted Turn row linked to its parent
    Guide turn via parent_run_id, so /api/turns lists advisor runs
    alongside Guide turns and the UI can navigate parent ↔ child. The
    state machine is degenerate (GENERATING → DONE/FAILED) because the
    Anthropic call is synchronous + non-tool-using, but the persistence
    + observability is the win.

    Cancellation of an in-flight advisor isn't supported yet — the
    underlying messages.create() is a blocking call. When advisors move
    to streaming + tools (Tier C), real cancellation lands.

    The full Agent(spec).run() with streaming + tools + state-machine
    coordination is the next milestone (deferred); this one-shot covers
    every existing advisor today.
    """
    from core.config import API_KEY, FAKE_SESSION
    from core.runtime.turn import Turn, TurnState, gen_run_id
    from core.runtime.checkpoint import checkpoint

    # B4: allocate a Turn row at GENERATING. session_id rides the parent
    # for parent-grouped /api/turns views; if there's no parent (e.g. a
    # standalone advisor probe), use the advisor's own run_id as session.
    run_id = gen_run_id()
    turn = Turn(
        run_id=run_id,
        session_id=parent_run_id or run_id,
        turn_index=0,
        agent_spec_name=spec.name,
        state=TurnState.GENERATING,
        focus_entity_id=focus_entity_id,
        thread_id=thread_id,
        parent_run_id=parent_run_id,
    )
    try:
        checkpoint(turn)
    except Exception:  # noqa: BLE001
        # Persistence is best-effort — never block the actual advisor call
        # on a Turn write failure (e.g. DB still warming on startup).
        pass

    try:
        if FAKE_SESSION and spec.fake_text:
            text = spec.fake_text
        else:
            # R-3.6: route through make_runtime when the spec — or an
            # ABA_RUNTIME_OVERRIDE env knob — asks for a non-direct
            # runtime. For the historical "direct" case we keep the
            # tight messages.create() call, which is cheaper (no async
            # bridge, no MCP overhead) for a non-streaming no-tool turn.
            import os as _os
            override = (_os.environ.get("ABA_RUNTIME_OVERRIDE") or "").strip().lower()
            chosen = override or (spec.runtime or "direct").strip().lower()
            if chosen == "sdk":
                text, used_in, used_out = _advisor_via_runtime(
                    spec, user_prompt, max_tokens, chosen)
                turn.usage_in  = used_in
                turn.usage_out = used_out
            else:
                import anthropic
                from core.llm import _credential_mode, _oauth_bearer, _wants_cc_marker, _CC_MARKER_BLOCK
                # Honor the configured credential mode (was hardcoded apikey →
                # silently failed on oauth_cc with a zero-balance .env key,
                # 2026-06-03). Mirrors core.llm._llm_client + the sync helper
                # in content/bio/lifecycle/promote.py.
                if _credential_mode() in ("oauth", "oauth_cc") and _oauth_bearer():
                    client = anthropic.Anthropic(auth_token=_oauth_bearer())
                else:
                    client = anthropic.Anthropic(api_key=API_KEY)
                if _wants_cc_marker():
                    system_payload = [_CC_MARKER_BLOCK,
                                      {"type": "text", "text": spec.system_prompt}]
                else:
                    system_payload = spec.system_prompt
                msg = client.messages.create(
                    model=spec.model,
                    max_tokens=max_tokens,
                    system=system_payload,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = "".join(
                    b.text for b in msg.content if getattr(b, "type", None) == "text"
                ).strip()
                # Record usage so /api/turns/{id} surfaces token spend.
                if getattr(msg, "usage", None):
                    turn.usage_in  = getattr(msg.usage, "input_tokens", 0) or 0
                    turn.usage_out = getattr(msg.usage, "output_tokens", 0) or 0
        turn.transition(TurnState.DONE)
    except Exception as e:  # noqa: BLE001
        turn.error = {"type": type(e).__name__, "message": str(e)}
        turn.transition(TurnState.FAILED)
        try:
            checkpoint(turn)
        except Exception:  # noqa: BLE001
            pass
        raise
    try:
        checkpoint(turn)
    except Exception:  # noqa: BLE001
        pass
    return text
