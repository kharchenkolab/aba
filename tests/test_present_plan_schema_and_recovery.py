"""present_plan: PlanStep schema cleanliness + validator recovery completeness.

Two separate fixes coupled here because they address the SAME live failure
(prj_2578185f thr_577d666a, 2026-06-09 — two present_plan calls in one
session, byte-identically misformed):

1. **Schema cleanliness (upstream).** FastMCP turns the PlanStep TypedDict's
   docstring into the JSON-schema `description` field for each plan step,
   which the model reads. The previous docstring contained developer-facing
   meta-commentary about how the schema is constructed and a past Opus 4.7
   incident ("the schema is just `array of anything` and the model has to
   *infer* field names from prose — Opus 4.7 was caught using `step` instead
   of `title`"). That self-referential schema-talk appears to destabilize
   the model exactly where it needs to be most confident — at the boundary
   between `assumptions` (simple) and `steps` (complex). In the live session
   the model slipped from JSON to XML mid-emission, emitting:

       "assumptions": "[...]</assumptions>\\n<parameter name=\\"steps\\">[...]"

   crammed everything into one assumptions string with no top-level `steps`.

2. **Validator recovery completeness.** validator.py's "JSON array embedded
   in a text field" recovery (line 234-252) already extracts steps in this
   case, but DROPS the `skill` and `parameters` fields on each step. With
   plan_first.md telling the agent to bind a recipe to each step via
   `step.skill`, losing the skill means the recipe-uptake tracker never
   captures the binding, no recipe body lands in the system prompt for
   later turns, and the plan's recipe contract isn't honored. The
   `assumptions` cleanup also misses the case where the WHOLE assumption
   string IS the JSON-array-followed-by-XML leak (not just an inner item
   with embedded tags), leaving the persisted plan's assumptions as a
   1-element list of garbage.

Run: ``.venv/bin/python -m pytest tests/test_present_plan_schema_and_recovery.py -q``
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path


_TMP = tempfile.mkdtemp(prefix="aba_plan_recov_")
os.environ.setdefault("ABA_RUNTIME_DIR", _TMP)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_TMP, "t.db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.planning.validator import normalize_plan, validate_plan  # noqa: E402


# ─── (1) schema cleanliness ────────────────────────────────────────────
def test_planstep_schema_description_has_no_meta_commentary():
    """The PlanStep TypedDict's docstring becomes the JSON-schema description
    that FastMCP pushes to the model. It must describe the *data shape*, not
    the schema-generation framework, prior model incidents, or commit hashes
    — that's developer noise that destabilizes model output (see live failure
    in prj_2578185f thr_577d666a, both present_plan calls slipped to XML)."""
    from content.bio.mcp_servers.aba_core.tools.plan_etc import PlanStep
    desc = (PlanStep.__doc__ or "").lower()

    # Anti-patterns: framework names, schema-meta talk, model-incident lore,
    # commit refs. These belong in a Python comment, not the schema field.
    forbidden = [
        "fastmcp",
        "tool_schemas",
        "opus 4.7",            # naming the model in its own schema description
        "commit ",             # any commit hash / history reference
        "wu-1", "pre-wu-1", "post-wu-1",
        "array of anything",   # the schema-meta phrase that anchored the slip
        "infer field names",   # ditto
    ]
    leaks = [k for k in forbidden if k in desc]
    assert not leaks, (
        f"PlanStep.__doc__ contains framework/meta language that leaks into "
        f"the JSON schema sent to the model: {leaks}. Move that history into "
        f"a Python comment beneath the docstring.")

    # Positive check: the docstring should still NAME the required field so
    # the model knows what's expected (the original justification for the
    # TypedDict). Just say it cleanly.
    assert "title" in desc, (
        "PlanStep.__doc__ should still mention `title` — that's why this "
        "typed shape exists in the first place.")


# ─── (2) validator: skill + parameters preserved through recovery ──────
def test_recovery_preserves_skill_on_each_step():
    """The model produced a leak where `assumptions` swallowed the entire
    `steps` array. The validator's recovery branch extracts the steps but
    used to drop `skill`. Without skill, the recipe-uptake tracker can't
    capture the binding plan_first.md instructs the agent to make."""
    leaked_assumptions = (
        '["Sample is human", "PBMC defaults apply"]</assumptions>\n'
        '<parameter name="steps">[\n'
        '  {"title": "Load counts", "description": "Read mtx triplet",'
        '   "skill": "scrna-qc-clustering-v2", "expected_outputs": ["AnnData"]},\n'
        '  {"title": "QC + filter", "description": "Compute QC metrics",'
        '   "skill": "scrna-qc-clustering-v2", "expected_outputs": ["qc.png"]}\n'
        ']'
    )
    p = normalize_plan({
        "title": "Scanpy QC + clustering",
        "rationale": "Standard workflow.",
        "assumptions": leaked_assumptions,
    })
    assert [s.title for s in p.steps] == ["Load counts", "QC + filter"], (
        f"steps not recovered: {[s.title for s in p.steps]}")
    skills = [s.skill for s in p.steps]
    assert skills == ["scrna-qc-clustering-v2", "scrna-qc-clustering-v2"], (
        f"skill dropped on recovery: {skills} — plan_first.md says skill is "
        f"the binding contract for the recipe-uptake tracker")


def test_recovery_preserves_parameters_on_each_step():
    """parameters is a dict the agent uses to pin resolved choices on a
    step. Lost together with skill in the same pre-fix recovery branch."""
    leaked_assumptions = (
        '["X", "Y"]</assumptions><parameter name="steps">'
        '[{"title": "Run DE", "skill": "deseq2-r",'
        '  "parameters": {"alpha": 0.05, "min_count": 10}}]'
    )
    p = normalize_plan({"title": "T", "assumptions": leaked_assumptions})
    assert len(p.steps) == 1, f"expected 1 step, got {len(p.steps)}"
    assert p.steps[0].parameters == {"alpha": 0.05, "min_count": 10}, (
        f"parameters dropped: {p.steps[0].parameters}")


# ─── (3) validator: assumptions cleanup for whole-string leak ──────────
def test_whole_string_xml_leak_in_assumptions_is_cleaned():
    """The shape that bit the live session: the agent passes assumptions as
    a string that BEGINS with a JSON array literal (the real assumptions)
    and then trails into </assumptions><parameter name="steps">[...].

    Pre-fix: the validator's _coerce_string_list saw the literal '\\n'
    inside and either single-element-listed it or line-split it into junk,
    leaving the persisted plan's assumptions as garbage the UI rendered
    verbatim. Post-fix: the JSON prefix is extracted as the real list."""
    leaked = (
        '["Sample: GSM5746259 only (day 0)", '
        '"Organism: Homo sapiens, PBMC defaults apply", '
        '"Thresholds picked from quantile readout, not hardcoded"]'
        '</assumptions>\n'
        '<parameter name="steps">[{"title": "Load", "skill": "x"}]'
    )
    p = normalize_plan({"title": "T", "assumptions": leaked})

    # Three clean assumption strings — not a single garbage blob.
    assert len(p.assumptions) == 3, (
        f"expected 3 clean assumptions, got {len(p.assumptions)}: "
        f"{p.assumptions}")
    assert all("<" not in a and "parameter name" not in a
               and "</assumptions>" not in a
               for a in p.assumptions), (
        f"leak residue still in assumptions: {p.assumptions}")
    assert p.assumptions[0].startswith("Sample: GSM5746259"), (
        f"first assumption mangled: {p.assumptions[0]!r}")


# ─── (4) end-to-end: replay the prj_2578185f live failure exactly ──────
def test_live_failure_shape_recovers_into_a_usable_plan():
    """Feed the validator the EXACT input shape the live session produced —
    only `title`, `rationale`, `assumptions` at the top level (no `steps`
    key), with the leaked assumptions value carrying the steps array.
    The recovered plan must have: 8 steps with titles, skills preserved,
    and clean assumptions in a 5-element list (not 1-of-garbage)."""
    steps_blob = (
        '[\n'
        '  {"title": "Load counts", "skill": "scrna-qc-clustering-v2",'
        '   "expected_outputs": ["AnnData"]},\n'
        '  {"title": "Compute QC metrics", "skill": "scrna-qc-clustering-v2",'
        '   "expected_outputs": ["Quantile readout"]},\n'
        '  {"title": "QC figures + apply filter", "skill": "scrna-qc-clustering-v2",'
        '   "expected_outputs": ["qc_violins_pre.png"]},\n'
        '  {"title": "Normalize, log, HVGs", "skill": "scrna-qc-clustering-v2",'
        '   "expected_outputs": ["hvg_plot.png"]},\n'
        '  {"title": "PCA + elbow", "skill": "scrna-qc-clustering-v2",'
        '   "expected_outputs": ["pca_elbow.png"]},\n'
        '  {"title": "Neighbors + Leiden + UMAP", "skill": "scrna-qc-clustering-v2",'
        '   "expected_outputs": ["umap_clusters.png"]},\n'
        '  {"title": "Cluster markers + canonical overlay", "skill": "scrna-qc-clustering-v2",'
        '   "expected_outputs": ["cluster_markers.csv"]},\n'
        '  {"title": "Save processed h5ad", "skill": "scrna-qc-clustering-v2",'
        '   "expected_outputs": ["processed.h5ad"]}\n'
        ']'
    )
    leaked = (
        '["Sample: GSM5746259 only (day 0)", '
        '"Organism: Homo sapiens, PBMC canonical markers", '
        '"10x triplet is GEO-style loose / GSM-prefixed", '
        '"Thresholds picked from quantile readout", '
        '"No doublet removal, no ambient-RNA correction"]'
        '</assumptions>\n'
        f'<parameter name="steps">{steps_blob}'
    )
    p = normalize_plan({
        "title": "Scanpy QC + clustering for GSM5746259",
        "rationale": "Standard single-sample workflow.",
        "assumptions": leaked,
    })

    # Steps
    assert len(p.steps) == 8, f"expected 8 recovered steps, got {len(p.steps)}"
    assert all(s.skill == "scrna-qc-clustering-v2" for s in p.steps), (
        f"skill not preserved on every recovered step: "
        f"{[s.skill for s in p.steps]}")

    # Assumptions
    assert len(p.assumptions) == 5, (
        f"expected 5 clean assumptions, got {len(p.assumptions)}: "
        f"{p.assumptions}")
    assert "Sample:" in p.assumptions[0]


# ─── (5) regression guard: clean canonical input untouched ─────────────
def test_canonical_clean_input_unchanged():
    """The fixes must not regress the happy path. A well-formed plan with
    skill + parameters + assumptions as a proper list still parses
    identically — no double-extraction, no extra fields added."""
    p = normalize_plan({
        "title": "T",
        "rationale": "R",
        "assumptions": ["a1", "a2"],
        "steps": [
            {"title": "S1", "skill": "rec-1", "parameters": {"k": 1}},
            {"title": "S2", "skill": "rec-2"},
        ],
    })
    assert [s.title for s in p.steps] == ["S1", "S2"]
    assert [s.skill for s in p.steps] == ["rec-1", "rec-2"]
    assert p.steps[0].parameters == {"k": 1}
    assert p.assumptions == ["a1", "a2"]
