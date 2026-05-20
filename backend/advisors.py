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
from db import get_entity, add_advisor_note, edges_from


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


def _ask_skeptic(prompt: str) -> str:
    """One-shot Haiku call returning the Skeptic's review text."""
    if FAKE_SESSION:
        # In fake mode, return a deterministic placeholder so e2e tests run
        # without burning API tokens.
        return (
            "[Skeptic, fake mode] One immediate concern: a single outlier "
            "sample drives the interpretation — confirm that excluding it "
            "doesn't dissolve the effect. The cluster size is small; a "
            "permutation test or a hold-out check would strengthen the claim."
        )
    import anthropic
    client = anthropic.Anthropic(api_key=API_KEY)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=400,
        system=_SKEPTIC_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text").strip()


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
