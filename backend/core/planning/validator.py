"""Plan normalization + validation.

Normalizes `present_plan` tool input into a Plan object: string steps
become {title}-only PlanSteps. Then runs lightweight checks and attaches
concerns:
  - Empty step title → error.
  - Step claims a skill that isn't in the registered skill catalog →
    warn (downgrade to ad-hoc).
  - Step has no description and no skill → info (small nudge).

The validator never blocks execution; it just surfaces what the user
should know before they click Go. The frontend renders warns/errors
inline with each step.
"""
from __future__ import annotations
from typing import Any

from core.planning.types import Plan, PlanStep, PlanConcern


# Resolved at registration time from content (bio/advisors/*.yaml feeds a
# similar registry; for skills we don't have one yet, so the catalog is
# the list of skills the agent should reference. T2.5 MVP: small allowlist
# pulled from bio/prompts/recipes.md procedures + the existing knowhow files.
KNOWN_SKILLS: set[str] = set()


def register_skill(name: str) -> None:
    """Bio registers its known skills here at import time. Until the
    skills/ subsystem ships, this catalog is the validator's reference."""
    KNOWN_SKILLS.add(name)


import re as _re

# Models sometimes wrap list items in XML-ish tags or emit a single string
# instead of a list. Most common patterns we've seen:
#   "<item>A</item><item>B</item>"
#   "- A\n- B\n- C"
#   "1. A\n2. B"
#   "A\nB\nC"
# Without coercion, a string is iterated character-by-character — every
# character becomes a list item. Disastrous in the UI.
_ITEM_TAG_RE = _re.compile(r"<\s*item\s*>(.*?)<\s*/\s*item\s*>", _re.DOTALL | _re.IGNORECASE)
_LIST_PREFIX_RE = _re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+")


def _coerce_string_list(x: Any) -> list[str]:
    """Best-effort: turn a model-supplied "list of strings" into an
    actual list of strings. Handles already-list inputs, XML-wrapped
    items, bullet/numbered lines, and bare strings."""
    if x is None:
        return []
    if isinstance(x, list):
        return [str(item).strip() for item in x if str(item).strip()]
    if isinstance(x, str):
        s = x.strip()
        if not s:
            return []
        # XML-ish <item>...</item> wrapping
        tagged = _ITEM_TAG_RE.findall(s)
        if tagged:
            return [t.strip() for t in tagged if t.strip()]
        # Newline-separated (with optional bullet/number prefixes)
        if "\n" in s:
            out: list[str] = []
            for line in s.splitlines():
                stripped = _LIST_PREFIX_RE.sub("", line).strip()
                if stripped:
                    out.append(stripped)
            return out
        # Single line — one item
        return [s]
    # Other shapes (dict, int, etc.) → render as string singleton.
    return [str(x).strip()] if str(x).strip() else []


def normalize_plan(raw: dict[str, Any]) -> Plan:
    """Coerce model output into a Plan object. Tolerant of common model-
    output drift: strings instead of lists, XML-ish item wrappers,
    alternative key names. Steps as strings become {title}-only PlanSteps."""

    # Steps: support list[dict|str] (canonical) OR a string (split it).
    steps_raw = raw.get("steps")
    # Some models put steps under a different key — try common alternatives.
    if not steps_raw:
        for alt in ("plan_steps", "procedure", "actions", "todo"):
            if raw.get(alt):
                steps_raw = raw[alt]
                break
    if isinstance(steps_raw, str):
        steps_raw = _coerce_string_list(steps_raw)
    elif not isinstance(steps_raw, list):
        steps_raw = []

    norm_steps: list[PlanStep] = []
    for i, s in enumerate(steps_raw, start=1):
        if isinstance(s, str):
            title = s.strip()
            if title:
                norm_steps.append(PlanStep(n=i, title=title))
        elif isinstance(s, dict):
            title = (s.get("title") or "").strip()
            if not title and isinstance(s.get("description"), str):
                # Some models put the step text under "description" only.
                title = s["description"][:80].strip()
            norm_steps.append(PlanStep(
                n=i,
                title=title,
                description=(s.get("description") or "").strip(),
                expected_outputs=_coerce_string_list(s.get("expected_outputs")),
                skill=(s.get("skill") or "").strip() or None,
                parameters=dict(s.get("parameters") or {}),
            ))
        else:
            # Unsupported shape — drop with a synthesized title for traceability.
            norm_steps.append(PlanStep(n=i, title=f"(unparsed step {i})"))

    return Plan(
        title=str(raw.get("title") or "").strip(),
        summary=str(raw.get("summary") or "").strip(),
        rationale=str(raw.get("rationale") or "").strip(),
        assumptions=_coerce_string_list(raw.get("assumptions")),
        steps=norm_steps,
    )


def validate_plan(plan: Plan) -> Plan:
    """Mutates `plan` by appending concerns. Returns the same plan for
    fluent chaining."""
    for step in plan.steps:
        if not step.title:
            plan.concerns.append(PlanConcern(
                step_n=step.n, level="error",
                message="Step has no title.",
            ))
            continue
        if step.skill and KNOWN_SKILLS and step.skill not in KNOWN_SKILLS:
            plan.concerns.append(PlanConcern(
                step_n=step.n, level="warn",
                message=(
                    f"Skill {step.skill!r} isn't in the known catalog. "
                    f"This step will run ad-hoc — please confirm or pick a "
                    f"registered skill."
                ),
            ))
        if not step.skill and not step.description:
            plan.concerns.append(PlanConcern(
                step_n=step.n, level="info",
                message="No description or skill — what does this step do?",
            ))
    if not plan.steps:
        plan.concerns.append(PlanConcern(
            step_n=None, level="error",
            message="The plan has no steps.",
        ))
    if plan.steps and not plan.assumptions:
        plan.concerns.append(PlanConcern(
            step_n=None, level="info",
            message=(
                "No assumptions listed. Naming defaults (modality, thresholds, "
                "scope) helps the user catch wrong premises before Go."
            ),
        ))
    return plan
