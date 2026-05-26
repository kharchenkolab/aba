"""
Advisor agents — secondary voices that surface their perspective at lifecycle
events on entities (see aba_arch2.md §2.4, §3.5).

Phase 10 introduces the **Skeptic** — challenges interpretations of results
and findings. Methodologist, Explorer, and Stylist will follow.

Agents communicate through the entity store, not through direct message
passing. The Skeptic writes its review to `advisor_notes` on the entity;
the frontend's AdvisorRail surfaces those notes when the user focuses the
entity.
"""
from __future__ import annotations
from typing import Optional

from config import API_KEY, MODEL, FAKE_SESSION
from db import get_entity, add_advisor_note, list_advisor_notes, edges_from, edges_to


_SKEPTIC_SYSTEM = (
    "You are the Skeptic — a critical-but-constructive reviewer of scientific "
    "interpretations. Your role is to challenge a *result* (an interpretation "
    "attached to a figure or table). You ask the questions a reviewer would: "
    "are there obvious alternative explanations? Missing controls? Could "
    "this be a technical artifact, batch effect, or sampling issue? Is the "
    "claim proportionate to the evidence?\n\n"
    "Write 3–5 sentences. Be specific and grounded in what you can see — "
    "do not invent data. Lead with the strongest concern. If the result "
    "looks solid, say so briefly and name the single thing that would "
    "strengthen it further. Plain prose; no headings, no bullet lists."
)


_FAKE_NOTE = {
    "skeptic": (
        "[Skeptic, fake mode] One immediate concern: a single outlier sample "
        "drives the interpretation — confirm that excluding it doesn't dissolve "
        "the effect. The cluster size is small; a permutation test or a hold-out "
        "check would strengthen the claim."
    ),
    "methodologist": (
        "[Methodologist, fake mode] The pipeline order looks standard. One flag: "
        "confirm highly_variable_genes ran on raw counts if you used the "
        "seurat_v3 flavor — running it post-log1p changes the selection."
    ),
    "explorer": (
        "[Explorer, fake mode] You haven't looked at how the metrics vary by "
        "condition yet — a per-condition split of mt_fraction and n_genes might "
        "reveal a batch effect worth ruling out before clustering."
    ),
    "stylist": (
        "[Stylist, fake mode] This sentence is carrying two claims at once; "
        "splitting it would make each easier to support and cite."
    ),
}


def _ask(advisor: str, system: str, prompt: str, max_tokens: int = 400) -> str:
    """One-shot Haiku call for an advisor; deterministic placeholder in fake mode."""
    if FAKE_SESSION:
        return _FAKE_NOTE.get(advisor, f"[{advisor}, fake mode] (placeholder)")
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    msg = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


def _ask_skeptic(prompt: str) -> str:
    return _ask("skeptic", _SKEPTIC_SYSTEM, prompt)


def _build_skeptic_prompt(result: dict) -> str:
    """
    Pull together what the Skeptic should know about a result entity:
      - the user's interpretation
      - the producing figure's title (and producing_code, if available)
      - any provenance trail
    """
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
            # Climb one more level if we can.
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
    """
    Run the Skeptic over a result entity. Writes the review as an advisor
    note. Returns the note dict, or None if the entity isn't a result.
    """
    result = get_entity(result_id)
    if not result:
        return None
    if result["type"] != "result":
        return None
    text = _ask_skeptic(_build_skeptic_prompt(result))
    note_id = add_advisor_note(
        result_id, advisor="skeptic", text=text,
        metadata={"trigger": "promotion"},
    )
    return {
        "id": note_id,
        "entity_id": result_id,
        "advisor": "skeptic",
        "text": text,
    }


_METHODOLOGIST_SYSTEM = (
    "You are the Methodologist — you review the *design* of an analysis: are "
    "the methods, parameters, controls, and statistics appropriate for the "
    "data and question? Look at the producing code. Flag the single most "
    "important methodological concern (wrong default, missing control, "
    "questionable parameter order, absent multiple-testing correction). If the "
    "method is sound, say so and name one optional improvement. 2–4 sentences, "
    "plain prose, grounded in what you can see in the code."
)

_EXPLORER_SYSTEM = (
    "You are the Explorer — you suggest analyses the scientist hasn't done yet. "
    "Given a dataset's columns and a preview, propose ONE concrete, high-value "
    "next analysis that would likely reveal something (a comparison, a QC check, "
    "a confound to rule out). Be specific to the columns present. 2–3 sentences, "
    "phrased as a friendly suggestion, not a lecture."
)

_STYLIST_SYSTEM = (
    "You are the Stylist — you improve the clarity and precision of scientific "
    "writing. Given a passage, point out the single change that would most "
    "improve it (splitting an overloaded sentence, removing hedging, tightening "
    "a claim to match its evidence). 2–3 sentences. Don't rewrite the whole "
    "thing; name the lever."
)


def methodologist_review(analysis_id: str) -> Optional[dict]:
    e = get_entity(analysis_id)
    if not e or e["type"] != "analysis":
        return None
    # Gather the producing code of the analysis's child figures.
    code_snippets = []
    for edge in edges_to(analysis_id):  # things generated by this analysis
        child = get_entity(edge["source_id"])
        if child and child.get("producing_code"):
            code_snippets.append(child["producing_code"])
    code = "\n\n".join(code_snippets)[:1500] or (e.get("producing_code") or "")
    prompt = (
        f"Analysis: {e['title']}\n\nProducing code:\n```python\n{code}\n```\n\n"
        "Review the methodology. What's the most important thing to check?"
    )
    text = _ask("methodologist", _METHODOLOGIST_SYSTEM, prompt)
    note_id = add_advisor_note(analysis_id, advisor="methodologist", text=text,
                               metadata={"trigger": "run_complete"})
    return {"id": note_id, "entity_id": analysis_id, "advisor": "methodologist", "text": text}


def explorer_suggest(dataset_id: str) -> Optional[dict]:
    e = get_entity(dataset_id)
    if not e or e["type"] != "dataset":
        return None
    # Don't re-fire if the Explorer already spoke about this dataset.
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
    text = _ask("explorer", _EXPLORER_SYSTEM, prompt)
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
    text = _ask("stylist", _STYLIST_SYSTEM, prompt)
    note_id = add_advisor_note(narrative_id, advisor="stylist", text=text,
                               metadata={"trigger": "narrative_focus"})
    return {"id": note_id, "entity_id": narrative_id, "advisor": "stylist", "text": text}


# ---------- Hook handlers ----------
# Pass D: methodologist auto-trigger on analysis-complete (was inline in
# guide.py). Will move to bio/advisors/methodologist.py in Pass F when the
# advisors module is reshaped.

import asyncio as _asyncio
from core.hooks.dispatcher import register as _register_hook


def _on_post_tool_methodologist(ctx: dict) -> None:
    """When new entities were registered AND we know which analysis they
    belong to, fire the Methodologist review asynchronously."""
    if not ctx.get("new_entities"):
        return
    analysis_ctx = ctx.get("analysis_ctx") or {}
    aid = analysis_ctx.get("analysis_id")
    if not aid:
        return
    # Run-in-executor — the review is a non-streaming Haiku call that we
    # don't want to block the agent loop on.
    try:
        loop = _asyncio.get_event_loop()
        loop.run_in_executor(None, methodologist_review, aid)
    except RuntimeError:
        # No running loop (e.g. called outside async context) — run inline.
        methodologist_review(aid)


# Priority 20 so artifact-registration (priority 10) runs first; we depend
# on its analysis_ctx['analysis_id'] mutation.
_register_hook("on_post_tool", _on_post_tool_methodologist, priority=20)
