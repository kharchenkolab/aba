"""Progress-bar task estimate: a real-data run should reuse the task total learned
from a `-profile test` run of the same pipeline+revision (profile mainly changes
input, not the process graph) — so the bar isn't a bare spinner on the first real
run. Exact (pipeline,revision,profile) match still wins; unknown pipeline → None.
"""
from __future__ import annotations
import os
import sys
import json
import tempfile

_tmp = tempfile.mkdtemp(prefix="aba_nftc_")
os.environ["ABA_RUNTIME_DIR"] = _tmp

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.exec import nextflow as nf  # noqa: E402


def _write_store(d):
    nf._task_count_path().parent.mkdir(parents=True, exist_ok=True)
    nf._task_count_path().write_text(json.dumps(d))


def test_cross_profile_fallback():
    """Only a `-profile test` count is known; a real (empty-profile) run reuses it."""
    _write_store({"nf-core/rnaseq|3.21.0|test": 209})
    assert nf.expected_task_count("nf-core/rnaseq", "3.21.0", None) == 209
    assert nf.expected_task_count("nf-core/rnaseq", "3.21.0", "") == 209


def test_exact_match_preferred():
    _write_store({"nf-core/rnaseq|3.21.0|test": 209,
                  "nf-core/rnaseq|3.21.0|": 240})
    assert nf.expected_task_count("nf-core/rnaseq", "3.21.0", "") == 240   # exact wins
    assert nf.expected_task_count("nf-core/rnaseq", "3.21.0", "test") == 209


def test_prefers_largest_when_multiple_profiles():
    _write_store({"nf-core/rnaseq|3.21.0|test": 209,
                  "nf-core/rnaseq|3.21.0|debug": 300})
    assert nf.expected_task_count("nf-core/rnaseq", "3.21.0", "singularity") == 300


def test_unknown_pipeline_none():
    _write_store({"nf-core/rnaseq|3.21.0|test": 209})
    assert nf.expected_task_count("nf-core/chipseq", "2.1.0", "") is None
    assert nf.expected_task_count("nf-core/rnaseq", "9.9.9", "") is None
