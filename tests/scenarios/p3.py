"""Phase 3 — discovery via search_skills, then Skill dispatch.

P2 showed the model can wrap a NAMED skill in Skill(...). P3 ratchets
up: the user does NOT name a skill, and the model has to (a) decide
it needs a recipe at all, (b) pick a search query, (c) parse the
results, (d) dispatch Skill on the right hit.

This is where the prj_2c015847-class failures showed up live: the
model either skipped search_skills entirely (going straight to
fetch_url), or did search → invented a tool from the result name.

Each scenario aborts after the second tool dispatch (stop_after_n_tools=2)
so the test never actually executes recipe Python.

Expected sequence per scenario:
    1. search_skills(query=<sensible query>)
    2. Skill(skill=<a name from the search result>)

Failure modes worth seeing in the report:
  A) NO search_skills called — model skipped discovery entirely
     (jumped to fetch_url, run_python, list_data_files, etc.)
  B) search_skills called, but second tool is a BARE NAME, not Skill
     (the original bug — name-as-tool dispatch)
  C) search_skills called, Skill called, but with a skill name that
     didn't appear in the search results (hallucinated / paraphrased)
"""
from __future__ import annotations

from tests.scenarios import Scenario, Assertion


# ── helpers ─────────────────────────────────────────────────────────


def _exactly_search_then_skill(calls):
    """Strict success: first call is search_skills, second is Skill."""
    if len(calls) < 1:
        return False, "no tools were called at all"
    if calls[0][0] != "search_skills":
        return False, (f"first tool was {calls[0][0]!r}, expected "
                       "'search_skills' (model skipped discovery)")
    if len(calls) < 2:
        return False, ("model only called search_skills; never moved to "
                       "Skill — likely got an empty result or didn't "
                       "trust what came back")
    if calls[1][0] != "Skill":
        bare = calls[1][0]
        return False, (f"after search, second tool was {bare!r} — "
                       "expected Skill. If this looks like a skill "
                       "name, that's the bare-name-dispatch bug.")
    sk = calls[1][1].get("skill") or ""
    if not sk:
        return False, "Skill called but skill= arg was empty"
    return True, f"search → Skill(skill={sk!r}); flow intact"


def _search_query_is_sensible(min_words: int = 1):
    """The search query should be a short phrase, not the literal user
    prompt verbatim. We don't pin keywords (different recipes match
    different phrasings) — only sanity-check the shape."""
    def _p(calls):
        if not calls or calls[0][0] != "search_skills":
            return False, "no search_skills call to inspect"
        q = (calls[0][1].get("query") or "").strip()
        if not q:
            return False, "search_skills query was empty"
        wc = len(q.split())
        if wc < min_words:
            return False, f"query {q!r} has {wc} words, expected ≥ {min_words}"
        if wc > 20:
            return False, (f"query {q!r} is suspiciously long "
                           f"({wc} words) — likely paste of user prompt")
        return True, f"query={q!r}"
    return _p


def _skill_arg_relates_to(any_of: tuple[str, ...]):
    """The chosen skill name should contain at least one keyword from
    `any_of`. We don't require a specific recipe (multiple skills can
    plausibly match a single user prompt), just that the choice is
    topically reasonable."""
    def _p(calls):
        if len(calls) < 2 or calls[1][0] != "Skill":
            return False, "no Skill dispatch to inspect"
        sk = (calls[1][1].get("skill") or "").lower()
        hits = [k for k in any_of if k.lower() in sk]
        return (bool(hits),
                f"skill={sk!r}; expected to contain one of {any_of}")
    return _p


def _did_NOT_invent_bare_tools(known_skill_names: tuple[str, ...]):
    """None of the recorded tool names should match a real skill name
    (the bare-name dispatch shape)."""
    def _p(calls):
        bad = [n for n, _ in calls if n in known_skill_names]
        return ((not bad),
                f"bare skill names appeared as tool dispatches: {bad}")
    return _p


# A subset of skill names that are common drift-targets if the model
# treats search results as tool names. Used by the assertion above.
_LIKELY_DRIFT = (
    "fetch-geo-processed-matrices", "scrna-geo-pipeline",
    "query-geo", "scrna-qc-clustering-v2",
    "deseq2-r", "limma-voom", "design-primer",
    "extract-pdf-content", "harmony-integration",
    "scvi-integration", "seurat-integration",
)


# ── scenarios ──────────────────────────────────────────────────────


P3_SCENARIOS: list[Scenario] = [
    # 1. GEO fetch — the live-session prompt that's been failing.
    Scenario(
        name="p3_geo_fetch_request",
        user_prompt=("help me fetch the count matrices for "
                     "GSE192391 from GEO"),
        assertions=[
            Assertion("flow_search_then_skill",
                      _exactly_search_then_skill),
            Assertion("query_is_sensible",
                      _search_query_is_sensible(min_words=1)),
            Assertion("skill_relates_to_geo",
                      _skill_arg_relates_to(("geo", "fetch"))),
            Assertion("did_not_invent_bare_tools",
                      _did_NOT_invent_bare_tools(_LIKELY_DRIFT)),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    # 2. Differential expression — the model has multiple plausible
    #    recipes to pick (deseq2-r, limma-voom, scvi-de).
    Scenario(
        name="p3_differential_expression",
        user_prompt=("I want to run differential expression on bulk "
                     "RNA-seq counts — find me a recipe"),
        assertions=[
            Assertion("flow_search_then_skill",
                      _exactly_search_then_skill),
            Assertion("skill_relates_to_de",
                      _skill_arg_relates_to(("deseq", "limma",
                                              "differential", "de"))),
            Assertion("did_not_invent_bare_tools",
                      _did_NOT_invent_bare_tools(_LIKELY_DRIFT)),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    # 3. Single-cell QC + clustering.
    Scenario(
        name="p3_single_cell_qc",
        user_prompt=("how do I run QC and clustering on a single-cell "
                     "RNA-seq dataset?"),
        assertions=[
            Assertion("flow_search_then_skill",
                      _exactly_search_then_skill),
            Assertion("skill_relates_to_scrna_qc",
                      _skill_arg_relates_to(("scrna", "single", "qc",
                                              "cluster", "seurat",
                                              "scanpy"))),
            Assertion("did_not_invent_bare_tools",
                      _did_NOT_invent_bare_tools(_LIKELY_DRIFT)),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    # 4. PDF extraction — niche, less domain context for the model.
    Scenario(
        name="p3_extract_pdf",
        user_prompt="how do I extract text from a PDF?",
        assertions=[
            Assertion("flow_search_then_skill",
                      _exactly_search_then_skill),
            Assertion("skill_relates_to_pdf",
                      _skill_arg_relates_to(("pdf", "extract"))),
            Assertion("did_not_invent_bare_tools",
                      _did_NOT_invent_bare_tools(_LIKELY_DRIFT)),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    # 5. Primer design — multiple matching recipes.
    Scenario(
        name="p3_primer_design",
        user_prompt=("I need to design PCR primers — find a recipe "
                     "that does this"),
        assertions=[
            Assertion("flow_search_then_skill",
                      _exactly_search_then_skill),
            Assertion("skill_relates_to_primer_design",
                      _skill_arg_relates_to(("primer", "design",
                                              "pcr"))),
            Assertion("did_not_invent_bare_tools",
                      _did_NOT_invent_bare_tools(_LIKELY_DRIFT)),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    # 6. Multi-dataset integration — the model has THREE
    #    near-equivalent recipes (harmony, scvi, seurat-integration).
    Scenario(
        name="p3_scrna_integration",
        user_prompt=("I want to integrate two scRNA-seq datasets from "
                     "different batches — find a recipe"),
        assertions=[
            Assertion("flow_search_then_skill",
                      _exactly_search_then_skill),
            Assertion("skill_relates_to_integration",
                      _skill_arg_relates_to(("harmony", "scvi",
                                              "integration",
                                              "seurat", "batch"))),
            Assertion("did_not_invent_bare_tools",
                      _did_NOT_invent_bare_tools(_LIKELY_DRIFT)),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
    # 7. Implicit discovery — "I want to" with no "find a recipe" hint.
    #    Tests whether the model proactively reaches for search_skills
    #    when the request is open-ended.
    Scenario(
        name="p3_implicit_discovery_marker_genes",
        user_prompt=("I have annotated clusters; what's the best way "
                     "to find marker genes for each one?"),
        assertions=[
            Assertion("flow_search_then_skill",
                      _exactly_search_then_skill),
            Assertion("did_not_invent_bare_tools",
                      _did_NOT_invent_bare_tools(_LIKELY_DRIFT)),
        ],
        max_turns=2,
        stop_after_n_tools=2,
    ),
]
