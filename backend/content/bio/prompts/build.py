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

from core import config

_HERE = Path(__file__).parent
# The system bundle is the real home for system policy/rules now (no symlinks).
# build.py sources them via the bundle; this is the on-disk fallback location.
_SYS_BUNDLE = _HERE.parents[2] / "system_bundle"   # backend/system_bundle

# The turn's intent (the user's message, usually) — set by build_system for
# the duration of one assembly so retrieval-gated blocks (the skills index)
# can rank against it without changing every block's render signature.
# A ContextVar (not a plain global) keeps concurrent assemblies — Guide turn
# vs. advisor/sub-agent — from clobbering each other's intent.
_INTENT: contextvars.ContextVar[str] = contextvars.ContextVar("aba_prompt_intent", default="")
# Per-assembly thread_id, so blocks can pull thread-scoped state (e.g. recipes the
# agent declared on `present_plan.steps[].skill` in earlier turns) without changing
# every block's render signature.
_THREAD_ID: contextvars.ContextVar[str] = contextvars.ContextVar("aba_prompt_thread_id", default="")
# Per-assembly "mode": "full" (default) reproduces today's prompt exactly;
# "lean" drops a hard-coded subset of heavy/dynamic blocks and forces
# behavior_slim.md. Set by build_system() at entry, consulted by blocks
# whose membership or render depends on the mode (see _LEAN_DROP and
# _behavior_block). Plumbed via a ContextVar (not a parameter) so we
# don't have to extend every _Block.render(tools) signature.
_MODE: contextvars.ContextVar[str] = contextvars.ContextVar("aba_prompt_mode", default="full")
# Block names dropped when mode == "lean". Justification:
#   skills_recipes   — BM25 catalog of 255 recipes; the only dynamic
#                      block. The agent can fetch a recipe on demand
#                      via Skill(skill=...).
#   recipe_arm       — eval-only, empty for control.
#   highlighting     — gated to figure-focus turns only; cheap to
#                      drop entirely when the lean agent rarely takes
#                      highlight actions.
#   data_orientation — env-knob already exists; lean folds it in.
#
# DO NOT add `declared_recipes` here. That block pins the "Most-
# relevant recipe — key rules + expected outputs" card per skill the
# agent declared on present_plan, surviving Tier-2 summarization.
# Without it, when Tier-2 fires after the agent has fetched the
# recipe body via Skill, the recipe's thresholds/gotchas/done-
# criteria fold into "Invoked skill X" and the agent drifts. Observed
# in prj_1141348f 2026-06-19: 38,894-char recipe body summarized to
# "Invoked fetch-geo-…" with no rules retained → agent kept the step
# titles but lost the parameter choices the recipe specified.
_LEAN_DROP = frozenset({"skills_recipes", "recipe_arm",
                        "highlighting", "data_orientation"})

# Small-model variant ("lean_small" mode): on top of _LEAN_DROP, also
# drop blocks empirically shown to interfere with — or merely dilute —
# small-model tool-dispatch discipline. Established by the H8 block
# ablation against Qwen3-30B-A3B 2026-06-20:
#   - skills_core:   declares recipes by name → contradicts the
#                    "search_skills FIRST" discovery directive
#                    (split-brain on which path to take).
#   - conventions:   file-naming + plot-DPI guidance, unrelated to
#                    tool use, but its presence still moved P3 from
#                    5/7 → 7/7 when ablated — classic softmax
#                    attention-dilution signature.
# Anthropic / regular spec is unaffected; this only fires for specs
# that opt in via `prompt_mode: lean_small`.
_LEAN_SMALL_DROP = _LEAN_DROP | frozenset({"skills_core", "conventions"})


@lru_cache(maxsize=None)
def _prompt(name: str) -> str:
    """Read a prompt .md file once and cache. Stripped of trailing whitespace
    so concatenations don't double-blank."""
    return (_HERE / name).read_text().rstrip()


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
    body = "\n".join(lines)
    # nonneg eval arm: drop the "Libraries available in the run_python sandbox" list.
    # It names scanpy/pandas/… as ready-to-use, which reads as "you already have it,
    # just code it" and competes with reading the analysis recipe (PK hypothesis on the
    # scanpy recipe-uptake gap). The sandbox stack is still noted in behavior_slim, so
    # the agent won't redundantly ensure_capability it.
    if _is_nonneg():
        return body
    return body + "\n\n" + _bundle_rule_text("sandbox_libs.md")


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
    # CC-convergence Phase 4 (cache split): blocks marked dynamic=True render
    # INTO THE SECOND SYSTEM CACHE BLOCK — uncached. Everything else is in the
    # cached prefix. The only block that genuinely varies per turn at intent
    # granularity is the BM25 recipes slice, so we split there. Stable prefix
    # caches across the whole session; dynamic tail is small (~3-4K) and cheap
    # to recompute. See open_stream in core/llm.py for the wire-format split.
    dynamic:       bool = False


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


def _skills_index_core(_tools: list[dict]) -> str:
    """Core skills slice — always-on operating + strategy skills. Stable across
    turns (the registered Core set doesn't change at user-message granularity),
    so this lives in the cached system prefix."""
    from core.skills import skills_index_block
    return skills_index_block(query=_INTENT.get(""), tier="core")


def _skills_index_recipes(_tools: list[dict]) -> str:
    """Recipes slice — BM25-ranked top-K relevant to this turn's intent. The
    only genuinely per-turn-dynamic system content; lives in the uncached tail
    block so per-intent catalog changes don't bust the system-prefix cache."""
    from core.skills import skills_index_block
    return skills_index_block(query=_INTENT.get(""), tier="recipes")


def build_recipes_reminder(intent: str = "", ctx: Optional[dict] = None) -> str:
    """Per-turn recipes catalog as a Claude Code system-reminder. Splice this
    into the LATEST user message at LLM-call time. Returns '' when the recipes
    tier is empty (registry has no `visibility='local'` skills)."""
    from core.skills import recipes_reminder_block
    token = _INTENT.set(intent or "")
    try:
        return recipes_reminder_block(query=intent)
    finally:
        _INTENT.reset(token)


def _memory_index(_tools: list[dict]) -> str:
    """B3: per-project memory index (typed files). Returns '' when no
    memories exist yet — so a fresh project shows no memory header."""
    from core.memory import memory_index_block
    return memory_index_block()


def _bundle_overlay(_tools: list[dict]) -> str:
    """Non-system policy text from the EffectiveBundle — i.e. institution,
    lab, user AGENTS.md content composed via the bundle layering algorithm.

    System-scope policy reaches the prompt through the existing `identity`
    block (which reads identity.md directly), so we exclude it here to
    avoid duplication. When no non-system scopes are present (Mac default),
    this returns empty string and contributes nothing → output stays
    byte-identical with pre-bundle behavior.

    Failures in bundle resolution must not break prompt assembly — fall
    back to empty string and log.
    """
    try:
        from core.bundle.active import get_bundle
        return get_bundle().policy_text_excluding({"system"})
    except Exception as e:                             # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "bundle_overlay: bundle resolution failed (%s); "
            "skipping non-system policy injection", e)
        return ""


# ── system policy + rules now come FROM the bundle (one loader) ───────────────
# build.py used to read content/bio/prompts/<name>.md directly for the system
# scope and never injected the bundle's composed rules — so an institution/lab
# override of a rule (e.g. figures.md) composed in the bundle but never reached
# the prompt. These helpers source each named block's content from the bundle
# (overrideable → narrowest-scope winner, required → additive), falling back to
# the on-disk file if bundle resolution fails. For the system-only case the
# composed content is byte-identical to the old direct read (verified).
def _bundle_rule_text(name: str) -> str:
    try:
        from core.bundle.active import get_bundle
        c = get_bundle().rule_content(name)
        if c is not None:
            return c.rstrip()
    except Exception as e:                             # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            "bundle rule %s failed (%s); using system bundle copy", name, e)
    for sub in ("rules", "rules/required"):            # fallback: the real file
        p = _SYS_BUNDLE / sub / name
        if p.is_file():
            return p.read_text().rstrip()
    return ""


def _rule(name: str) -> Callable[[list[dict]], str]:
    """Like _md, but sources `name` from the bundle's composed rules so a
    lab/institution override of that rule reaches the prompt."""
    return lambda _tools: _bundle_rule_text(name)


def _system_policy_block(_tools: list[dict]) -> str:
    """The identity block — system-scope AGENTS.md, from the bundle."""
    try:
        from core.bundle.active import get_bundle
        c = get_bundle().system_policy()
        if c:
            return c.rstrip()
    except Exception:                                  # noqa: BLE001
        pass
    p = _SYS_BUNDLE / "AGENTS.md"
    return p.read_text().rstrip() if p.is_file() else ""


# Rule filenames already injected by a NAMED block (with their own position +
# gates). The bundle_rules catch-all skips these so it only carries NEW rules an
# institution/lab/user added under a fresh filename.
_NAMED_RULES = frozenset({
    "figures.md", "data_orientation.md", "highlighting.md", "recipes.md",
    "plan_first.md", "nonnegotiables.md", "behavior.md", "behavior_slim.md",
    "sandbox_libs.md", "scenarios.md", "identity.md", "promotion.md",
})


def _bundle_extra_rules(_tools: list[dict]) -> str:
    """Institution/lab/user rules NOT already covered by a named block — i.e.
    rule files a scope added under a new filename. System rules are excluded so
    the system-only prompt is unchanged."""
    try:
        from core.bundle.active import get_bundle
        parts = [r.content.rstrip()
                 for r in get_bundle().rules_excluding({"system"})
                 if r.filename not in _NAMED_RULES and r.content.strip()]
        return "\n\n".join(parts)
    except Exception:                                  # noqa: BLE001
        return ""


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
    return config.settings.prompt_arm.get().strip() or "control"


# ── prompt-STRUCTURE arm: 'nonneg' ───────────────────────────────────────────
# Tests the invariant-vs-dilution hypothesis (the 5 integrity invariants are
# already hard-phrased but drowned in a 20-bullet bold wall). 'nonneg' hoists them
# into an isolated, salient "Ground rules" block at the top (after identity) and
# renders a de-bolded, slimmed behavior block. control = current behavior.md.
def _is_nonneg() -> bool:
    return _current_arm() == "nonneg"


_H5_DISCOVERY_DIRECTIVE = """\
## Recipe discovery + execution flow

When the user's request matches a possible recipe (data fetch, QC,
clustering, integration, DE, PDF extract, primer design, …):

  1. `search_skills(query="<short intent phrase>")` — FIRST.
  2. Look at the returned `skills[*]` list. Pick the most relevant.
  3. `Skill(skill="<that name>", args="<args if any>")` —
     IMMEDIATELY after, same turn. This LOADS the recipe body
     into your history; the recipe is now your reference.
  4. `run_python` (or `run_r`) with the code from the recipe body —
     this is where the work actually happens. You only call `Skill`
     ONCE per task; do not re-load the same recipe.

Do NOT skip step 1 and try to remember a recipe name yourself.
Do NOT stop after step 1 to narrate what you found.
Do NOT switch to `run_python` before reading the recipe body (step 3).

### When a `run_python` / `run_r` background job FAILED

You will see a `[continuation: background job ... FAILED]` user
message with a traceback. The recipe is still loaded in your
history. Your next action is DEBUGGING, not re-discovery:

  - Read the traceback. Identify the line and error type
    (NameError, ImportError, KeyError, ValueError, …).
  - Fix the offending code — add the missing import, correct the
    argument, rename the variable. Stdlib imports (`re`, `os`,
    `json`, `subprocess`, `time`, …) are very common slip-ups;
    if the traceback says `name 'X' is not defined`, the fix is
    usually `import X` at the top of your next `run_python`.
  - Submit the corrected code via a new `run_python` call.

Do NOT call `search_skills` or `Skill` again on a job failure —
the failure is a code bug, not a wrong-recipe problem. Restarting
discovery is the most common cycling failure mode and produces no
new information.

Examples (the SHAPE is the rule, not the specific names):

  User: "help me fetch matrices for GSE192391"
    → search_skills(query="fetch GEO data")
    → Skill(skill="fetch-geo-processed-matrices", args="GSE192391")
    → run_python(code=<recipe code, adapted to GSE192391>)

  Continuation: "[... FAILED] Error: NameError: name 're' is not defined"
    → run_python(code=<same code, with `import re` added at top>)

  User: "design PCR primers"
    → search_skills(query="primer design")
    → Skill(skill="design-primer")
    → run_python(code=<recipe code>)
"""


def _behavior_block(_tools: list[dict]) -> str:
    # Lean mode forces the slim variant regardless of arm. The `mode`
    # signal is plumbed via a ContextVar so we don't have to thread it
    # through every block's render(tools) signature.
    current_mode = _MODE.get("full")
    if current_mode in ("lean", "lean_small") or _is_nonneg():
        body = _bundle_rule_text("behavior_slim.md")  # bundle-sourced so lab/institution can override lean behavior too
    else:
        body = _bundle_rule_text("behavior.md")   # full behavior, bundle-sourced
    return body


def _discovery_directive_block(_tools: list[dict]) -> str:
    """Top-of-prompt anchor for the search_skills → Skill flow.

    Promoted out of `_behavior_block` 2026-06-20 to ride the "critical
    info at the start or end" principle from the 'lost in the middle'
    long-context literature. Within `_BLOCKS` we position this right
    after identity/bundle_overlay so it sits at position 2-3 in the
    assembled system prompt — as early as we can get it without
    displacing the model's role framing."""
    # The discovery directive is driven by mode (lean_small small-model lane); the
    # former ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE manual override was never set in
    # production and only duplicated this trigger — resolved away (env_reorg §6).
    current_mode = _MODE.get("full")
    if current_mode == "lean_small":
        return _H5_DISCOVERY_DIRECTIVE
    return ""


def build_discovery_reminder(mode: str, user_text: str = "") -> str:
    """Per-turn discovery reminder — a tight one-paragraph anchor that
    rides next to the user's latest message via splice_recipes_reminder.

    Returns "" except in `lean_small` mode. Leverages Qwen3's recency
    bias: the model is documented to follow the most recent instruction
    in multi-turn conversations, so a short directive adjacent to the
    user message has high effective attention weight even when the
    system prompt is long. Pairs with the system-prompt-level
    `discovery_directive` block — same content, different positions.

    Suppressed on background-job-failed continuations (prj_3aa75c1f
    2026-06-20): those arrive as `role:"user"` text starting with
    `[continuation:` and aren't fresh discovery turns — they're
    error-recovery turns where the right next action is debugging
    the failed code, NOT restarting from search_skills.
    """
    if mode != "lean_small":
        return ""
    if user_text.lstrip().startswith("[continuation:"):
        return ""
    return (
        "<system-reminder>\n"
        "Discovery flow this turn: if the user's request could match "
        "a recipe (data fetch, QC, clustering, integration, DE, PDF "
        "extract, primer design, …), your FIRST tool call is "
        "`search_skills(query=\"<intent>\")`, your SECOND is "
        "`Skill(skill=\"<name from the search result>\", args=\"…\")`, "
        "and your THIRD is `run_python` (or `run_r`) with the code "
        "from the recipe body. Do not call `Skill` twice for the same "
        "task.\n"
        "</system-reminder>"
    )


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
           f"(Gotchas slice; `Skill(skill=\"{s.name}\")` for the full procedure.)"]
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
                f"Your task matches these recipes: {names}. You MUST invoke `Skill(skill=...)` "
                f"on the best-matching one (likely `{top.name}`) FIRST, then state in one line "
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
            "the pipeline from your pretrained priors. First invoke `Skill(skill=…)` on the "
            "candidate(s) you might use, then emit this decision record (and nothing "
            "analysis-wise until you have):\n\n"
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
def _highlight_relevant(c: dict) -> bool:
    # Relevant when the user highlighted THIS turn, or a figure is in focus (so
    # "here"/"this region" references resolve). Otherwise it's dead weight.
    return bool(c.get("highlight_active") or c.get("focus_is_figure"))


def _has_declared_recipes(c: dict) -> bool:
    """Render the declared-recipes block only when the agent has named >=1 recipe
    on a present_plan step.skill field in this thread.

    Cache-miss recovery: if `_THREAD_DECLARED_RECIPES` is empty for this
    thread (server bounce wiped the in-process cache, or another
    advisor process is asking), rehydrate from the durable plan entity.
    Without this, every restart silently drops the recipe-rules card
    that anchors a long thread to the recipe's gotchas (prj_1141348f
    2026-06-19)."""
    tid = str(c.get("thread_id") or "")
    if not tid: return False
    try:
        from content.bio.tools import (_THREAD_DECLARED_RECIPES,
                                        rehydrate_declared_recipes)
        if _THREAD_DECLARED_RECIPES.get(tid):
            return True
        return bool(rehydrate_declared_recipes(tid))
    except Exception:
        return False


def _declared_recipes_block(active_tools: list[dict]) -> str:
    """Inject the BODIES of recipes the agent declared on its plan's step.skill
    fields, each LABELED with the specific plan step(s) that declared it
    (#324 Phase 3 — step-labeled). The label says e.g. "for step 4 (DESeq2…)
    — use `deseq2-r`" so the agent can't leak APIs across steps. Agent-driven:
    if the agent didn't bind a recipe to a step, none is pushed.

    LEAN MODE: dump the GOTCHAS card per recipe (~1.6k chars each) instead
    of the full body (~30-40k each). The full-mode dump explodes the system
    budget for any recipe-bound thread; in lean we keep just the
    rules/caveats/produces-list anchor so the agent stays on-recipe through
    Tier-2 collapses without paying for the full procedure twice. The full
    procedure is still fetch-able via `Skill(skill="…")`."""
    tid = _THREAD_ID.get() or ""
    if not tid: return ""
    try:
        from content.bio.tools import _THREAD_DECLARED_RECIPES, rehydrate_declared_recipes
        from core.skills import get_skill
    except Exception:
        return ""
    bindings = _THREAD_DECLARED_RECIPES.get(tid) or rehydrate_declared_recipes(tid)
    if not bindings: return ""
    lean_mode = _MODE.get("full") in ("lean", "lean_small")
    # Group steps by recipe so each recipe body appears once, listing all
    # bound steps. Preserve first-mention order across the plan.
    by_recipe: dict[str, list[tuple[int, str]]] = {}
    order: list[str] = []
    for step_i, title, rn in bindings:
        if rn not in by_recipe:
            by_recipe[rn] = []; order.append(rn)
        by_recipe[rn].append((step_i, title))
    parts: list[str] = ["## Recipes you declared in your plan — bound to specific steps"]
    for rn in order:
        spec = get_skill(rn)
        if not spec: continue
        body = getattr(spec, "body", None)
        slots = by_recipe[rn]
        labels = ", ".join(f"step {i}" + (f" ({t[:60]})" if t else "") for i, t in slots)
        if lean_mode:
            # GOTCHAS slice only — preserves the recipe's rules /
            # caveats / "Done = produce" anchor without the full
            # procedure body.
            card = _gotchas_card(spec)
            if not card: continue
            parts.append(
                f"\n### `{rn}` — declared for: {labels}\n"
                f"Apply this recipe's rules + done-criteria for the listed "
                f"step(s); fetch the full procedure with "
                f"`Skill(skill=\"{rn}\")` if you need API specifics.\n\n{card}")
        else:
            if not body: continue
            parts.append(
                f"\n### `{rn}` — declared for: {labels}\n"
                f"Apply this recipe's APIs and ordering ONLY for the listed step(s); "
                f"other steps may use a different recipe or none.\n\n{body}")
    if len(parts) == 1: return ""   # nothing resolved → don't emit the header
    parts.append("\nIf a step actually needs a different recipe than you bound to it, "
                 "present a revised plan rather than coding around the binding.")
    return "\n".join(parts)


_BLOCKS: tuple[_Block, ...] = (
    _Block("identity",     None,                   None,             _system_policy_block),
    # Institution / lab / user policy layered on top of the system identity.
    # Renders empty when no non-system scopes are present (Mac default), so
    # the live prompt is byte-identical with pre-bundle behavior until a
    # site.yaml / ABA_*_BUNDLE env var puts a bundle in front of the loader.
    _Block("bundle_overlay", None, None, _bundle_overlay),
    # Institution/lab/user RULES added under a NEW filename (overrides of the
    # named rule blocks below already flow through those blocks via the bundle).
    # Empty for the system-only default → byte-identical.
    _Block("bundle_rules", None, None, _bundle_extra_rules),
    # Top-of-prompt anchor for the discovery flow (lean_small / Qwen3-class).
    # Empty for every other mode. Renders right after identity to ride the
    # "critical info at the start" finding from the long-context literature.
    _Block("discovery_directive", frozenset({"primary"}), None,
           _discovery_directive_block),
    # Non-negotiables (integrity invariants) — 'nonneg' arm only; isolated + salient
    # at the top so they don't compete with the operational wall. control = no-op.
    _Block("nonnegotiables", frozenset({"primary"}), None, _rule("nonnegotiables.md"),
           gate=lambda c: _is_nonneg()),
    _Block("capabilities", frozenset({"primary"}), None,             _capabilities_block),
    _Block("recipes",      frozenset({"primary"}), None,             _rule("recipes.md")),
    # The "scenarios" prompt block (gated on the legacy create_scenario tool)
    # was removed 2026-06-06 — variant-figure flow is now the revisions
    # cluster (Stage 5 of misc/exec_records_and_versioning.md). The
    # `make_revision` tool's docstring carries the same guidance ("only when
    # the user EXPLICITLY asks for a variant"), so no replacement block is
    # needed. Highlighting still renders on focused-figure turns below.
    _Block("behavior",     None,                   None,             _behavior_block),
    _Block("promotion",    frozenset({"primary"}), None,             _rule("promotion.md")),
    # dynamic=True is a CACHING requirement, not a style choice: the gate is
    # per-TURN state (focus/highlight), so rendering this into the stable block
    # would change the cached system prefix on every focus flip and re-bill the
    # whole conversation (the sidebar bug class — see core.llm.place_volatile_tail).
    # Per-turn-gated blocks must ride the dynamic tail; only deployment-constant
    # gates (config, tool set) may sit in stable.
    _Block("highlighting", frozenset({"primary"}), None,             _rule("highlighting.md"),
           dynamic=True, gate=_highlight_relevant),
    _Block("conventions",  None,                   None,             _conventions),
    # Figure-style directive — clean layout, one panel by default, ggplot2 in R,
    # alpha-blending on dense scatters. Primary only (advisors don't draw figures).
    _Block("figures",      frozenset({"primary"}), None,             _rule("figures.md")),
    # Skills index, Core tier — always-on, stable. Cached with the rest of the
    # system prefix. Renders the leading "### Skills you can reference..." prose
    # + the Core skills bullets.
    _Block("skills_core",  frozenset({"primary"}), "Skill",          _skills_index_core),
    # Recipe-uptake strategy arm (eval). Empty for control → no effect live.
    _Block("recipe_arm",   frozenset({"primary"}), "Skill",          _recipe_arm_block),
    # Memory index — primary only, gated on read_memory. Always rendered
    # for primary turns when the tool is on; the block itself returns ''
    # when no memories exist, which the assembler drops.
    _Block("memory",       frozenset({"primary"}), "read_memory",    _memory_index),
    # Data-first / observe-before-assume (PK #2-via-instruction). Salient END
    # position (like plan_first). Toggle off with ABA_DATA_SUMMARY=off (for eval
    # isolation); on by default live.
    _Block("data_orientation", frozenset({"primary"}), None, _rule("data_orientation.md"),
           gate=lambda c: config.settings.data_summary.get().lower() != "off"),
    _Block("plan_first",   frozenset({"primary"}), "present_plan",   _rule("plan_first.md")),
    # Agent-declared recipes pinned for the rest of the thread (#324 Phase 2).
    # Renders only when the agent has populated >=1 step.skill on present_plan.
    _Block("declared_recipes", frozenset({"primary"}), "present_plan",
           _declared_recipes_block, gate=_has_declared_recipes),
    # Recipes catalog — BM25-ranked, varies per intent. Marked dynamic=True so
    # build_system emits it as the SECOND (uncached) system block. Everything
    # above is the stable, cached prefix.
    _Block("skills_recipes", frozenset({"primary"}), "Skill",
           _skills_index_recipes, dynamic=True),
)


def build_system(active_tools: list[dict], role: str = "primary", intent: str = "",
                 ctx: Optional[dict] = None,
                 mode: str = "full") -> tuple[str, str]:
    """Assemble a role-appropriate system prompt as TWO cache blocks
    (CC-convergence Phase 4 cache split):

      • (stable, dynamic)
      • stable: identity, behavior, capabilities, plan rules, tool descriptions,
        Core skills, declared-recipes, etc. Stable across turns within a session →
        caches with `cache_control: ephemeral` on the API side.
      • dynamic: the BM25 recipes catalog only. Varies per intent (the relevant
        top-K can shift turn-to-turn). NOT cached.

    role defaults to "primary" (the Guide). Advisor roles get a trimmed prompt.

    `intent` feeds the retrieval-gated skills index. `ctx` carries per-turn
    signals for gated blocks. Missing/empty ctx → gated blocks just don't render.

    Backward-compat: legacy callers that did `system = build_system(...)` (one
    string) now get a tuple; either join it with "\\n\\n" or pass both to the
    transport layer as a 2-block system."""
    if mode not in ("full", "standard", "lean", "lean_small"):
        raise ValueError(f"build_system: mode={mode!r} must be 'full', "
                         "'standard', 'lean', or 'lean_small'")
    token     = _INTENT.set(intent or "")
    ctx       = ctx or {}
    tid_token = _THREAD_ID.set(str(ctx.get("thread_id") or ""))
    mode_tok  = _MODE.set(mode)
    try:
        names = {t["name"] for t in active_tools}
        # Env-gated block ablation (kept for ad-hoc experiments).
        # Comma-separated block names to drop on top of the mode's
        # built-in drops.
        _ablate = set(config.settings.experimental_ablate_blocks.get())
        # Pick the mode-specific drop set.
        #   - lean_small: lean's drops + skills_core + conventions
        #   - lean:       static drops only (heavy/dynamic blocks)
        #   - standard:   no drops; behavior.md (full); compact catalog
        #                 (the catalog-prefix policy is decided in guide.py)
        #   - full:       no drops; behavior.md; non-compact catalog
        if mode == "lean_small":
            _mode_drops = _LEAN_SMALL_DROP
        elif mode == "lean":
            _mode_drops = _LEAN_DROP
        else:
            _mode_drops = frozenset()
        stable_parts: list[str] = []
        dynamic_parts: list[str] = []
        for blk in _BLOCKS:
            if blk.name in _mode_drops:
                continue
            if blk.name in _ablate:
                continue
            if blk.roles is not None and role not in blk.roles:
                continue
            if blk.required_tool is not None and blk.required_tool not in names:
                continue
            if blk.gate is not None and not blk.gate(ctx):
                continue
            text = blk.render(active_tools)
            if not text:
                continue
            (dynamic_parts if blk.dynamic else stable_parts).append(text)
        return "\n\n".join(stable_parts), "\n\n".join(dynamic_parts)
    finally:
        _INTENT.reset(token)
        _THREAD_ID.reset(tid_token)
        _MODE.reset(mode_tok)
