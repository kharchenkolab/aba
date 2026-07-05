"""
Adaptive context — §3.6 of aba_arch2.md. Passive layer.

Phase 11 establishes the instrumentation that any later improvements
(DSPy / RL / skill library) need to sit on top of:

  - Every Guide turn writes a row to `context_assemblies` capturing what
    was preloaded into context and how much extra work the agent had to
    do mid-session.
  - When a session crosses a complexity threshold, an end-of-session
    reflection prompt is appended to the Guide and its response is stored
    as a `context_suggestion`.
  - A simple Settings page lists pending suggestions; promoting a
    suggestion appends it to `backend/knowhow/context_policy/<type>.md`,
    which the context service concatenates into the system prompt next
    time that entity type is focused.

Phase 11 stops at "the data is being collected and reflections are
captured." Active probe queries and the DSPy optimization layer follow
once there's session data to test them on.
"""
from __future__ import annotations
import uuid
from pathlib import Path

from core.config import MODEL, FAKE_SESSION


# Turn-count threshold at which the reflection prompt fires.
REFLECTION_TOOL_CALL_THRESHOLD = 4


# Each WS connection / chat session is tagged. For now we just create one
# session per stream_response call; later we can persist this in localStorage.
def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:10]}"


_REFLECTION_PROMPT = (
    "This session involved several tool calls and lookups. Briefly reflect:\n"
    "1. What single piece of information, if pre-loaded into your context "
    "for this entity type, would have most reduced the need for follow-up "
    "queries?\n"
    "2. Phrase a one-line rule (under 30 words) that I could add to the "
    "context policy for this entity type. Start with: \"When the user focuses "
    "on a [type] and asks about [topic], pre-load [X].\"\n\n"
    "Just the rule on a single line, no preamble."
)


def maybe_reflect(
    session_id: str,
    focus_entity_type: str | None,
    total_tool_calls: int,
    history: list,
) -> str | None:
    """
    Decide whether to fire a reflection prompt and, if so, return the
    suggestion text. The caller is responsible for storing it via
    db.add_context_suggestion.

    Returns None if the threshold wasn't met or we can't reach the LLM.
    """
    if total_tool_calls < REFLECTION_TOOL_CALL_THRESHOLD:
        return None
    if FAKE_SESSION:
        # Deterministic placeholder so e2e tests can exercise the path
        # without spending tokens.
        return (
            "When the user focuses on a figure and asks about an outlier, "
            "pre-load the per-sample QC summary table so I don't have to "
            "re-read the source CSV."
        )
    try:
        from core.llm import sync_anthropic_client
        client = sync_anthropic_client()   # credential-aware (oauth_cc/apikey) — model seam
        msg = client.messages.create(
            model=MODEL,
            max_tokens=180,
            system=(
                "You are reflecting on a session you just completed. The goal "
                "is to propose a single concrete rule that would reduce "
                "context-retrieval effort next time."
            ),
            messages=[
                # Compact representation of what just happened.
                {
                    "role": "user",
                    "content": (
                        f"You ran {total_tool_calls} tool calls in this "
                        f"session while focused on entities of type "
                        f"{focus_entity_type or 'workspace'}.\n\n"
                        + _REFLECTION_PROMPT
                    ),
                },
            ],
        )
        text = "".join(
            b.text for b in msg.content
            if getattr(b, "type", None) == "text"
        ).strip()
        return text or None
    except Exception:
        return None


def run_probe() -> dict | None:
    """
    Pop-quiz probe (§3.6): pick a recent entity with upstream provenance, ask
    the Guide a diagnostic question with that entity's focus context but NO
    tools, and check whether it can name the upstream from context alone.
    Failures are logged as quiz_failure context_suggestions.

    Returns a small report dict, or None if there's nothing to probe.
    """
    from core.graph.audit import add_context_suggestion
    from core.graph.entities import list_entities
    from core.graph.provenance import upstream
    from core.manifest.assembler import build_manifest, render_focus_preamble
    import content.bio.cards  # noqa: F401 — ensure builders are registered

    # Find a recent figure/result with at least one upstream entity.
    candidates = [
        e for e in reversed(list_entities(exclude_workspace=True))
        if e["type"] in ("figure", "result") and e["status"] == "active"
    ]
    target = None
    up = []
    for e in candidates:
        up = upstream(e["id"])
        if up:
            target = e
            break
    if not target:
        return None

    expected = {n["title"].lower() for n in up}
    question = (
        f"Without querying any external APIs or tools, what data or analysis "
        f"was '{target['title']}' derived from? Answer in one sentence."
    )

    if FAKE_SESSION:
        # Deterministic: simulate a context gap so the loop is demonstrable.
        answer = "I'm not sure from the context I have."
        passed = False
    else:
        try:
            from core.llm import sync_anthropic_client
            client = sync_anthropic_client()   # credential-aware — model seam
            _m = build_manifest(session_id='probe', turn_index=0,
                                focus_entity_id=target["id"], thread_id=None)
            _focus_text, _ = render_focus_preamble(_m)
            system = _focus_text + (
                "You are answering a self-check question from your loaded "
                "context only. Do not speculate; if you don't know, say so."
            )
            msg = client.messages.create(
                model=MODEL, max_tokens=150, system=system,
                messages=[{"role": "user", "content": question}],
            )
            answer = "".join(
                b.text for b in msg.content if getattr(b, "type", None) == "text"
            ).strip()
            passed = any(name in answer.lower() for name in expected)
        except Exception:
            return None

    if not passed:
        suggestion = (
            f"When the user focuses on a {target['type']} and asks how it was "
            f"made, pre-load the titles of its upstream entities "
            f"({', '.join(sorted(expected))}) so I can answer without retrieval."
        )
        add_context_suggestion(
            session_id="probe", entity_type=target["type"],
            trigger="quiz_failure", suggestion=suggestion,
        )
    return {
        "entity_id": target["id"], "entity_type": target["type"],
        "question": question, "answer": answer, "passed": passed,
    }


def policy_path_for(entity_type: str | None) -> Path:
    # knowhow lives at bio/knowhow/, not bio/lifecycle/knowhow.
    base = Path(__file__).parent.parent / "knowhow" / "context_policy"
    base.mkdir(parents=True, exist_ok=True)
    safe = (entity_type or "workspace").replace("/", "_")
    return base / f"{safe}.md"


def _dedupe_bullets(text: str) -> str:
    """Drop repeat `- ...` bullets (keeping first occurrence). Preserves
    everything else (header, blank lines, non-bullet content). Comparison
    strips surrounding whitespace so trivially-different copies collapse."""
    seen: set[str] = set()
    out: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("- "):
            body = s[2:].strip()
            if body in seen:
                continue
            seen.add(body)
        out.append(line)
    rendered = "\n".join(out)
    return rendered + ("\n" if text.endswith("\n") else "")


def append_to_policy(entity_type: str | None, suggestion: str) -> Path:
    """Promote a suggestion into the per-type policy file. Idempotent —
    a second promotion of the same text is a no-op (also rewrites the
    file deduped if historical duplicates were sitting in there)."""
    path = policy_path_for(entity_type)
    s = (suggestion or "").strip()
    if not s:
        return path
    existed = path.exists()
    current = path.read_text() if existed else ""
    present = {
        line.strip()[2:].strip()
        for line in current.splitlines()
        if line.strip().startswith("- ")
    }
    if s in present:
        # Already promoted — opportunistically clean any historical
        # duplication so the model stops seeing the repeat.
        cleaned = _dedupe_bullets(current)
        if cleaned != current:
            path.write_text(cleaned)
        return path
    header = f"# Context policy: {entity_type or 'workspace'}\n\n" if not existed else ""
    new_text = header + current.rstrip("\n") + ("\n" if current else "") + f"- {s}\n"
    path.write_text(_dedupe_bullets(new_text))
    return path


def policy_for(entity_type: str | None) -> str:
    """Read the per-type policy if one exists, returning '' otherwise.
    Returns a deduped view — historical duplicates in the on-disk file
    are collapsed in-memory so the model never sees the repeat. Doesn't
    rewrite the file (writes are reserved for explicit promotions)."""
    path = policy_path_for(entity_type)
    if not path.exists():
        return ""
    return _dedupe_bullets(path.read_text())


# ---------- Hook handlers ----------
# Pass D: end-of-turn reflection registered as an on_stop hook.

from core.hooks.dispatcher import register as _register_hook


def _on_stop_reflect(ctx: dict) -> None:
    """ctx: session_id, focus_entity_type, total_tool_calls, history.
    On suggestion, ctx['suggestion'] is set so guide can emit the SSE event."""
    suggestion = maybe_reflect(
        session_id=ctx["session_id"],
        focus_entity_type=ctx.get("focus_entity_type"),
        total_tool_calls=ctx.get("total_tool_calls") or 0,
        history=ctx.get("history") or [],
    )
    if suggestion:
        ctx["suggestion"] = suggestion


_register_hook("on_stop", _on_stop_reflect, priority=10)
