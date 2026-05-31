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

# Leaked function-call / XML-ish markup some models (esp. small ones) emit INTO
# values — e.g. '<parameter name="item">', '<parameter name="title">…', '</steps>',
# '<invoke …>'. Seen live leaking into step TITLES, turning a plan into junk steps.
_LEAK_TAG_RE = _re.compile(r"</?(?:parameter|assumptions|steps|invoke|function_calls|antml)[^>]*>", _re.IGNORECASE)
_LEAK_ARR_RE = _re.compile(r"\[\s*\{.*?\}\s*\]", _re.S)


def _strip_leak(s: str) -> str:
    """Strip leaked tool-call/XML markup and a trailing crammed JSON array."""
    s = _LEAK_TAG_RE.sub("", s or "")
    s = _LEAK_ARR_RE.sub("", s)
    return s.strip()


def _loads_loose(s: str):
    """Parse a model-emitted array/object string into Python data, tolerant of
    the two shapes models actually produce: strict JSON, and Python-repr (single
    quotes, e.g. `['adata.layers["counts"] preserved']`) which json.loads rejects.
    Returns the parsed value, or None if neither parser succeeds."""
    import json as _json
    try:
        return _json.loads(s)
    except Exception:
        pass
    import ast as _ast
    try:
        return _ast.literal_eval(s)   # handles single-quoted strings, etc.; literals only (safe)
    except Exception:
        return None


def _salvage_step_objects(s: str) -> list[dict]:
    """Last-resort recovery when a `steps` string LOOKS like a JSON array of step
    objects but fails BOTH strict and loose parsing — usually because the model
    emitted one malformed value (e.g. an unquoted array element:
    `"expected_outputs": ["x.png", ~2000 HVGs ...]`). Rather than degrade to
    line-splitting (which turns every source line into a junk step), anchor on
    each `"title":` key and pull title/description/expected_outputs out with
    tolerant regexes. One bad value then costs at most that one field, not the
    whole plan. Returns [] if it doesn't look like step objects at all."""
    title_iter = list(_re.finditer(r'"title"\s*:', s))
    if not title_iter:
        return []
    _STR = r'"((?:[^"\\]|\\.)*)"'        # a JSON double-quoted string body
    out: list[dict] = []
    for j, m in enumerate(title_iter):
        chunk = s[m.start():(title_iter[j + 1].start() if j + 1 < len(title_iter) else len(s))]
        tm = _re.search(r'"title"\s*:\s*' + _STR, chunk)
        dm = _re.search(r'"description"\s*:\s*' + _STR, chunk)
        title = (tm.group(1).strip() if tm else "")
        desc = (dm.group(1).strip() if dm else "")
        om = _re.search(r'"expected_outputs"\s*:\s*\[(.*?)\]', chunk, _re.S)
        outs = [o for o in _re.findall(_STR, om.group(1)) if o.strip()] if om else []
        sm = _re.search(r'"skill"\s*:\s*' + _STR, chunk)
        if title or desc:
            out.append({"title": title, "description": desc,
                        "expected_outputs": outs, "skill": (sm.group(1).strip() if sm else "")})
    return out


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
        # A model may pass the array as a serialized string ('[{"title":…}]')
        # rather than a real list — parse it (JSON or Python-repr) before falling
        # back to line-splitting. (Python-repr happens when a step value uses
        # single quotes, e.g. 'adata.layers["counts"]', which json.loads rejects;
        # without this it line-splits the array into garbled one-char/-line steps.)
        s = steps_raw.strip()
        looks_structured = s[:1] in "[{"
        parsed = _loads_loose(s) if looks_structured else None
        if isinstance(parsed, list):
            steps_raw = parsed
        elif isinstance(parsed, dict):
            steps_raw = [parsed]
        elif looks_structured:
            # Malformed-but-structured (a stray bad value): salvage step objects
            # instead of line-splitting the JSON source into junk steps. Only if
            # salvage finds nothing do we fall back to bullet/line coercion.
            salvaged = _salvage_step_objects(s)
            steps_raw = salvaged if salvaged else _coerce_string_list(steps_raw)
        else:
            steps_raw = _coerce_string_list(steps_raw)
    elif not isinstance(steps_raw, list):
        steps_raw = []

    norm_steps: list[PlanStep] = []
    for s in steps_raw:
        n = len(norm_steps) + 1   # sequential — dropped junk steps leave no gaps
        if isinstance(s, str):
            title = _strip_leak(s.strip())
            # Some models emit each step as an object-LITERAL string instead of a real
            # object — '{title: "X", description: "Y"}', often with UNQUOTED keys, so
            # json.loads / ast.literal_eval both reject it and it lands here as a bare
            # string. Pull title/description out tolerantly (quote char captured so the
            # other quote may appear inside the value).
            if title.startswith("{") and ("title" in title or "description" in title):
                mt = _re.search(r'title\s*:\s*(["\'])(.*?)\1', title)
                md = _re.search(r'description\s*:\s*(["\'])(.*?)\1', title)
                t = (mt.group(2).strip() if mt else "")
                d = (md.group(2).strip() if md else "")
                if t or d:
                    norm_steps.append(PlanStep(n=n, title=(t or d[:80]), description=d))
                    continue
            if title:
                norm_steps.append(PlanStep(n=n, title=title))
        elif isinstance(s, dict):
            title = _strip_leak((s.get("title") or "").strip())
            desc = _strip_leak((s.get("description") or "").strip())
            if not title and desc:
                # Some models put the step text under "description" only.
                title = desc[:80].strip()
            if not title and not desc:
                # Pure leaked-tag junk (e.g. a step whose title was just
                # '<parameter name="item">') — drop it rather than show a blank step.
                continue
            norm_steps.append(PlanStep(
                n=n,
                title=title,
                description=desc,
                expected_outputs=_coerce_string_list(s.get("expected_outputs")),
                skill=(s.get("skill") or "").strip() or None,
                parameters=dict(s.get("parameters") or {}),
            ))
        else:
            # Unsupported shape — drop with a synthesized title for traceability.
            norm_steps.append(PlanStep(n=n, title=f"(unparsed step {n})"))

    # Recovery: some models cram the steps as a JSON array into a *text* field
    # (e.g. function-call XML leaking into `assumptions`), leaving `steps` empty.
    # If we have no steps, scan the raw string values for an embedded step array.
    if not norm_steps:
        for v in raw.values():
            if not (isinstance(v, str) and ("title" in v and ("description" in v or "expected_outputs" in v))):
                continue
            m = _re.search(r"\[\s*\{.*\}\s*\]", v, _re.S)
            if not m:
                continue
            arr = _loads_loose(m.group(0))
            if isinstance(arr, list):
                for i, s in enumerate(arr, start=1):
                    if isinstance(s, dict):
                        title = (s.get("title") or s.get("description") or "").strip()[:120]
                        if title:
                            norm_steps.append(PlanStep(
                                n=i, title=title,
                                description=(s.get("description") or "").strip(),
                                expected_outputs=_coerce_string_list(s.get("expected_outputs"))))
                if norm_steps:
                    break

    # Recovery (XML-tagged): some models emit the steps as XML-ish blocks
    # inline in a *string* field rather than the `steps` array. Pattern:
    #   <title>...</title> <description>...</description> <expected_outputs>...</expected_outputs> <skill>...</skill>
    # Without this the chat shows the prose AND a "plan has no steps" error
    # (observed 2026-05-31). Scan every string value and any string list
    # entry (assumptions often catches the spillover).
    if not norm_steps:
        _tag = lambda t, blob: (_re.search(rf"<{t}>(.*?)</{t}>", blob, _re.S | _re.I) or None)
        def _emit_from_block(block: str, n: int) -> PlanStep | None:
            mt = _tag("title", block)
            md = _tag("description", block)
            mo = _tag("expected_outputs", block)
            ms = _tag("skill", block)
            title = (mt.group(1).strip() if mt else "")
            desc  = (md.group(1).strip() if md else "")
            if not title and not desc:
                return None
            return PlanStep(
                n=n,
                title=title or desc[:120],
                description=desc,
                expected_outputs=_coerce_string_list([mo.group(1).strip()] if mo else None),
                skill=(ms.group(1).strip() if ms else None) or None,
            )

        def _scan_strings(obj):
            if isinstance(obj, str):
                yield obj
            elif isinstance(obj, list):
                for x in obj:
                    yield from _scan_strings(x)
            elif isinstance(obj, dict):
                for x in obj.values():
                    yield from _scan_strings(x)

        for v in _scan_strings(raw):
            if "<title>" not in v.lower():
                continue
            # Split into one block per <title>...</title> ... boundary so each
            # step's tags stay grouped. Lookahead splits BEFORE each <title>.
            chunks = _re.split(r"(?=<title>)", v, flags=_re.I)
            for chunk in chunks:
                step = _emit_from_block(chunk, len(norm_steps) + 1)
                if step is not None:
                    norm_steps.append(step)
            if norm_steps:
                break

    return Plan(
        title=_strip_leak(str(raw.get("title") or "")),
        summary=_strip_leak(str(raw.get("summary") or "")),
        rationale=_strip_leak(str(raw.get("rationale") or "")),
        assumptions=[_strip_leak(a) for a in _coerce_string_list(raw.get("assumptions")) if _strip_leak(a)],
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
