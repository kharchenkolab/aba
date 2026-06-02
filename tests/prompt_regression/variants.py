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
    "canonical_reminder_only": {"arm": "control", "tier": "core"},   # Phase 4 strict — recipes ONLY in <system-reminder>, not in system prompt
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

    # ── Session-audit fixes (2026-06-01, from thr_e3bdc7f6 root-cause) ─────────
    # Each variant maps to one fix in misc/prompt_quality_test_plan.md.
    # Targeted at the two new cases:
    #   recipe_skip_subtask__annotate_clusters  (B2)
    #   scope_creep_on_surprise__celltypist_disagrees  (B4)

    # Fix #1: drop "more than one step" qualifier — sub-tasks need micro-plans.
    # Behavior.md:18 ('first name the concrete thing you need to do RIGHT NOW')
    # gives lip service to per-task planning, but plan_first.md:1 qualifier
    # gives Haiku permission to skip planning on what looks like a short task
    # ("annotate the clusters" feels like one step).
    "subtask_plans": {"sys_sub": [(
        "Before running ANY analysis that takes more than one step",
        "Before running ANY analysis with a deliverable (a figure, a table, a "
        "registered result) — even if it feels like one step (annotate, DE, "
        "embedding, scoring)",
    )]},

    # Fix #3: promote "search_skills hit → read_skill is mandatory next call".
    # Live failure: agent ran search_skills, got annotate-celltype-scrna as a
    # hit, then verbally named it ("using the annotate-celltype-scrna skill")
    # and went straight to coding. Naming ≠ using. Recipes.md says "prefer it"
    # softly; this variant adds the hard sequencing rule.
    "forced_read_after_search": {"sys_sub": [(
        "When one matches what you're doing, it will give a more correct and "
        "cleaner result than improvising the pipeline from memory — so prefer it.",
        "When one matches what you're doing, it will give a more correct and "
        "cleaner result than improvising the pipeline from memory — so prefer "
        "it. **If you ran `search_skills` and it returned a hit relevant to "
        "what you're about to do, your VERY NEXT tool call must be "
        "`read_skill(<top_hit_name>)`. Naming a recipe in your prose ('I'll "
        "use the X skill') is NOT using it — you have to read the body. "
        "Coding before reading is the #1 cause of wrong-API errors and "
        "cluster-driven (not marker-driven) cell-type labels.**",
    )]},

    # Fix #2 (small variant — single new rule, not full reorg): explicit
    # surprise→report+ask clause as a new bullet in behavior.md. The full
    # nonneg-v2 reorganization is heavier; this isolates the rule change.
    "surprise_report_not_investigate": {"sys_sub": [(
        "- **The approved plan's steps ARE the scope**",
        "- **A surprising result is a finding to REPORT, not a tangent to "
        "PURSUE.** When two methods disagree, an annotation looks wrong, a "
        "QC metric is unexpected, or any result contradicts your earlier "
        "claim — summarize the surprise in 1-2 sentences with the numbers, "
        "and ASK the user whether to investigate. Do NOT autonomously launch "
        "a discrepancy investigation: extra confusion matrices, per-cell "
        "marker breakdowns, 'why-it-disagrees' figures, 'critical issue' or "
        "'executive summary' reports are scope-creep even if no new sample "
        "is touched. Offer in one sentence; let the user decide.\n"
        "- **The approved plan's steps ARE the scope**",
    )]},

    # Fix #4: add a negative-example pair to the recipes.md positive framing.
    # Pattern lifted from behavior.md:17 ("Substituting DATA_DIR/geo_data is
    # the single most common path-failure mode") — pair every rule with its
    # known trap so Haiku has a refusal anchor, not just an aspiration.
    "negative_examples": {"sys_sub": [(
        "When one matches what you're doing, it will give a more correct and "
        "cleaner result than improvising the pipeline from memory — so prefer it.",
        "When one matches what you're doing, it will give a more correct and "
        "cleaner result than improvising the pipeline from memory — so prefer "
        "it. **The trap is naming a recipe ('I'll use the X skill') without "
        "reading it — verbal mention is not invocation; `read_skill(X)` is.**",
    )]},

    # ── Plan-faithfulness variants (2026-06-01) ────────────────────────────
    # All use continue_after_plan=True so the rollout proceeds past
    # present_plan into codegen, where recipe_following_strict can be measured.
    # `current_go` is the existing baseline-with-continue.

    # A. Convergence rule — divergence is allowed but the model must converge
    # back. The default is faithful execution; "I'll do it my way" is the
    # failure mode.
    "pf_converge_back": {"continue_after_plan": True, "sys_sub": [(
        "Execute the plan one step at a time",
        "When following an approved plan, your code at each step should "
        "implement the bound recipe (declared in `step.skill`). Divergence "
        "is allowed ONLY when the recipe demonstrably doesn't fit the data, "
        "or the user asked for a change — in that case, name the reason in "
        "one sentence and try to converge back to the plan's next step "
        "rather than improvising onward. The default is faithful execution; "
        "'I'll do it my way' is the failure mode.\n"
        "- Execute the plan one step at a time",
    )]},

    # checkbox_plan_rewrite — biomni-borrow A. Restate the entire plan as a
    # checkbox checklist at the START of EVERY assistant turn, including
    # explicit `[✗] (failed because…) + [ ] modified step` recovery syntax.
    # Hypothesis: pull the plan back to recency every turn so it doesn't
    # decay into history under accumulating observations, AND provide the
    # template for drift-recovery that Haiku never invents on its own
    # (zero drift_recovered outcomes across 224 verdicts in prior round).
    # Biomni reference: a1.py:1098-1143 (checklist with [ ] [✓] [✗]).
    "checkbox_plan_rewrite": {"continue_after_plan": True, "sys_sub": [(
        "Execute the plan one step at a time",
        "**Restate and update the plan at the start of EVERY assistant turn** "
        "as a checkbox checklist:\n"
        "  1. [✓] First step (completed)\n"
        "  2. [ ] Second step  ← about to execute\n"
        "  3. [ ] Third step\n"
        "Marker semantics: `[ ]` pending; `[✓]` done; `[✗]` failed/modified "
        "with the reason in parens. If a step fails or no longer fits the "
        "data, mark it `[✗] (failed because <reason>)` and insert a "
        "`[ ] modified step <what>` immediately below it, then continue. "
        "The most recent message in our conversation must ALWAYS show the "
        "current plan state — this keeps you on track and lets the user "
        "see progress.\n"
        "- Execute the plan one step at a time",
    )]},

    # B. Restate-before-code — name step + bound recipe section before each
    # run_python/run_r. Anchors codegen to the plan.
    "pf_restate_before_code": {"continue_after_plan": True, "sys_sub": [(
        "Execute the plan one step at a time",
        "Before each `run_python`/`run_r` call during plan execution, "
        "briefly state which step number you're executing and which bound "
        "recipe section it implements. One sentence is enough — this "
        "anchors the code to the plan. If you can't name the step, the "
        "code is probably scope-creep; stop and re-read the plan.\n"
        "- Execute the plan one step at a time",
    )]},

    # C. Plan-as-contract — strengthen the framing that the plan IS what
    # was approved; deviations need surfacing.
    "pf_plan_as_contract": {"continue_after_plan": True, "sys_sub": [(
        "Execute the plan one step at a time",
        "The approved plan is a CONTRACT with the user — the steps you "
        "proposed are the steps you'll execute. Implementing them "
        "faithfully (with the bound recipe's APIs) is what was approved. "
        "You don't need to ask permission to execute the plan, but you DO "
        "need to flag any meaningful deviation (a different normalization, "
        "a different statistical test, a skipped step) in one sentence "
        "before doing it.\n"
        "- Execute the plan one step at a time",
    )]},

    # D. Present_plan-hint — system-level reminder about what's bound.
    "pf_present_plan_hint": {"continue_after_plan": True, "sys_sub": [(
        "Execute the plan one step at a time",
        "After the user approves the plan, your job is to execute those "
        "steps in order, applying the APIs of the bound recipes (the ones "
        "you put in `step.skill`) — not to redesign on the fly. The plan "
        "is fixed at approval time; codegen is the FOLLOWING-THROUGH step, "
        "not a fresh planning loop.\n"
        "- Execute the plan one step at a time",
    )]},

    # E. Combined A + B — convergence + restate
    "pf_combined_AB": {"continue_after_plan": True, "sys_sub": [(
        "Execute the plan one step at a time",
        "When following an approved plan, your code at each step should "
        "implement the bound recipe (declared in `step.skill`). Default to "
        "faithful execution; divergence is allowed only when the recipe "
        "doesn't fit the data, named in one sentence, and you try to "
        "converge back to the plan's next step. Before each "
        "`run_python`/`run_r`, briefly state which step + recipe section "
        "you're executing.\n"
        "- Execute the plan one step at a time",
    )]},

    # F. Combined E + recipe-body-injection (the upper-bound stack).
    "pf_combined_AB_plus_body": {
        "continue_after_plan": True,
        "append_recipe_body": True,
        "sys_sub": [(
            "Execute the plan one step at a time",
            "When following an approved plan, your code at each step should "
            "implement the bound recipe. Default to faithful execution; "
            "divergence only when the recipe doesn't fit, named in one "
            "sentence, converge back when possible. Before each `run_python`/"
            "`run_r`, briefly state which step + recipe section.\n"
            "- Execute the plan one step at a time",
        )],
    },

    # surprise_v2_strong forced onto the nonneg base (live arm) to verify the
    # rule transfers — the canonical-based test left this open. Uses the same
    # anchor text from behavior_slim.md (the nonneg-arm behavior file).
    "surprise_v2_strong_nonneg": {"arm": "nonneg", "sys_sub": [(
        "- **The approved plan's steps ARE the scope**",
        "- **Surprises end the turn.** When two methods disagree, an "
        "annotation looks wrong, or any result contradicts your earlier "
        "claim — your IMMEDIATE next action is to STOP, summarize the "
        "surprise in 2-3 sentences with the actual numbers, and ASK the "
        "user (end_turn). Do NOT call ANY tool in the same turn: not "
        "`run_python`, not `run_r`, not 'one more summary plot', not a "
        "comparison figure, not a 'Now let me investigate' analysis. The "
        "literal pattern 'Now let me create / investigate / generate one "
        "more X' is the failure mode — recognize it and stop. End_turn with "
        "prose; let the user decide whether to investigate.\n"
        "- **The approved plan's steps ARE the scope**",
    )]},

    # surprise_v2: stronger language than v1 — name the tool-call failure
    # mode explicitly. The v1 rule allowed "summary first, then investigate"
    # because the model reads "ask the user whether to investigate" and
    # interprets "create a summary plot first" as part of the asking.
    "surprise_v2_strong": {"sys_sub": [(
        "- **The approved plan's steps ARE the scope**",
        "- **Surprises end the turn.** When two methods disagree, an "
        "annotation looks wrong, or any result contradicts your earlier "
        "claim — your IMMEDIATE next action is to STOP, summarize the "
        "surprise in 2-3 sentences with the actual numbers, and ASK the "
        "user (end_turn). Do NOT call ANY tool in the same turn: not "
        "`run_python`, not `run_r`, not 'one more summary plot', not a "
        "comparison figure, not a 'Now let me investigate' analysis. The "
        "literal pattern 'Now let me create / investigate / generate one "
        "more X' is the failure mode — recognize it and stop. End_turn with "
        "prose; let the user decide whether to investigate.\n"
        "- **The approved plan's steps ARE the scope**",
    )]},
    # nonneg arm + surprise rule combined (hoists rule into invariants).
    "nonneg_with_surprise": {"arm": "nonneg", "sys_sub": [(
        "- **The approved plan's steps ARE the scope**",
        "- **Surprises end the turn.** When two methods disagree or a "
        "result contradicts your earlier claim — STOP, summarize in 2-3 "
        "sentences with numbers, ASK the user (end_turn). Do NOT call any "
        "tool ('Now let me create one more summary / investigate' is the "
        "failure mode).\n"
        "- **The approved plan's steps ARE the scope**",
    )]},

    # Combined bet: fix #1 + fix #3 + surprise rule, the three highest-leverage
    # changes from the meta-analysis.
    "combined_v1": {"sys_sub": [
        (
            "Before running ANY analysis that takes more than one step",
            "Before running ANY analysis with a deliverable (a figure, a table, "
            "a registered result) — even if it feels like one step (annotate, "
            "DE, embedding, scoring)",
        ),
        (
            "When one matches what you're doing, it will give a more correct and "
            "cleaner result than improvising the pipeline from memory — so prefer it.",
            "When one matches what you're doing, it will give a more correct and "
            "cleaner result than improvising the pipeline from memory — so prefer "
            "it. **If you ran `search_skills` and got a hit, the VERY NEXT tool "
            "call must be `read_skill(<top_hit>)`. Naming ≠ using.**",
        ),
        (
            "- **The approved plan's steps ARE the scope**",
            "- **A surprising result is a finding to REPORT, not a tangent to "
            "PURSUE.** When two methods disagree or a result contradicts your "
            "earlier claim — summarize in 1-2 sentences with numbers, ASK the "
            "user. Do NOT autonomously launch a discrepancy investigation "
            "(extra confusion matrices, 'why-it-disagrees' plots, 'critical "
            "issue' reports are scope-creep).\n"
            "- **The approved plan's steps ARE the scope**",
        ),
    ]},
}
