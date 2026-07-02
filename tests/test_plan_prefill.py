"""Plan-card prefill: the launch form must open with the user's `input` path
filled in — regardless of whether the agent nested the run-params under
`parameters.params` or (as it commonly does) put them FLAT beside
pipeline/revision.

Bug (2026-07): the agent is only told `parameters` is "a dict of resolved
choices"; it emits {pipeline, revision, input, genome} flat. enrich_plan_steps
read `input` only from `parameters.params`, so the required `input` field
rendered empty even when the user gave a samplesheet path. Fix: accept both
shapes. This test pins both.
"""
from __future__ import annotations
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="aba_planpf_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_DB_PATH"] = os.path.join(_tmp, "pf.db")

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.exec import nextflow_schema as ns  # noqa: E402

_SCHEMA = {"definitions": {"io": {"title": "Input/output options",
                                  "required": ["input"],
                                  "properties": {"input": {"type": "string"},
                                                 "genome": {"type": "string"},
                                                 "outdir": {"type": "string"}}}}}
SS = "/groups/x/samplesheet.csv"


def _enrich_one(step, monkeypatch):
    monkeypatch.setattr(ns, "fetch_schema", lambda *a, **k: _SCHEMA)
    return ns.enrich_plan_steps([step])[0]


def test_prefill_flat_shape(monkeypatch):
    """Agent's natural emission: run-params FLAT beside pipeline/revision."""
    step = {"title": "Run rnaseq", "skill": "run_nextflow",
            "parameters": {"pipeline": "nf-core/rnaseq", "revision": "3.21.0",
                           "input": SS, "genome": "R64-1-1"}}
    out = _enrich_one(step, monkeypatch)
    assert out["pipeline"] == "nf-core/rnaseq" and out["revision"] == "3.21.0"
    assert out["prefilled"].get("input") == SS, out["prefilled"]
    assert out["prefilled"].get("genome") == "R64-1-1", out["prefilled"]
    # input must NOT be excluded from the form (user supplied it)
    names = {p["name"] for g in out["param_form"] for p in g["params"]}
    assert "input" in names


def test_prefill_nested_shape(monkeypatch):
    """Documented shape: run-params nested under parameters.params."""
    step = {"title": "Run rnaseq", "skill": "run_nextflow",
            "parameters": {"pipeline": "nf-core/rnaseq", "revision": "3.21.0",
                           "params": {"input": SS, "genome": "R64-1-1"}}}
    out = _enrich_one(step, monkeypatch)
    assert out["prefilled"].get("input") == SS, out["prefilled"]
    assert out["prefilled"].get("genome") == "R64-1-1", out["prefilled"]


def test_reserved_keys_not_leaked_as_params(monkeypatch):
    """Run controls (execution/profile/outdir) must not pollute prefilled."""
    step = {"title": "Run", "skill": "run_nextflow",
            "parameters": {"pipeline": "nf-core/rnaseq", "revision": "3.21.0",
                           "profile": "test", "execution": "local", "input": SS}}
    out = _enrich_one(step, monkeypatch)
    assert out["prefilled"].get("input") == SS
    for reserved in ("profile", "execution", "pipeline", "revision"):
        assert reserved not in out["prefilled"], f"{reserved} leaked into prefilled"
