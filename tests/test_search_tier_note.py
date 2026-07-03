"""search_skills_tool must type its results (recipe vs knowhow) and, ONLY when a
knowhow is in the hits, append guidance telling the agent how to use the two
tiers. A pure-recipe result set stays clean (no advice-tier noise).

Guards the Task-2 decision: rather than re-rank BM25, we give the agent a typed
surface and let it choose — so the note must (a) carry `kind` per result and
(b) explain the tiers exactly when they're present."""
from __future__ import annotations
import os, sys, tempfile

os.environ["ABA_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="aba_tier_")
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import pytest  # noqa: E402
import core.skills.loader as L  # noqa: E402
from core.skills.loader import SkillSpec, register_skill_spec  # noqa: E402
from content.bio.tools.discovery import search_skills_tool  # noqa: E402

RECIPE = SkillSpec(
    name="scvi-integration", kind="recipe", visibility="local", domain="genomics",
    requires_tools=("run_python",), description="run scVI batch integration on scRNA-seq",
    when_to_use="integrate multiple scRNA-seq samples with scVI",
    keywords=("scvi", "integration", "batch", "scrna"))
KNOWHOW = SkillSpec(
    name="scrna-integration-decision", kind="knowhow", visibility="local", domain="genomics",
    requires_tools=("Read",), description="decision guide for choosing an integration method",
    when_to_use="which integration method Harmony vs scVI for scRNA-seq samples",
    keywords=("integration", "decision", "harmony", "scvi", "which method"))
# a recipe on an unrelated topic so a pure-recipe query has something to match
RECIPE2 = SkillSpec(
    name="bulk-de", kind="recipe", visibility="local", domain="genomics",
    requires_tools=("run_python",), description="bulk RNA-seq differential expression with DESeq2",
    when_to_use="run bulk RNA-seq DE with DESeq2", keywords=("bulk", "deseq2", "differential"))


@pytest.fixture(autouse=True)
def _registry():
    saved = dict(L._REGISTRY)
    L._REGISTRY.clear(); L._INDEX = None
    for s in (RECIPE, KNOWHOW, RECIPE2):
        register_skill_spec(s)
    yield
    L._REGISTRY.clear(); L._REGISTRY.update(saved); L._INDEX = None


def test_results_carry_kind():
    res = search_skills_tool({"query": "which integration method harmony or scvi"})
    kinds = {s["name"]: s["kind"] for s in res["skills"]}
    assert kinds.get("scrna-integration-decision") == "knowhow"
    assert kinds.get("scvi-integration") == "recipe"


def test_note_explains_tiers_when_knowhow_present():
    res = search_skills_tool({"query": "which integration method harmony or scvi"})
    assert any(s["kind"] == "knowhow" for s in res["skills"]), "expected a knowhow hit"
    note = res["note"].lower()
    assert "knowhow" in note and "recipe" in note
    assert "method-choice" in note or "decision guide" in note


def test_note_stays_clean_for_pure_recipe_query():
    """A query that matches only recipes must NOT get the advice-tier guidance."""
    res = search_skills_tool({"query": "bulk deseq2 differential expression", "limit": 3})
    assert res["skills"] and all(s["kind"] == "recipe" for s in res["skills"]), \
        f"expected only recipes, got {[(s['name'], s['kind']) for s in res['skills']]}"
    assert "decision guide" not in res["note"].lower()


if __name__ == "__main__":
    import types
    fx = _registry.__wrapped__ if hasattr(_registry, "__wrapped__") else None
    # simple manual run (pytest fixture won't auto-apply): set up registry inline
    for s in (RECIPE, KNOWHOW, RECIPE2):
        register_skill_spec(s)
    test_results_carry_kind()
    print("PASS test_results_carry_kind")
    test_note_explains_tiers_when_knowhow_present()
    print("PASS test_note_explains_tiers_when_knowhow_present")
    test_note_stays_clean_for_pure_recipe_query()
    print("PASS test_note_stays_clean_for_pure_recipe_query")
