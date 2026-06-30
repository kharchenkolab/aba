"""P0 unit tests for HPC-routed Nextflow (core.exec.nextflow + job wiring).

Covers the pure layer (config resolution, profile merge, command builder, trace
parse) and the job seam (submit_nextflow_job params, run_nextflow background
routing, the kind:workflow exec record). The live Slurm round-trip is a separate
on-cluster script (tests/live_nextflow_hpc.py).

Run: .venv/bin/python -m pytest tests/test_nextflow_hpc.py -q
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_nfhpc_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "h.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
sys.path.insert(0, str(ROOT / "backend"))

from core.exec import nextflow as nf  # noqa: E402


# ── pure: config resolution ──────────────────────────────────────────────────
def test_config_defaults_are_conservative(monkeypatch):
    for k in ("ABA_NEXTFLOW_MODULE", "ABA_NEXTFLOW_PROFILES", "ABA_NEXTFLOW_CACHEDIR",
              "ABA_NEXTFLOW_WORKDIR"):
        monkeypatch.delenv(k, raising=False)
    c = nf.nextflow_config()
    assert c["module"] is None and c["profiles"] == []
    assert c["singularity_cachedir"] is None and c["workdir_root"] is None
    assert c["head"]["cores"] == 2 and c["head"]["walltime_h"] == 24


def test_config_env_overrides(monkeypatch):
    monkeypatch.setenv("ABA_NEXTFLOW_MODULE", "nextflow/24.10.6")
    monkeypatch.setenv("ABA_NEXTFLOW_PROFILES", "cbe,singularity")
    monkeypatch.setenv("ABA_NEXTFLOW_CACHEDIR", "/resources/containers")
    monkeypatch.setenv("ABA_NEXTFLOW_WORKDIR", "/scratch-cbe/users/me/nxf")
    c = nf.nextflow_config()
    assert c["module"] == "nextflow/24.10.6"
    assert c["profiles"] == ["cbe", "singularity"]
    assert c["singularity_cachedir"] == "/resources/containers"
    assert c["workdir_root"] == "/scratch-cbe/users/me/nxf"


# ── pure: profile merge ──────────────────────────────────────────────────────
def test_merged_profile_caller_first_then_site_dedup():
    assert nf.merged_profile("test", ["cbe", "singularity"]) == "test,cbe,singularity"
    assert nf.merged_profile("test,cbe", ["cbe"]) == "test,cbe"          # dedup
    assert nf.merged_profile(None, ["cbe"]) == "cbe"
    assert nf.merged_profile(None, []) is None


# ── pure: command builder ────────────────────────────────────────────────────
def test_command_builder_full():
    cmd = nf.nextflow_command("nf-core/rnaseq", revision="3.14.0", profile="test,cbe",
                              outdir="/out", params={"input": "s.csv"}, work_dir="/wd",
                              reports_dir="/rep", resume=True)
    s = " ".join(cmd)
    assert cmd[:3] == ["nextflow", "run", "nf-core/rnaseq"]
    assert "-r 3.14.0" in s and "-profile test,cbe" in s
    assert "-work-dir /wd" in s and "-resume" in s
    assert "-with-trace /rep/trace.txt" in s and "-with-report /rep/report.html" in s
    assert "--outdir /out" in s and "--input s.csv" in s
    assert "-ansi-log false" in s


def test_command_builder_minimal_no_resume_no_reports():
    cmd = nf.nextflow_command("nextflow-io/hello", outdir="/o", resume=False)
    s = " ".join(cmd)
    assert "-resume" not in s and "-with-trace" not in s and "-work-dir" not in s
    assert "--outdir /o" in s


# ── pure: trace parse ────────────────────────────────────────────────────────
def test_parse_trace_containers(tmp_path):
    t = tmp_path / "trace.txt"
    t.write_text("task_id\tname\tstatus\tcontainer\n"
                 "1\tFASTQC\tCOMPLETED\tquay.io/biocontainers/fastqc:0.12\n"
                 "2\tSALMON\tCOMPLETED\tquay.io/biocontainers/salmon:1.10\n"
                 "3\tDUP\tCOMPLETED\tquay.io/biocontainers/fastqc:0.12\n"          # dup
                 "4\tNONE\tCOMPLETED\t-\n")                                         # skipped
    assert nf.parse_trace_containers(t) == [
        "quay.io/biocontainers/fastqc:0.12", "quay.io/biocontainers/salmon:1.10"]
    assert nf.parse_trace_containers(tmp_path / "missing.txt") == []


# ── job seam: submit_nextflow_job builds the right job row ────────────────────
def test_submit_nextflow_job_params():
    from core import projects
    from core.jobs.runner import submit_nextflow_job
    projects.init()
    pid = projects.create_project("nfsubmit")["id"]
    job = submit_nextflow_job(
        pipeline="nf-core/rnaseq", title="rnaseq run", focus_entity_id=None,
        revision="3.14.0", profile="test,cbe", nf_params={"input": "s.csv"},
        outdir="/out", timeout_s=7200, project_id=pid, thread_id="t1",
        run_id="run_1", estimate={"runtime_min": 30})
    assert job["kind"] == "run_nextflow"
    p = job["params"]
    assert p["pipeline"] == "nf-core/rnaseq" and p["profile"] == "test,cbe"
    assert p["nf_params"] == {"input": "s.csv"} and p["outdir"] == "/out"
    assert p["run_id"] == "run_1" and p["timeout_s"] == 7200
    assert "nextflow run nf-core/rnaseq" in p["code"]      # descriptive, for logs/titling


# ── tool: run_nextflow(background=True) routes to a job ───────────────────────
def test_run_nextflow_background_routes(monkeypatch):
    import content.bio.tools.plan_etc as pe
    import core.jobs.runner as runner
    from core import projects as _proj
    monkeypatch.setattr(pe, "_nextflow_env_blocker", lambda *a, **k: None)   # ignore container-engine gate
    monkeypatch.setattr(_proj, "current", lambda: "p1", raising=False)
    captured = {}

    def fake_submit(**kw):
        captured.update(kw)
        return {"id": "job_abc"}
    monkeypatch.setattr(runner, "submit_nextflow_job", fake_submit)

    res = pe.run_nextflow({"pipeline": "nf-core/rnaseq", "profile": "test,cbe",
                           "background": True, "params": {"input": "x"},
                           "estimated_runtime_min": 20}, None)
    assert res["status"] == "submitted" and res["job_id"] == "job_abc"
    assert captured["pipeline"] == "nf-core/rnaseq" and captured["profile"] == "test,cbe"
    assert captured["nf_params"] == {"input": "x"}
    assert captured["project_id"] == "p1"


def test_run_nextflow_needs_pipeline():
    import content.bio.tools.plan_etc as pe
    r = pe.run_nextflow({"background": True}, None)
    assert r["status"] == "error" and "pipeline" in r["note"]


# ── provenance: the kind:workflow exec record ─────────────────────────────────
def test_workflow_exec_record_written():
    from core import projects
    from core.jobs.runner import _write_workflow_exec_record_for_job
    projects.init()
    pid = projects.create_project("nfprov")["id"]
    job = {"id": "job_w", "kind": "run_nextflow", "focus_entity_id": None,
           "params": {"thread_id": "t1", "run_id": "run_w", "code": "nextflow run X"}}
    result_obj = {
        "returncode": 0, "stdout": "ok", "stderr": "", "cwd": "/scratch/x",
        "plots": [{"url": "/a/u.png", "original_name": "umap.png"}],
        "tables": [], "files": [],
        "workflow": {"engine": {"name": "nextflow", "version": "24.10.6"},
                     "per_process_images": ["quay.io/x:1"], "pipeline": "nf-core/rnaseq",
                     "revision": "3.14.0", "profile": "test,cbe", "params": {},
                     "outputs": ["multiqc_report.html"], "command": "nextflow run nf-core/rnaseq"},
    }
    _write_workflow_exec_record_for_job(job, result_obj, pid, pid)
    assert result_obj.get("exec_id")          # a record was created + injected


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
