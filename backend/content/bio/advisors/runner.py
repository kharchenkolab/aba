"""Advisor functions — prompt builders + one-shot calls keyed off the
loaded AgentSpec. Pre-Pass-F these lived in backend/advisors.py; the
T1.2 cleanup relocates them here so adding/removing an advisor is
fully contained in content/bio/advisors/.
"""
from __future__ import annotations
import os
from typing import Optional

from core import config
from core.graph.audit import add_advisor_note, list_advisor_notes
from core.graph.edges import edges_to
from core.graph.entities import get_entity
from core.runtime.agent import get_agent_spec, run_advisor_one_shot


def advisors_enabled() -> bool:
    """Advisors (skeptic/methodologist/explorer/stylist) are PAUSED by default
    pending refinement — their suggestions aren't useful enough yet. The code is
    intact; re-enable with ABA_ADVISORS_ENABLED=1. Guarding the single _run()
    chokepoint means no advisor LLM call fires on any hook while paused."""
    return config.settings.advisors_enabled.get()


def _run(advisor_name: str, prompt: str, max_tokens: int = 400,
         *, parent_run_id: Optional[str] = None,
         focus_entity_id: Optional[str] = None,
         thread_id: Optional[str] = None) -> Optional[str]:
    if not advisors_enabled():
        return None
    spec = get_agent_spec(advisor_name)
    if spec is None:
        return None
    return run_advisor_one_shot(
        spec, user_prompt=prompt, max_tokens=max_tokens,
        parent_run_id=parent_run_id,
        focus_entity_id=focus_entity_id,
        thread_id=thread_id,
    )


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
            # Post-cutover: resolve code via the exec record.
            from core.graph.exec_records import lookup_code_for_entity
            _fig_code = lookup_code_for_entity(fig)
            if _fig_code:
                code = _fig_code
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


def skeptic_review(result_id: str, *,
                   parent_run_id: Optional[str] = None,
                   thread_id: Optional[str] = None) -> Optional[dict]:
    result = get_entity(result_id)
    if not result or result["type"] != "result":
        return None
    text = _run("skeptic", _build_skeptic_prompt(result),
                parent_run_id=parent_run_id, focus_entity_id=result_id, thread_id=thread_id)
    if text is None:
        return None
    note_id = add_advisor_note(
        result_id, advisor="skeptic", text=text,
        metadata={"trigger": "promotion"},
    )
    return {"id": note_id, "entity_id": result_id, "advisor": "skeptic", "text": text}


def methodologist_review(analysis_id: str, *,
                         parent_run_id: Optional[str] = None,
                         thread_id: Optional[str] = None) -> Optional[dict]:
    e = get_entity(analysis_id)
    if not e or e["type"] != "analysis":
        return None
    # Post-cutover: code resolution goes through the exec records. For the
    # analysis (= Run) itself, that's `aggregated_code_for_run`. For each
    # child artifact, it's `lookup_code_for_entity`. The legacy
    # `producing_code` column is no longer the source of truth.
    from core.graph.exec_records import (
        aggregated_code_for_run as _agg_code,
        lookup_code_for_entity as _ent_code,
    )
    code_snippets = []
    for edge in edges_to(analysis_id):
        child = get_entity(edge["source_id"])
        if not child:
            continue
        c = _ent_code(child)
        if c:
            code_snippets.append(c)
    code = "\n\n".join(code_snippets)[:1500] or _agg_code(analysis_id)
    prompt = (
        f"Analysis: {e['title']}\n\nProducing code:\n```python\n{code}\n```\n\n"
        "Review the methodology. What's the most important thing to check?"
    )
    text = _run("methodologist", prompt,
                parent_run_id=parent_run_id, focus_entity_id=analysis_id, thread_id=thread_id)
    if text is None:
        return None
    note_id = add_advisor_note(analysis_id, advisor="methodologist", text=text,
                               metadata={"trigger": "run_complete"})
    return {"id": note_id, "entity_id": analysis_id, "advisor": "methodologist", "text": text}


def explorer_suggest(dataset_id: str, *,
                     parent_run_id: Optional[str] = None,
                     thread_id: Optional[str] = None) -> Optional[dict]:
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
    text = _run("explorer", prompt,
                parent_run_id=parent_run_id, focus_entity_id=dataset_id, thread_id=thread_id)
    if text is None:
        return None
    note_id = add_advisor_note(dataset_id, advisor="explorer", text=text,
                               metadata={"trigger": "dataset_focus"})
    return {"id": note_id, "entity_id": dataset_id, "advisor": "explorer", "text": text}


def stylist_review(narrative_id: str, *,
                   parent_run_id: Optional[str] = None,
                   thread_id: Optional[str] = None) -> Optional[dict]:
    e = get_entity(narrative_id)
    if not e or e["type"] != "narrative":
        return None
    text_in = (e.get("metadata") or {}).get("text", "") or e.get("notes", "")
    if not text_in:
        return None
    prompt = f"Passage:\n{text_in}\n\nWhat's the one change that would most improve it?"
    text = _run("stylist", prompt,
                parent_run_id=parent_run_id, focus_entity_id=narrative_id, thread_id=thread_id)
    if text is None:
        return None
    note_id = add_advisor_note(narrative_id, advisor="stylist", text=text,
                               metadata={"trigger": "narrative_focus"})
    return {"id": note_id, "entity_id": narrative_id, "advisor": "stylist", "text": text}
