"""Phase 2 — Skill dispatch mechanics, no recipe content.

The user explicitly names a skill in the prompt. The model only has
to wrap it in `Skill(skill="…")`. No discovery via search_skills, no
expectation that the model uses the recipe body — that's P3+.

What we test for:
  - the model emits `Skill(skill="<name>")` as the first move
  - it does NOT dispatch the bare name as a tool (the prj_2c015847
    bug shape)
  - extra args (like `args="..."`) appear only when the user gave
    them in the prompt

Failure modes worth distinguishing in the report:
  A) Model called `<name>(...)` directly → catalog confusion. The
     runtime auto-rewrite intervention is the right fix.
  B) Model called Skill with wrong skill name → didn't read the
     prompt carefully (paraphrased / misspelled).
  C) Model added extra args the user didn't ask for → over-helpful
     pattern from P1.
"""
from __future__ import annotations

from tests.scenarios import Scenario, Assertion


# ── helpers ─────────────────────────────────────────────────────────


def _first_tool_is_Skill(calls):
    if not calls:
        return False, "no tools were called"
    n = calls[0][0]
    return (n == "Skill",
            f"first tool was {n!r}, expected 'Skill'")


def _skill_arg_is(expected_name: str):
    def _p(calls):
        if not calls:
            return False, "no tools"
        if calls[0][0] != "Skill":
            return False, f"first tool was {calls[0][0]!r}, can't check skill arg"
        sk = (calls[0][1].get("skill") or "").strip()
        return (sk == expected_name,
                f"skill arg was {sk!r}, expected {expected_name!r}")
    return _p


def _did_NOT_dispatch_name_as_bare_tool(skill_name: str):
    """The headline regression: skill name appears AS a tool name in
    the call list. If this fails, runtime auto-rewrite is the next
    intervention."""
    def _p(calls):
        bad = [n for n, _ in calls if n == skill_name]
        return ((not bad),
                f"dispatched {skill_name!r} as a bare tool — should "
                "have wrapped in Skill(skill=…)")
    return _p


def _args_arg_contains(substr: str):
    def _p(calls):
        if not calls or calls[0][0] != "Skill":
            return False, "no Skill call to check"
        a = str(calls[0][1].get("args") or "")
        return (substr.lower() in a.lower(),
                f"args={a[:80]!r} doesn't contain {substr!r}")
    return _p


def _no_extra_skill_args(calls):
    """Only `skill` and (optionally) `args` should be set. The model
    shouldn't hallucinate domain/category/limit/etc."""
    if not calls or calls[0][0] != "Skill":
        return False, "no Skill call to check"
    allowed = {"skill", "args"}
    extras = [k for k, v in calls[0][1].items()
              if k not in allowed and v not in (None, "", [], {})]
    return ((not extras),
            f"Skill got unexpected non-empty args: {extras}")


# ── scenarios ──────────────────────────────────────────────────────


P2_SCENARIOS: list[Scenario] = [
    # 1. The most-explicit hand-holding: user says "use Skill tool".
    Scenario(
        name="p2_explicit_skill_tool_inspect_upload",
        user_prompt=("use the Skill tool to load the recipe named "
                     "'inspect-upload'"),
        assertions=[
            Assertion("first_tool_is_Skill", _first_tool_is_Skill),
            Assertion("skill_arg_is_inspect-upload",
                      _skill_arg_is("inspect-upload")),
            Assertion("did_not_dispatch_name_as_tool",
                      _did_NOT_dispatch_name_as_bare_tool("inspect-upload")),
            Assertion("no_extra_args", _no_extra_skill_args),
        ],
        max_turns=2,
        stop_after_n_tools=1,
    ),
    # 2. "Load the X recipe" — slightly less explicit than #1.
    Scenario(
        name="p2_load_recipe_manage_entities",
        user_prompt="load the 'manage-entities' recipe",
        assertions=[
            Assertion("first_tool_is_Skill", _first_tool_is_Skill),
            Assertion("skill_arg_is_manage-entities",
                      _skill_arg_is("manage-entities")),
            Assertion("did_not_dispatch_name_as_tool",
                      _did_NOT_dispatch_name_as_bare_tool("manage-entities")),
        ],
        max_turns=2,
        stop_after_n_tools=1,
    ),
    # 3. "Show me the X recipe" — natural-language framing.
    Scenario(
        name="p2_show_me_recipe_summarize_thread",
        user_prompt="show me the 'summarize-thread' recipe",
        assertions=[
            Assertion("first_tool_is_Skill", _first_tool_is_Skill),
            Assertion("skill_arg_is_summarize-thread",
                      _skill_arg_is("summarize-thread")),
            Assertion("did_not_dispatch_name_as_tool",
                      _did_NOT_dispatch_name_as_bare_tool("summarize-thread")),
        ],
        max_turns=2,
        stop_after_n_tools=1,
    ),
    # 4. With args: tests that the model passes args= when given.
    Scenario(
        name="p2_skill_with_args_compare_branches",
        user_prompt=("use the 'compare-branches' skill with args "
                     "'baseline vs scenario_a'"),
        assertions=[
            Assertion("first_tool_is_Skill", _first_tool_is_Skill),
            Assertion("skill_arg_is_compare-branches",
                      _skill_arg_is("compare-branches")),
            Assertion("args_arg_contains_baseline",
                      _args_arg_contains("baseline")),
            Assertion("did_not_dispatch_name_as_tool",
                      _did_NOT_dispatch_name_as_bare_tool("compare-branches")),
        ],
        max_turns=2,
        stop_after_n_tools=1,
    ),
    # 5. "What does X say" — less imperative phrasing.
    Scenario(
        name="p2_what_does_branch_from_figure_say",
        user_prompt="what does the 'branch-from-figure' recipe say?",
        assertions=[
            Assertion("first_tool_is_Skill", _first_tool_is_Skill),
            Assertion("skill_arg_is_branch-from-figure",
                      _skill_arg_is("branch-from-figure")),
            Assertion("did_not_dispatch_name_as_tool",
                      _did_NOT_dispatch_name_as_bare_tool("branch-from-figure")),
        ],
        max_turns=2,
        stop_after_n_tools=1,
    ),
    # 6. Pasted from a (fake) search result — the post-search dispatch
    #    pattern the prompt-structure fix targets.
    Scenario(
        name="p2_after_search_dispatch_approach_unfamiliar_tool",
        user_prompt=("I just ran search_skills and the top result was "
                     "'approach-unfamiliar-tool'. Load it."),
        assertions=[
            Assertion("first_tool_is_Skill", _first_tool_is_Skill),
            Assertion("skill_arg_is_approach-unfamiliar-tool",
                      _skill_arg_is("approach-unfamiliar-tool")),
            Assertion("did_not_dispatch_name_as_tool",
                      _did_NOT_dispatch_name_as_bare_tool(
                          "approach-unfamiliar-tool")),
        ],
        max_turns=2,
        stop_after_n_tools=1,
    ),
    # 7. Mixed case: a real recipe name that doesn't appear in the
    #    Core skills section (so the model can't copy-paste from
    #    the system prompt). Tests that the dispatch pattern
    #    generalizes beyond the in-prompt list.
    Scenario(
        name="p2_dispatch_non_core_recipe_register_artifact",
        user_prompt=("load the 'register-artifact' recipe so I can read "
                     "what it does"),
        assertions=[
            Assertion("first_tool_is_Skill", _first_tool_is_Skill),
            Assertion("skill_arg_is_register-artifact",
                      _skill_arg_is("register-artifact")),
            Assertion("did_not_dispatch_name_as_tool",
                      _did_NOT_dispatch_name_as_bare_tool("register-artifact")),
        ],
        max_turns=2,
        stop_after_n_tools=1,
    ),
]
