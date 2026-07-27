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
    for key in ("suggestion", "fix", "remedy", "levers"):
        msg = _typed_task_error({"error": "env.solve_conflict", "detail": "d",
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
