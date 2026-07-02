"""P1: env_profile() + tool_viable() — the capability snapshot that lets
discovery gate recipes whose declared requires_tools can't run here. run_nextflow
is the one environment-hard tool: viable only with nextflow present AND (a
container engine OR a cluster). run_python/run_r (and unmodeled tools) always viable.
"""
from __future__ import annotations
import os, sys, shutil, tempfile

os.environ["ABA_RUNTIME_DIR"] = tempfile.mkdtemp(prefix="aba_envprof_")
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.exec import compute_env as ce  # noqa: E402


def _which_of(present):
    present = set(present)
    return lambda name: (f"/usr/bin/{name}" if name in present else None)


def test_laptop_no_pipelines(monkeypatch):
    monkeypatch.setattr(shutil, "which", _which_of([]))
    monkeypatch.delenv("ABA_NEXTFLOW_BIN", raising=False)
    monkeypatch.delenv("ABA_NEXTFLOW_MODULE", raising=False)
    p = ce.env_profile(refresh=True)
    assert p["run_nextflow"] is False
    assert ce.tool_viable("run_nextflow", p) is False
    assert ce.tool_viable("run_python", p) is True
    assert ce.tool_viable("run_r", p) is True


def test_cluster_pipelines_viable(monkeypatch):
    monkeypatch.setattr(shutil, "which", _which_of(["nextflow", "apptainer", "sbatch", "sinfo"]))
    p = ce.env_profile(refresh=True)
    assert p["run_nextflow"] is True
    assert p["cluster"] is True and "apptainer" in p["container_engines"]


def test_workstation_container_no_cluster(monkeypatch):
    # container engine alone (no scheduler) is enough to run nf-core locally
    monkeypatch.setattr(shutil, "which", _which_of(["nextflow", "docker"]))
    monkeypatch.delenv("ABA_NEXTFLOW_BIN", raising=False)
    monkeypatch.delenv("ABA_NEXTFLOW_MODULE", raising=False)
    p = ce.env_profile(refresh=True)
    assert p["run_nextflow"] is True


def test_conda_only_laptop_not_viable(monkeypatch):
    # nextflow present but NO container engine and NO cluster → not real pipeline env
    monkeypatch.setattr(shutil, "which", _which_of(["nextflow"]))
    monkeypatch.delenv("ABA_NEXTFLOW_BIN", raising=False)
    monkeypatch.delenv("ABA_NEXTFLOW_MODULE", raising=False)
    p = ce.env_profile(refresh=True)
    assert p["nextflow_present"] is True and p["run_nextflow"] is False


def test_module_configured_cluster(monkeypatch):
    # nextflow via module (not on PATH) + a cluster → viable
    monkeypatch.setattr(shutil, "which", _which_of(["sbatch", "apptainer"]))
    monkeypatch.setenv("ABA_NEXTFLOW_MODULE", "nextflow/26.04.4")
    p = ce.env_profile(refresh=True)
    assert p["nextflow_present"] is True and p["run_nextflow"] is True


def test_custom_bin_env(monkeypatch):
    monkeypatch.setattr(shutil, "which", _which_of(["apptainer", "sbatch"]))
    monkeypatch.setenv("ABA_NEXTFLOW_BIN", "/shared/opt/nextflow")
    p = ce.env_profile(refresh=True)
    assert p["run_nextflow"] is True


def test_unknown_tool_assumed_viable(monkeypatch):
    monkeypatch.setattr(shutil, "which", _which_of([]))
    p = ce.env_profile(refresh=True)
    assert ce.tool_viable("some_future_tool", p) is True
