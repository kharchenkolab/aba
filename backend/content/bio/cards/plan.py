"""Focus card for plan entities.

The Guide may revisit a previously-presented plan ("did we ever discuss
QC thresholds?"). The plan card surfaces the plan's lifecycle state +
summary + step titles + concern count, so the Guide knows what was
proposed and where it stands.
"""
from __future__ import annotations

from core.manifest.assembler import _generic_card, register_card_builder


def build_plan_card(entity: dict) -> tuple[str, list[str]]:
    text, fields = _generic_card(entity)
    meta = entity.get("metadata") or {}
    plan = meta.get("plan") or {}
    lifecycle = meta.get("plan_lifecycle") or "validated"
    extras = [f"This is a plan — lifecycle state: {lifecycle}."]
    fields.append("plan_lifecycle")

    if plan.get("summary"):
        extras.append("Summary: " + str(plan["summary"]).strip())
        fields.append("plan_summary")

    steps = plan.get("steps") or []
    if steps:
        titles = [s.get("title") or f"step {s.get('n', '?')}" for s in steps[:10]]
        extras.append(f"Steps ({len(steps)}): " + "; ".join(titles)
                      + (" …" if len(steps) > 10 else ""))
        fields.append("plan_steps")

    concerns = plan.get("concerns") or []
    if concerns:
        n_err = sum(1 for c in concerns if c.get("level") == "error")
        n_warn = sum(1 for c in concerns if c.get("level") == "warn")
        bits = []
        if n_err:  bits.append(f"{n_err} error")
        if n_warn: bits.append(f"{n_warn} warning")
        if bits:
            extras.append("Validator concerns: " + ", ".join(bits) + ".")
            fields.append("plan_concerns")

    if extras:
        text = text + "\n" + "\n".join(extras)
    return text, fields


register_card_builder("plan", build_plan_card)
