"""Bio system-prompt assembler.

Composes per-turn system prompts from a declared list of blocks. Each
block knows (a) which roles it applies to and (b) which tool, if any,
must be active for it to render. This lets `build_system(active_tools,
role=...)` deliver a primary-Guide prompt with recipes / scenarios /
plan_first, an advisor prompt with just identity + behavior, etc.

A3 (agent_conditioning_plan.md): per-role manifest filtering. The role
defaults to "primary" for the existing Guide call site.

In Pass C this is wrapped by core/manifest/system_prompt_assembler.py,
which composes Manifest.knowledge_text from prompt + memory + policy.
For now bio owns the assembly directly; the assembler call site in
guide.py imports `build_system` from here.
"""
from __future__ import annotations
import contextvars
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

_HERE = Path(__file__).parent
_BIO_ROOT = _HERE.parent  # backend/content/bio/

# The turn's intent (the user's message, usually) — set by build_system for
# the duration of one assembly so retrieval-gated blocks (the skills index)
# can rank against it without changing every block's render signature.
# A ContextVar (not a plain global) keeps concurrent assemblies — Guide turn
# vs. advisor/sub-agent — from clobbering each other's intent.
_INTENT: contextvars.ContextVar[str] = contextvars.ContextVar("aba_prompt_intent", default="")


@lru_cache(maxsize=None)
def _prompt(name: str) -> str:
    """Read a prompt .md file once and cache. Stripped of trailing whitespace
    so concatenations don't double-blank."""
    return (_HERE / name).read_text().rstrip()


@lru_cache(maxsize=None)
def _bio_doc(relpath: str) -> str:
    """Read a bio doc (e.g. conventions.md) once and cache."""
    p = _BIO_ROOT / relpath
    return p.read_text().rstrip() if p.exists() else ""


def _capabilities_block(active_tools: list[dict]) -> str:
    """Dynamic block — list each active tool's name + first sentence of its
    description. Stays in code because it composes runtime state, not text."""
    if not active_tools:
        return ""
    lines = ["Your tools (use them directly for routine reads — don't ask permission):"]
    for t in active_tools:
        desc = " ".join((t.get("description") or "").split())
        # First sentence, but don't break on the "e.g." / "i.e." abbreviations
        # (a naive split(". ") truncated tool descriptions right at "(e.g").
        first = re.split(r"(?<![ei]\.[ge])\.\s", desc, maxsplit=1)[0].rstrip(".")
        lines.append(f"- {t['name']}: {first}.")
    return "\n".join(lines) + "\n\n" + _prompt("sandbox_libs.md")


@dataclass(frozen=True)
class _Block:
    """One slot in the system prompt.

    roles=None  → applies to every role
    roles={"primary"} → only the streaming, halt-capable Guide gets it
    required_tool=None → render unconditionally (subject to role)
    required_tool="present_plan" → only when that tool is in active_tools
    gate=None → render regardless of turn context
    gate(ctx) → render only when the predicate over this turn's context is true
                (e.g. only inject the scenarios/highlighting blocks when a figure
                is in focus or the user just highlighted — they're dead weight
                otherwise). ctx carries focus_is_figure / highlight_active / ….
    render(active_tools) → returns the block text, or "" to skip
    """
    name:          str
    roles:         Optional[frozenset[str]]
    required_tool: Optional[str]
    render:        Callable[[list[dict]], str]
    gate:          Optional[Callable[[dict], bool]] = None


def _md(name: str) -> Callable[[list[dict]], str]:
    """Closure that ignores active_tools and returns a static .md file."""
    return lambda _tools: _prompt(name)


# Only the agent-actionable essentials go in every prompt. The full run/result/
# thread/finding directory layout (the bulk of conventions.md) is applied
# automatically by the materializer when entities are registered — it's not
# something the agent codes — so injecting all ~115 lines every turn was waste.
_CONVENTIONS_ESSENTIALS = (
    "### File conventions (essentials)\n"
    "- Generated files: snake_case + descriptive (`qc_n_genes_per_cell.png`, not `plot.png`); "
    "no spaces/parens; **no dates in names** (the entity's created_at carries the date).\n"
    "- Plots: 150 DPI (300 for publication); colorblind-safe palettes (viridis/cividis); "
    "label axes; save each plot to its own descriptively-named PNG.\n"
    "- The run/result/thread/finding directory layout is applied automatically when entities are "
    "registered — you don't build it by hand."
)


def _conventions(_tools: list[dict]) -> str:
    return _CONVENTIONS_ESSENTIALS


def _skills_index(_tools: list[dict]) -> str:
    """B2: per-turn skills catalog. Retrieval-gated: a large recipe library
    surfaces only the top-K skills relevant to this turn's intent plus a
    pointer to search_skills, so the prompt imprint stays bounded. The agent
    uses read_skill(name) to expand a body on demand. Empty until bio.skills
    register at import time (registry returns '', which the assembler drops)."""
    from core.skills import skills_index_block
    return skills_index_block(query=_INTENT.get(""))


def _memory_index(_tools: list[dict]) -> str:
    """B3: per-project memory index (typed files). Returns '' when no
    memories exist yet — so a fresh project shows no memory header."""
    from core.memory import memory_index_block
    return memory_index_block()


# ── recipe-uptake strategy ARMS (eval) ───────────────────────────────────────
# Selected by ABA_PROMPT_ARM. 'control' (default) = current behaviour, so the
# live server is unaffected. Each arm changes HOW the most-relevant recipe
# reaches the agent — the lever we're A/B-ing. See misc/scrna_test_findings.md.
_ARM_INJECT = {"inject_body", "inject_gotchas", "forced_triage",
               "decision_record", "relevance_rationale"}
_GOTCHA_HDR = re.compile(
    r"rule|caveat|pitfall|gotcha|\bnote\b|common|adjust|scope|honor|honour|"
    r"warning|important|plotting|threshold|orient", re.I)


def _current_arm() -> str:
    return (os.environ.get("ABA_PROMPT_ARM") or "control").strip() or "control"


def _split_sections(body: str) -> list[tuple[str, str]]:
    secs: list[tuple[str, str]] = []
    cur_h, cur = "", []
    for ln in body.splitlines():
        if re.match(r"^#{1,4}\s", ln):
            if cur_h or cur:
                secs.append((cur_h, "\n".join(cur).strip()))
            cur_h, cur = ln, []
        else:
            cur.append(ln)
    if cur_h or cur:
        secs.append((cur_h, "\n".join(cur).strip()))
    return secs


def _gotchas_card(s) -> str:
    """A compact 'what people get wrong + what done looks like' card distilled
    from a recipe — the caveat/rule/plotting sections + its `produces` list —
    without the full procedure. Tests whether the gotchas alone suffice."""
    out = [f"### Most-relevant recipe `{s.name}` — key rules + expected outputs",
           f"(Gotchas slice; `read_skill('{s.name}')` for the full procedure.)"]
    if getattr(s, "produces", None):
        out.append(f"- **Done = produce:** {', '.join(s.produces)}.")
    picked = [f"{h}\n{txt}" for h, txt in _split_sections(s.body or "")
              if h and _GOTCHA_HDR.search(h) and txt]
    card = "\n\n".join(picked)
    if not card:   # fallback: lead of the body
        card = "\n".join(l for l in (s.body or "").splitlines() if l.strip())
    out.append(card[:1600])
    return "\n".join(out)


def _recipe_arm_block(_tools: list[dict]) -> str:
    """Arm-specific injection appended after the skills index. Empty for control
    (and whenever there's no intent / no match)."""
    arm = _current_arm()
    if arm not in _ARM_INJECT:
        return ""
    intent = _INTENT.get("")
    if not intent:
        return ""
    try:
        from core.skills import search_skills
        hits = search_skills(intent, limit=3)
    except Exception:  # noqa: BLE001
        return ""
    if not hits:
        return ""
    top = hits[0]
    others = ", ".join(f"`{h.name}`" for h in hits[1:]) or "(none)"
    if arm == "inject_body":
        return (f"### Most-relevant recipe, injected IN FULL: `{top.name}`\n"
                f"Follow this procedure — it carries the correct API, parameters, and "
                f"gotchas; do not re-derive from memory. Other candidates: {others}.\n\n"
                f"{(top.body or '')[:4000]}")
    if arm == "inject_gotchas":
        return _gotchas_card(top)
    if arm == "forced_triage":
        names = ", ".join(f"`{h.name}`" for h in hits)
        return ("### REQUIRED before any run_python/run_r on a multi-step analysis\n"
                f"Your task matches these recipes: {names}. You MUST `read_skill` the "
                f"best-matching one (likely `{top.name}`) FIRST, then state in one line "
                "which recipe you'll follow (or why none fits), and only THEN write code. "
                "Coding a known library from memory is the top cause of wrong-API errors "
                "here — the recipe has the correct idioms and the expected outputs.")
    if arm == "decision_record":
        rows = "\n".join(f"| `{h.name}` |  |  |  |  |" for h in hits)
        return (
            "### REQUIRED FIRST STEP — Recipe triage (do this BEFORE planning or any "
            "run_python/run_r)\n"
            "You are in a customized environment whose recipes carry the correct tool "
            "choices, parameters, and known failure modes for THIS setup — do not design "
            "the pipeline from your pretrained priors. First `read_skill` the candidate(s) "
            "you might use, then emit this decision record (and nothing analysis-wise until "
            "you have):\n\n"
            "```\n## Recipe triage\n"
            "| recipe | use / partial / reject | why (grounded in THIS task) | section(s) I'll rely on | mismatch/risk |\n"
            "|---|---|---|---|---|\n"
            f"{rows}\n"
            "Primary: <recipe or 'none — using standard method X because …'>\n"
            "```\n"
            "Rules: (a) **'none fits' is a valid outcome** — say why and name the standard "
            "method you'll use instead; do NOT force-fit a recipe. (b) Recipes covering the "
            "SAME task are **alternatives, not complements** — pick one, don't blend competing "
            "pipelines. (c) You must **cite the specific section** of any recipe you use "
            "(you can't cite a step you didn't read). (d) Reject a high-ranked recipe only "
            "with an explicit reason.")
    if arm == "relevance_rationale":
        lines = ["### Candidate recipes — with why each MIGHT or might NOT fit this task",
                 "(Retrieval is topical; topical ≠ right-for-the-constraints. Use these to "
                 "SELECT — a lower-ranked recipe that matches your input/goal beats a higher-"
                 "ranked one that only shares vocabulary. Read the one you pick before coding.)"]
        for h in hits:
            fit = " ".join((getattr(h, "when_to_use", "") or h.description or "").split())[:240]
            avoid = " ".join((getattr(h, "avoid_when", "") or "").split())[:200]
            lines.append(f"- `{h.name}`")
            lines.append(f"    - may fit: {fit or '(see description)'}")
            lines.append(f"    - may NOT fit: {avoid or 'verify your data modality, input type, and goal match before using.'}")
        return "\n".join(lines)
    return ""


# Gate predicates over the turn context (see build_system's `ctx`).
def _has_focus_figure(c: dict) -> bool:
    return bool(c.get("focus_is_figure"))


def _highlight_relevant(c: dict) -> bool:
    # Relevant when the user highlighted THIS turn, or a figure is in focus (so
    # "here"/"this region" references resolve). Otherwise it's dead weight.
    return bool(c.get("highlight_active") or c.get("focus_is_figure"))


_BLOCKS: tuple[_Block, ...] = (
    _Block("identity",     None,                   None,             _md("identity.md")),
    _Block("capabilities", frozenset({"primary"}), None,             _capabilities_block),
    _Block("recipes",      frozenset({"primary"}), None,             _md("recipes.md")),
    # scenarios + highlighting only render when there's actually a figure to act
    # on (focus is a figure) or a fresh highlight — not on every turn.
    _Block("scenarios",    frozenset({"primary"}), "create_scenario", _md("scenarios.md"),
           gate=_has_focus_figure),
    _Block("behavior",     None,                   None,             _md("behavior.md")),
    _Block("highlighting", frozenset({"primary"}), None,             _md("highlighting.md"),
           gate=_highlight_relevant),
    _Block("conventions",  None,                   None,             _conventions),
    # Skills index — primary only, gated on read_skill so a deployment
    # that doesn't enable the tool also doesn't advertise the catalog.
    _Block("skills",       frozenset({"primary"}), "read_skill",     _skills_index),
    # Recipe-uptake strategy arm (eval). Empty for control → no effect live.
    _Block("recipe_arm",   frozenset({"primary"}), "read_skill",     _recipe_arm_block),
    # Memory index — primary only, gated on read_memory. Always rendered
    # for primary turns when the tool is on; the block itself returns ''
    # when no memories exist, which the assembler drops.
    _Block("memory",       frozenset({"primary"}), "read_memory",    _memory_index),
    # Data-first / observe-before-assume (PK #2-via-instruction). Salient END
    # position (like plan_first). Toggle off with ABA_DATA_SUMMARY=off (for eval
    # isolation); on by default live.
    _Block("data_orientation", frozenset({"primary"}), None, _md("data_orientation.md"),
           gate=lambda c: (os.environ.get("ABA_DATA_SUMMARY") or "on").lower() != "off"),
    _Block("plan_first",   frozenset({"primary"}), "present_plan",   _md("plan_first.md")),
)


def build_system(active_tools: list[dict], role: str = "primary", intent: str = "",
                 ctx: Optional[dict] = None) -> str:
    """Assemble a role-appropriate system prompt for this turn.

    role defaults to "primary" (the Guide). Advisor roles (e.g.
    "skeptic", "methodologist") get a trimmed prompt — no recipes,
    no scenarios, no plan_first, and no capabilities listing when
    they have no tools.

    `intent` (the user's message for this turn) feeds the retrieval-gated
    skills index so a large recipe library surfaces only the relevant slice.

    `ctx` carries per-turn signals for gated blocks — currently
    `focus_is_figure` and `highlight_active` — so blocks that only matter when
    the user is acting on a figure (scenarios, highlighting) aren't injected on
    every turn. Missing/empty ctx → those gated blocks simply don't render."""
    token = _INTENT.set(intent or "")
    ctx = ctx or {}
    try:
        names = {t["name"] for t in active_tools}
        parts: list[str] = []
        for blk in _BLOCKS:
            if blk.roles is not None and role not in blk.roles:
                continue
            if blk.required_tool is not None and blk.required_tool not in names:
                continue
            if blk.gate is not None and not blk.gate(ctx):
                continue
            text = blk.render(active_tools)
            if text:
                parts.append(text)
        return "\n\n".join(parts)
    finally:
        _INTENT.reset(token)
