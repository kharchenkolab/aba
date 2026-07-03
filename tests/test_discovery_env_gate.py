"""P2: capability-aware discovery gate. search_skills demotes ('soft'), drops
('hard'), or ignores ('off') recipes whose declared requires_tools can't run
here. Environment fit comes from compute_env.tool_viable (monkeypatched here to
simulate a laptop where run_nextflow isn't viable). Registry is snapshotted +
restored so we don't disturb the process-wide skill catalog."""
from __future__ import annotations
import os, sys, tempfile

os.environ["ABA_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="aba_gate_")
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import pytest  # noqa: E402
import core.skills.loader as L  # noqa: E402
from core.skills.loader import (SkillSpec, register_skill_spec, search_skills,  # noqa: E402
                                 unmet_tools, _apply_env_gate, _env_gate_policy)
from core.exec import compute_env as ce  # noqa: E402

NF = SkillSpec(name="bp-variants", requires_tools=("run_nextflow",), visibility="local",
               domain="genomics", description="germline variant calling pipeline",
               when_to_use="call variants from WGS genomics", keywords=("variant", "snp", "genomics"))
PY1 = SkillSpec(name="scrna-cluster", requires_tools=("run_python",), visibility="local",
                domain="genomics", description="cluster single cell rna genomics",
                when_to_use="cluster scRNA genomics", keywords=("cluster", "scrna", "genomics"))
PY2 = SkillSpec(name="bulk-de", requires_tools=("run_python",), visibility="local",
                domain="genomics", description="differential expression genomics",
                when_to_use="DE analysis genomics", keywords=("differential", "expression", "genomics"))


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    saved = dict(L._REGISTRY)
    L._REGISTRY.clear(); L._INDEX = None
    for s in (NF, PY1, PY2):
        register_skill_spec(s)
    # laptop by default: run_nextflow not viable
    monkeypatch.setattr(ce, "tool_viable", lambda t, profile=None: t != "run_nextflow")
    monkeypatch.delenv("ABA_DISCOVERY_ENV_GATE", raising=False)
    yield
    L._REGISTRY.clear(); L._REGISTRY.update(saved); L._INDEX = None


def test_unmet_tools():
    assert unmet_tools(NF) == ["run_nextflow"]      # laptop: not viable
    assert unmet_tools(PY1) == []


def test_apply_env_gate_off_soft_hard():
    pool = [NF, PY1, PY2]
    assert _apply_env_gate(pool, "off") == pool                       # unchanged
    soft = _apply_env_gate(pool, "soft")
    assert soft.index(PY1) < soft.index(NF) and soft.index(PY2) < soft.index(NF)  # runnable first
    assert NF in soft                                                 # still present
    assert _apply_env_gate(pool, "hard") == [PY1, PY2]                # blocked dropped


def test_search_hard_drops_pipeline():
    names = [s.name for s in search_skills("genomics", env_gate="hard")]
    assert "bp-variants" not in names and "bulk-de" in names


def test_search_soft_demotes_pipeline():
    names = [s.name for s in search_skills("genomics", env_gate="soft")]
    assert "bp-variants" in names                                     # present
    assert names.index("bp-variants") > names.index("scrna-cluster")  # after runnable
    assert names.index("bp-variants") > names.index("bulk-de")


def test_search_off_keeps_pipeline():
    names = [s.name for s in search_skills("germline variant WGS", env_gate="off")]
    assert "bp-variants" in names                                     # not gated


def test_cluster_env_no_gating(monkeypatch):
    monkeypatch.setattr(ce, "tool_viable", lambda t, profile=None: True)  # everything viable
    assert unmet_tools(NF) == []
    names = [s.name for s in search_skills("genomics", env_gate="soft")]
    assert "bp-variants" in names                                     # soft is a no-op here


def test_empty_query_gated():
    names = [s.name for s in search_skills("", env_gate="hard")]
    assert "bp-variants" not in names                                 # empty-query path gated too


def test_policy_resolver(monkeypatch):
    monkeypatch.delenv("ABA_DISCOVERY_ENV_GATE", raising=False)
    assert _env_gate_policy() == "soft"                               # default
    monkeypatch.setenv("ABA_DISCOVERY_ENV_GATE", "auto"); assert _env_gate_policy() == "soft"
    monkeypatch.setenv("ABA_DISCOVERY_ENV_GATE", "off");  assert _env_gate_policy() == "off"
    monkeypatch.setenv("ABA_DISCOVERY_ENV_GATE", "hard"); assert _env_gate_policy() == "hard"
    monkeypatch.setenv("ABA_DISCOVERY_ENV_GATE", "bogus"); assert _env_gate_policy() == "soft"
