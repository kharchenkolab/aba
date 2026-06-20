"""Phase 3b — discovery via fetch_recipe (1-call) OR
search_skills + Skill (2-call). Tests Hypothesis #1: small models
prefer collapsed tool chains.

Same prompts as P3. Assertion is permissive: success = the model
reached a recipe body via EITHER path in ≤2 tool calls. This lets us
measure how often `fetch_recipe` (when available) gets picked vs the
2-step chain, AND whether either path closes the P3 gap from 1/7.

Requires `ABA_EXPERIMENTAL_FETCH_RECIPE=1` in the live server's env
so the tool is registered. Without it, scenarios still run but the
model can't pick fetch_recipe → effectively re-runs P3.
"""
from __future__ import annotations

from tests.scenarios import Scenario, Assertion


def _reached_recipe_in_two_or_fewer(calls):
    """Pass if EITHER:
      (a) first call is fetch_recipe, OR
      (b) first is search_skills AND second is Skill.

    Failure shapes (each gets a clear reason in the report):
      A — no tools called at all (text-only response)
      B — first tool wasn't a discovery primitive
      C — searched but never followed up
      D — searched, picked the wrong follow-up (not Skill / not fetch_recipe)
    """
    if not calls:
        return False, "no tools were called (text-only response)"
    first = calls[0][0]
    if first == "fetch_recipe":
        sk = (calls[0][1].get("query") or "").strip()
        if not sk:
            return False, "fetch_recipe called with empty query"
        return True, f"reached via fetch_recipe(query={sk!r}) — 1 call"
    if first == "search_skills":
        if len(calls) < 2:
            return False, ("search_skills called but no follow-up — "
                           "model stopped at the search result "
                           "(P3 'narrate, don't dispatch' failure shape)")
        second = calls[1][0]
        if second == "Skill":
            sk = calls[1][1].get("skill") or ""
            return True, (f"reached via search_skills → "
                          f"Skill(skill={sk!r}) — 2 calls")
        return False, (f"after search, second tool was {second!r} — "
                       "expected Skill or fetch_recipe")
    return False, (f"first tool was {first!r} — expected fetch_recipe "
                   "or search_skills; model skipped discovery")


def _used_fetch_recipe(calls):
    """Diagnostic-only assertion: did the model pick fetch_recipe over
    the 2-step path? Useful to see WHICH route it took on success."""
    if not calls:
        return False, "no tools"
    return (calls[0][0] == "fetch_recipe",
            f"first tool was {calls[0][0]!r}; "
            f"fetch_recipe {'used' if calls[0][0]=='fetch_recipe' else 'NOT used'}")


# ── Scenarios — same prompts as P3 ──────────────────────────────────


P3B_SCENARIOS: list[Scenario] = [
    Scenario(
        name="p3b_geo_fetch_request",
        user_prompt=("help me fetch the count matrices for "
                     "GSE192391 from GEO"),
        assertions=[
            Assertion("reached_recipe_in_two_or_fewer",
                      _reached_recipe_in_two_or_fewer),
            Assertion("diagnostic_used_fetch_recipe",
                      _used_fetch_recipe),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    Scenario(
        name="p3b_differential_expression",
        user_prompt=("I want to run differential expression on bulk "
                     "RNA-seq counts — find me a recipe"),
        assertions=[
            Assertion("reached_recipe_in_two_or_fewer",
                      _reached_recipe_in_two_or_fewer),
            Assertion("diagnostic_used_fetch_recipe",
                      _used_fetch_recipe),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    Scenario(
        name="p3b_single_cell_qc",
        user_prompt=("how do I run QC and clustering on a single-cell "
                     "RNA-seq dataset?"),
        assertions=[
            Assertion("reached_recipe_in_two_or_fewer",
                      _reached_recipe_in_two_or_fewer),
            Assertion("diagnostic_used_fetch_recipe",
                      _used_fetch_recipe),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    Scenario(
        name="p3b_extract_pdf",
        user_prompt="how do I extract text from a PDF?",
        assertions=[
            Assertion("reached_recipe_in_two_or_fewer",
                      _reached_recipe_in_two_or_fewer),
            Assertion("diagnostic_used_fetch_recipe",
                      _used_fetch_recipe),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    Scenario(
        name="p3b_primer_design",
        user_prompt=("I need to design PCR primers — find a recipe "
                     "that does this"),
        assertions=[
            Assertion("reached_recipe_in_two_or_fewer",
                      _reached_recipe_in_two_or_fewer),
            Assertion("diagnostic_used_fetch_recipe",
                      _used_fetch_recipe),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    Scenario(
        name="p3b_scrna_integration",
        user_prompt=("I want to integrate two scRNA-seq datasets from "
                     "different batches — find a recipe"),
        assertions=[
            Assertion("reached_recipe_in_two_or_fewer",
                      _reached_recipe_in_two_or_fewer),
            Assertion("diagnostic_used_fetch_recipe",
                      _used_fetch_recipe),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    Scenario(
        name="p3b_implicit_discovery_marker_genes",
        user_prompt=("I have annotated clusters; what's the best way "
                     "to find marker genes for each one?"),
        assertions=[
            Assertion("reached_recipe_in_two_or_fewer",
                      _reached_recipe_in_two_or_fewer),
            Assertion("diagnostic_used_fetch_recipe",
                      _used_fetch_recipe),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
]
