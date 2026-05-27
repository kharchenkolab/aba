"""Advisor agents — thin shim that drives spec-defined advisors.

Pass F (arch3_plan.md): the per-advisor system-prompt text and fake-mode
placeholders moved into content/bio/{prompts,advisors}/*. This module
now:
  - imports bio/advisors at startup to register all YAML specs,
  - keeps the existing function names (skeptic_review, etc.) so callers
    don't break,
  - dispatches each function through core/runtime/agent.run_advisor_one_shot
    which loads model/system/fake_text from the spec.

Adding a new advisor is now: a YAML spec + a prompt MD file. No code
change here.
"""
from __future__ import annotations
from typing import Optional

from core.graph.audit import add_advisor_note, list_advisor_notes
from core.graph.edges import edges_to
from core.graph.entities import get_entity
from core.runtime.agent import get_agent_spec, run_advisor_one_shot
# Load all advisor specs at import time.
import content.bio.advisors  # noqa: F401


def _run(advisor_name: str, prompt: str, max_tokens: int = 400) -> Optional[str]:
    spec = get_agent_spec(advisor_name)
    if spec is None:
        return None
    return run_advisor_one_shot(spec, user_prompt=prompt, max_tokens=max_tokens)


def _build_skeptic_prompt(result: dict) -> str:
    interpretation = (result.get("metadata") or {}).get("interpretation", "")
    title = result.get("title", "")
    evidence_id = (result.get("metadata") or {}).get("evidence_figure")
    bits = [
        "## The result under review",
        f"Title: {title}",
        f"Interpretation (verbatim from the user): {interpretation}",
    ]
    if evidence_id:
        fig = get_entity(evidence_id)
        if fig:
            bits.append(f"\nSupporting figure: {fig['title']}")
            if fig.get("producing_code"):
                code = fig["producing_code"]
                if len(code) > 800:
                    code = code[:800] + "\n# ... (truncated)"
                bits.append("Producing code:\n```python\n" + code + "\n```")
            parent = fig.get("parent_entity_id")
            if parent:
                p = get_entity(parent)
                if p:
                    bits.append(f"Came from: {p['type']} '{p['title']}'")
    bits.append(
        "\nReview this result. What's the most important concern that would "
        "make a careful reviewer pause? Keep it to 3–5 sentences."
    )
    return "\n".join(bits)


def skeptic_review(result_id: str) -> Optional[dict]:
    result = get_entity(result_id)
    if not result or result["type"] != "result":
        return None
    text = _run("skeptic", _build_skeptic_prompt(result))
    if text is None:
        return None
    note_id = add_advisor_note(
        result_id, advisor="skeptic", text=text,
        metadata={"trigger": "promotion"},
    )
    return {"id": note_id, "entity_id": result_id, "advisor": "skeptic", "text": text}


def methodologist_review(analysis_id: str) -> Optional[dict]:
    e = get_entity(analysis_id)
    if not e or e["type"] != "analysis":
        return None
    code_snippets = []
    for edge in edges_to(analysis_id):
        child = get_entity(edge["source_id"])
        if child and child.get("producing_code"):
            code_snippets.append(child["producing_code"])
    code = "\n\n".join(code_snippets)[:1500] or (e.get("producing_code") or "")
    prompt = (
        f"Analysis: {e['title']}\n\nProducing code:\n```python\n{code}\n```\n\n"
        "Review the methodology. What's the most important thing to check?"
    )
    text = _run("methodologist", prompt)
    if text is None:
        return None
    note_id = add_advisor_note(analysis_id, advisor="methodologist", text=text,
                               metadata={"trigger": "run_complete"})
    return {"id": note_id, "entity_id": analysis_id, "advisor": "methodologist", "text": text}


def explorer_suggest(dataset_id: str) -> Optional[dict]:
    e = get_entity(dataset_id)
    if not e or e["type"] != "dataset":
        return None
    if any(n["advisor"] == "explorer" for n in list_advisor_notes(dataset_id)):
        return None
    cols = ""
    path = e.get("artifact_path")
    if path:
        try:
            import pandas as pd
            df = pd.read_csv(path, nrows=5)
            cols = ", ".join(str(c) for c in df.columns)
        except Exception:
            cols = ""
    prompt = (
        f"Dataset: {e['title']}\nColumns: {cols or 'unknown'}\n\n"
        "Suggest one high-value analysis the scientist hasn't done yet."
    )
    text = _run("explorer", prompt)
    if text is None:
        return None
    note_id = add_advisor_note(dataset_id, advisor="explorer", text=text,
                               metadata={"trigger": "dataset_focus"})
    return {"id": note_id, "entity_id": dataset_id, "advisor": "explorer", "text": text}


def stylist_review(narrative_id: str) -> Optional[dict]:
    e = get_entity(narrative_id)
    if not e or e["type"] != "narrative":
        return None
    text_in = (e.get("metadata") or {}).get("text", "") or e.get("notes", "")
    if not text_in:
        return None
    prompt = f"Passage:\n{text_in}\n\nWhat's the one change that would most improve it?"
    text = _run("stylist", prompt)
    if text is None:
        return None
    note_id = add_advisor_note(narrative_id, advisor="stylist", text=text,
                               metadata={"trigger": "narrative_focus"})
    return {"id": note_id, "entity_id": narrative_id, "advisor": "stylist", "text": text}


# ---------- Hook handlers ----------
# Pass D: methodologist auto-trigger on analysis-complete. The handler
# stays here for now; in the next reshape it lands in
# bio/advisors/handlers.py.

import asyncio as _asyncio
from core.hooks.dispatcher import register as _register_hook


def _on_post_tool_methodologist(ctx: dict) -> None:
    if not ctx.get("new_entities"):
        return
    analysis_ctx = ctx.get("analysis_ctx") or {}
    aid = analysis_ctx.get("analysis_id")
    if not aid:
        return
    try:
        loop = _asyncio.get_event_loop()
        loop.run_in_executor(None, methodologist_review, aid)
    except RuntimeError:
        methodologist_review(aid)


_register_hook("on_post_tool", _on_post_tool_methodologist, priority=20)
