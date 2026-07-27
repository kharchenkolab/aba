"""A failed job must surface the SUBSTRATE'S typed verdict, not a guess.

Live (2026-07-27, an arm64 slurm node): a background job failed with weft's
`env.platform_mismatch` — "env is locked for ['linux-64','osx-arm64'] but site
orbslurm is linux-aarch64" — a typed error whose hints name the fix. aba
replaced it with "weft task FAILED with no result.json (infra failure before the
entry ran?)". The agent concluded the SITE was broken, re-submitted the
identical job, and it failed identically.

Same class as the env-resolution swallow: a typed, actionable diagnosis
discarded in favour of a generic one. Guarded here at the rendering seam.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.jobs.weft_submitter import _typed_task_error  # noqa: E402

PLATFORM_MISMATCH = json.dumps({
    "error": "env.platform_mismatch",
    "detail": "env is locked for ['linux-64', 'osx-arm64'] but site orbslurm "
              "is linux-aarch64",
    "hints": {"locked_platforms": ["linux-64", "osx-arm64"],
              "site_platform": "linux-aarch64",
              "suggestion": "use an isolated env (make_isolated_env) — it "
                            "re-locks for the site's platform automatically"},
    "stage": "realize",
})


def test_the_live_failure_is_rendered_with_cause_and_fix():
    """THE regression, verbatim from the incident."""
    msg = _typed_task_error(PLATFORM_MISMATCH)
    assert msg
    assert "env.platform_mismatch" in msg          # the CLASS
    assert "linux-aarch64" in msg                  # the specific mismatch
    assert "make_isolated_env" in msg              # the FIX, from hints
    assert "infra failure" not in msg


def test_a_dict_payload_works_too():
    """weft rows carry a dict on some paths and a JSON string on others."""
    msg = _typed_task_error(json.loads(PLATFORM_MISMATCH))
    assert msg and "env.platform_mismatch" in msg


def test_hint_key_variants_are_all_surfaced():
    """weft puts its lever under different keys depending on the path. Uses an
    UNMAPPED code deliberately — a code with an aba lever overrides the hint by
    design (see the override tests below)."""
    for key in ("suggestion", "fix", "remedy", "levers"):
        msg = _typed_task_error({"error": "task.capacity", "detail": "d",
                                 "hints": {key: "DO THE THING"}})
        assert "DO THE THING" in msg, key


def test_absent_or_unusable_payloads_keep_the_callers_wording():
    """CEILING: returning "" or a bare label would REPLACE a serviceable
    generic message with an empty one. None means "I have nothing to add"."""
    assert _typed_task_error(None) is None
    assert _typed_task_error("") is None
    assert _typed_task_error({}) is None
    assert _typed_task_error({"detail": "no code here"}) is None
    assert _typed_task_error([1, 2, 3]) is None


def test_an_unparseable_string_still_beats_nothing():
    """WIDE — the degenerate shape: not JSON, but the substrate said SOMETHING.
    Passing it through is more useful than inventing 'infra failure'."""
    msg = _typed_task_error("sbatch: error: Batch job submission failed: bad partition")
    assert msg and "bad partition" in msg


def test_no_hints_still_renders_class_and_detail():
    msg = _typed_task_error({"error": "task.invalid", "detail": "walltime unparseable"})
    assert "task.invalid" in msg and "walltime unparseable" in msg


# ── the FIX must name a lever the agent can actually pull ───────────────────
#
# Surfacing the substrate's verdict is only half the job. weft's hints are
# written for a WEFT caller ("add the site's platform to the spec's 'platforms'
# and env_ensure again") — an agent cannot call env_ensure, so a correct
# diagnosis still dead-ends. Observed live: the agent read the platform_mismatch
# correctly, explained it well, and then had no action available.

def test_platform_mismatch_names_an_ABA_lever_not_a_weft_verb():
    msg = _typed_task_error(PLATFORM_MISMATCH)
    assert "make_isolated_env" in msg
    assert "env_ensure" not in msg, \
        "the substrate hint names a verb the agent cannot call"
    # and it must still carry the diagnosis, not just the lever
    assert "env.platform_mismatch" in msg and "linux-aarch64" in msg


def test_solve_conflict_also_gets_the_aba_lever():
    msg = _typed_task_error({"error": "env.solve_conflict", "detail": "unsat",
                             "hints": {"suggestion": "relax pins and env_ensure"}})
    assert "make_isolated_env" in msg and "env_ensure" not in msg


def test_unmapped_codes_still_use_the_substrate_hint():
    """CEILING: the override table is small on purpose. A code aba has no lever
    for must keep weft's own hint — which is usually the best available — rather
    than losing it to an empty mapping."""
    msg = _typed_task_error({"error": "task.walltime_exceeded", "detail": "d",
                             "hints": {"suggestion": "ask for a longer walltime"}})
    assert "ask for a longer walltime" in msg
