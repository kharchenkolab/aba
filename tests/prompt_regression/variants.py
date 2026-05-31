"""Named system-prompt variants for A/B + ablation.

A variant transforms the rendered system prompt:
  arm     -> ABA_PROMPT_ARM
  sys_sub -> [(old,new)] string swaps applied after render
  ablate  -> [block names] dropped from build._BLOCKS

The plan_first swaps below reproduce the 2026-05-30 finding (numbered list beats
both the old 'examine' wording and the run-on 'read-first' wording).
"""
# current live numbered wording (the anchor present in the rendered prompt)
_NUMBERED = ("you MUST do these IN ORDER and then STOP: (1) `read_skill` the recipe for the analysis "
             "method you'll run (use `search_skills` if you're unsure which), separately from any "
             "data-loading recipe you already read, and base your plan on it; (2) call `present_plan` "
             "with a short ordered list of the steps; (3) STOP and wait for the user's Go.")
_OLD = ("you MUST prepare, examine potentially relevant recipes and tools, and call present_plan FIRST "
        "with a short ordered list of the steps, then STOP.")
_NEW = ("you MUST FIRST `read_skill` the recipe for the analysis method you'll run (use `search_skills` "
        "if you're unsure which), separately from any data-loading recipe you already read, and base your "
        "plan on it. THEN call present_plan with a short ordered list of the steps, and STOP. Do not run "
        "any of those steps in the same turn.")

VARIANTS = {
    "current":        {},                                            # live numbered list (nonneg)
    "planfirst_old":  {"sys_sub": [(_NUMBERED, _OLD)]},              # 'examine' wording
    "planfirst_new":  {"sys_sub": [(_NUMBERED, _NEW)]},              # run-on read-first
    "control_arm":    {"arm": "control"},                            # the un-restructured prompt
    "ablate_recipes": {"ablate": ["recipes"]},                       # drop recipes.md value-case
    "canonical":      {"arm": "control"},                            # alias: un-restructured/default live prompt (behavior.md, sandbox libs, no nonnegotiables)
    "nonneg":         {"arm": "nonneg"},                             # restructured eval arm (behavior_slim.md + isolated nonnegotiables, no sandbox-libs list)

    # ── Recipe-following intervention variants (Phase 1 of #324) ──────────────
    # `recipe_body_in_system`: re-inject the target recipe's body into the system
    # prompt to keep it salient at code-gen time (eval-side approximation of a
    # PostToolUse re-injection hook). Tests: does keeping the recipe content
    # closer to the generation moment improve api-token coverage?
    "recipe_body_in_system": {"append_recipe_body": True},

    # `explicit_binding`: insert a sentence into plan_first.md asking the agent
    # to declare which recipe section each step implements (e.g. step + recipe_section).
    # Tests: does the agent's act of declaring the binding anchor its later code?
    # Anchor on the numbered plan_first content (text present in live nonneg arm).
    "explicit_binding": {"sys_sub": [(
        _NUMBERED,
        _NUMBERED + " When you call present_plan, ALSO include in each step a "
        "`recipe_section` field naming WHICH numbered section of the recipe that step "
        "implements (e.g. step.recipe_section = 'Normalize + log1p'). Declaring this "
        "binding helps you stay faithful to the recipe's APIs when you write code."
    )]},

    # Belt-and-braces: both interventions together.
    "explicit_binding_plus_body": {"append_recipe_body": True, "sys_sub": [(
        _NUMBERED,
        _NUMBERED + " When you call present_plan, ALSO include in each step a "
        "`recipe_section` field naming WHICH numbered section of the recipe that step "
        "implements."
    )]},

    # ── Declared-recipes → inject-at-codegen (Phase 2 of #324) ────────────────
    # Hypothesis: rather than force-inject a target recipe (which violates agent
    # autonomy if it rejected the recipe), let the AGENT declare its chosen
    # recipe(s) on its plan steps via the existing `skill` field. The runtime
    # then injects those recipe bodies at code-gen time. Tests:
    #   `current_go`            — baseline: continues past plan, no instruction, no injection
    #   `plan_declared_injected` — prompt nudges agent to declare; harness injects declared bodies
    "current_go": {"continue_after_plan": True},
    "plan_declared_injected": {
        "continue_after_plan": True,
        "sys_sub": [(
            _NUMBERED,
            _NUMBERED + " IMPORTANT: in your `present_plan` call, populate each "
            "step's `skill` field with the name of the recipe that step will follow "
            "(e.g. 'scrna-qc-clustering', 'deseq2-r'). The runtime uses these "
            "declarations to keep the chosen recipes salient when you generate code "
            "for each step."
        )],
    },
    # (B.1) Step-labeled injection — same declaration prompt as plan_declared_injected,
    # but at injection time each recipe is bound to its specific plan-step indices.
    # Tests whether labeling "recipe X is for step 4" tightens scope_discipline vs.
    # union injection (which lets unrelated recipes leak into step N's context).
    "plan_declared_step_labeled": {
        "continue_after_plan": True,
        "step_labeled_injection": True,
        "sys_sub": [(
            _NUMBERED,
            _NUMBERED + " IMPORTANT: in your `present_plan` call, populate each "
            "step's `skill` field with the name of the recipe that step will follow "
            "(e.g. 'scrna-qc-clustering', 'deseq2-r'). The runtime uses these "
            "declarations to keep each recipe BOUND TO ITS STEP — apply each recipe's "
            "APIs only for the step(s) you bound it to."
        )],
    },
    # (F) Mid-plan recheck steer — single OODA-proxy checkpoint just after Go.
    # The synthetic "User approved" tool result asks the agent to verify step 1's
    # recipe binding once more before coding. Lightest possible iterative pass.
    "plan_declared_with_recheck": {
        "continue_after_plan": True,
        "plan_recheck_steer": True,
        "sys_sub": [(
            _NUMBERED,
            _NUMBERED + " IMPORTANT: in your `present_plan` call, populate each "
            "step's `skill` field with the name of the recipe that step will follow."
        )],
    },
    # (B.1 + F) — both at once; the strongest version short of full OODA (E).
    "plan_declared_step_labeled_with_recheck": {
        "continue_after_plan": True,
        "step_labeled_injection": True,
        "plan_recheck_steer": True,
        "sys_sub": [(
            _NUMBERED,
            _NUMBERED + " IMPORTANT: in your `present_plan` call, populate each "
            "step's `skill` field with the name of the recipe that step will follow. "
            "The runtime binds each recipe to its declared step — apply each recipe "
            "ONLY for the step(s) you bound it to."
        )],
    },
}
