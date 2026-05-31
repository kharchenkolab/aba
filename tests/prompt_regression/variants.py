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
}
