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

from config import API_KEY, MODEL, FAKE_SESSION


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
        import anthropic
        client = anthropic.Anthropic(api_key=API_KEY)
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


def policy_path_for(entity_type: str | None) -> Path:
    base = Path(__file__).parent / "knowhow" / "context_policy"
    base.mkdir(parents=True, exist_ok=True)
    safe = (entity_type or "workspace").replace("/", "_")
    return base / f"{safe}.md"


def append_to_policy(entity_type: str | None, suggestion: str) -> Path:
    """Append a promoted suggestion to the per-type policy file."""
    path = policy_path_for(entity_type)
    existed = path.exists()
    with path.open("a") as f:
        if not existed:
            f.write(f"# Context policy: {entity_type or 'workspace'}\n\n")
        f.write(f"- {suggestion.strip()}\n")
    return path


def policy_for(entity_type: str | None) -> str:
    """Read the per-type policy if one exists, returning '' otherwise."""
    path = policy_path_for(entity_type)
    if path.exists():
        return path.read_text()
    return ""
