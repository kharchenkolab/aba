"""
Scenarios — variants of an existing figure produced by re-running the
baseline's producing_code with substituted parameters.

End-user mental model: the word "branch" never appears. The user says
"what if we use a tighter mt_fraction cutoff?" and a scenario variant
appears alongside the baseline, with a Compare toggle in the canvas.

This module is the bottom half: given an explicit description (or new
code), it rewrites the baseline's code (via Haiku) and runs it.
"""
from __future__ import annotations
import json
from typing import Optional

from config import API_KEY, MODEL, FAKE_SESSION
from db import create_entity, get_entity, add_edge
from tools import execute_tool
from content.bio.lifecycle.registry import _title_from_code


_REWRITE_SYSTEM = (
    "You are rewriting a small Python analysis script to produce a scenario "
    "variant. The user will describe a change (e.g. 'use mt_fraction cutoff "
    "0.10' or 'exclude sample S4'). Output ONLY the modified Python code — "
    "no commentary, no markdown fences, no preamble. The code must continue "
    "to save its figure with plt.savefig() so the harness captures it. Keep "
    "all variable names and structure that aren't part of the change."
)


def _rewrite_code_via_llm(original_code: str, description: str) -> str:
    """One-shot Haiku call to rewrite a script for a scenario variant."""
    if FAKE_SESSION:
        raise RuntimeError(
            "scenario rewrite needs the live LLM; pass `code` directly in fake mode",
        )
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        system=_REWRITE_SYSTEM,
        messages=[{
            "role": "user",
            "content": (
                f"Modify this code for the following scenario: {description}\n\n"
                f"```python\n{original_code}\n```"
            ),
        }],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
    # Strip markdown fences if the model included them despite the system prompt.
    text = text.strip()
    if text.startswith("```"):
        first_nl = text.find("\n")
        text = text[first_nl + 1:] if first_nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[: -3]
    return text.strip()


def create_scenario_variant(
    baseline_id: str,
    description: str,
    code: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """
    Run a scenario variant of `baseline_id`'s producing_code.

    - `description` is what the user typed ("what if...").
    - `code` lets callers (or tests) supply the modified code directly,
      bypassing the LLM rewrite.

    Returns the new figure entity record.
    """
    baseline = get_entity(baseline_id)
    if not baseline:
        raise ValueError(f"baseline {baseline_id} not found")
    if baseline["type"] != "figure":
        raise ValueError("scenarios can only be derived from figures for now")
    if not baseline.get("producing_code"):
        raise ValueError("baseline has no producing_code; can't derive a scenario")

    new_code = code or _rewrite_code_via_llm(baseline["producing_code"], description)

    result_json = execute_tool("run_python", {"code": new_code})
    result = json.loads(result_json)
    if result.get("error"):
        raise ValueError(f"scenario run failed: {result['error']}")
    plots = result.get("plots") or []
    if not plots:
        stderr = result.get("stderr", "")[:300]
        raise ValueError(
            "scenario produced no figures" + (f"; stderr: {stderr}" if stderr else "")
        )

    plot = plots[0]
    derived_title = title or _title_from_code(new_code) or f"{baseline['title']} ({description})"
    eid = create_entity(
        entity_type="figure",
        title=derived_title[:120],
        artifact_path=plot["url"],
        producing_code=new_code,
        parent_entity_id=baseline["parent_entity_id"],
        scenario_of=baseline_id,
        metadata={
            "scenario_description": description,
            "original_name": plot.get("original_name", "figure.png"),
        },
    )
    add_edge(eid, baseline_id, "variantOf", {"description": description})
    return get_entity(eid)  # type: ignore[return-value]
