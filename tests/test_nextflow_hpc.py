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
                              reports_dir="/rep", resume=True, config_file="/etc/cbe.config")
    s = " ".join(cmd)
    assert cmd[:3] == ["nextflow", "run", "nf-core/rnaseq"]
    assert "-r 3.14.0" in s and "-profile test,cbe" in s
    assert "-c /etc/cbe.config" in s
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
    import core.exec.nextflow_schema as _ns
    from core import projects as _proj
    monkeypatch.setattr(pe, "_nextflow_env_blocker", lambda *a, **k: None)   # ignore container-engine gate
    monkeypatch.setattr(_ns, "fetch_schema", lambda *a, **k: None)           # skip P2 schema fetch (no network in unit)
    monkeypatch.setattr(_ns, "latest_release", lambda *a, **k: None)
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


_FIX_SCHEMA = {"definitions": {"io": {"title": "IO", "required": ["input"],
               "properties": {"input": {"type": "string", "description": "samplesheet"},
                              "genome": {"type": "string", "enum": ["GRCh38"]}}}}}


def test_run_nextflow_blocks_invalid_params(monkeypatch):
    # P2: a missing-required param is caught pre-submit — nothing reaches Slurm.
    import content.bio.tools.plan_etc as pe
    import core.jobs.runner as runner
    import core.exec.nextflow_schema as _ns
    from core import projects as _proj
    monkeypatch.setattr(pe, "_nextflow_env_blocker", lambda *a, **k: None)
    monkeypatch.setattr(_ns, "fetch_schema", lambda *a, **k: _FIX_SCHEMA)
    monkeypatch.setattr(_ns, "latest_release", lambda *a, **k: None)
    monkeypatch.setattr(_proj, "current", lambda: "p1", raising=False)
    submitted = {"n": 0}
    monkeypatch.setattr(runner, "submit_nextflow_job",
                        lambda **k: submitted.__setitem__("n", submitted["n"] + 1) or {"id": "j"})
    res = pe.run_nextflow({"pipeline": "nf-core/x", "background": True, "params": {}}, None)
    assert res["status"] == "invalid_params" and any("input" in e for e in res["errors"])
    assert submitted["n"] == 0                              # blocked before submit


def test_describe_pipeline(monkeypatch):
    import content.bio.tools.plan_etc as pe
    import core.exec.nextflow_schema as _ns
    monkeypatch.setattr(_ns, "fetch_schema", lambda *a, **k: _FIX_SCHEMA)
    monkeypatch.setattr(_ns, "latest_release", lambda *a, **k: "1.2.0")
    monkeypatch.setattr(_ns, "fetch_input_schema", lambda *a, **k: {
        "type": "array", "description": "samplesheet",
        "items": {"properties": {"sample": {"type": "string"},
                                 "fastq_1": {"type": "string", "format": "file-path"}},
                  "required": ["sample", "fastq_1"]}})
    r = pe.describe_pipeline({"pipeline": "nf-core/x"}, None)
    assert r["status"] == "ok" and r["required"] == ["input"] and r["latest_release"] == "1.2.0"
    assert "IO" in r["param_groups"] and any(p["name"] == "genome" for p in r["param_groups"]["IO"])
    assert r["input_format"]["required_columns"] == ["sample", "fastq_1"]      # P2.5
    assert any(c["name"] == "fastq_1" and c["format"] == "file-path" for c in r["input_format"]["columns"])
    assert r["docs"]["usage"].endswith("docs/usage.md") and r["docs"]["repo"].startswith("https://github.com/")  # P2.5b


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


# ── P1: monitoring — trace summary / live progress / failure diagnosis ────────
_TRACE = (
    "task_id\thash\tnative_id\tname\tstatus\texit\trealtime\t%cpu\tpeak_rss\n"
    "1\taa/1\t101\tFASTQC (s1)\tCOMPLETED\t0\t2m\t180%\t512 MB\n"
    "2\tbb/2\t102\tSTAR_ALIGN (s1)\tCOMPLETED\t0\t30m\t750%\t12 GB\n"
    "3\tcc/3\t103\tSALMON (s1)\tRUNNING\t-\t-\t-\t-\n"
    "4\tdd/4\t104\tMULTIQC\tSUBMITTED\t-\t-\t-\t-\n"
    "5\tee/5\t105\tTRIM (s2)\tFAILED\t1\t10s\t90%\t256 MB\n"
)


def _rows(tmp_path):
    t = tmp_path / "trace.txt"; t.write_text(_TRACE)
    return nf.parse_trace_rows(t)


def test_parse_trace_rows(tmp_path):
    rows = _rows(tmp_path)
    assert len(rows) == 5 and rows[1]["name"] == "STAR_ALIGN (s1)"
    assert rows[1]["peak_rss"] == "12 GB" and rows[4]["status"] == "FAILED"
    assert nf.parse_trace_rows(tmp_path / "nope.txt") == []


def test_trace_summary(tmp_path):
    s = nf.trace_summary(_rows(tmp_path))
    assert s["total_tasks"] == 5
    assert s["status_counts"]["COMPLETED"] == 2 and s["status_counts"]["FAILED"] == 1
    assert s["peak_process"] == "STAR_ALIGN" and abs(s["peak_rss_mb"] - 12 * 1024) < 1
    assert {f["process"] for f in s["failed"]} == {"TRIM"}      # exit/status flagged, '(s2)' stripped


def test_trace_progress(tmp_path):
    p = nf.trace_progress(_rows(tmp_path))
    assert p["total"] == 5 and p["completed"] == 2 and p["running"] == 1
    assert p["submitted"] == 1 and p["failed"] == 1
    assert p["current"] == ["SALMON"] and p["pct"] == 40.0


def test_parse_failure(tmp_path):
    stderr = ("Foo\nError executing process > 'TRIM (s2)'\nCaused by: exit status 1\n"
              "Command error: trimgalore: not found\n")
    f = nf.parse_failure(stderr, _rows(tmp_path))
    assert "TRIM (exit 1)" in f["failed_processes"]
    assert "Error executing process" in f["error_excerpt"] and "trimgalore" in f["error_excerpt"]


def test_size_mb():
    assert nf._size_mb("12 GB") == 12 * 1024
    assert nf._size_mb("512 MB") == 512 and nf._size_mb("1024 KB") == 1.0
    assert nf._size_mb("-") == 0.0 and nf._size_mb("") == 0.0


# ── P1.5: auto-resume a Slurm-killed Nextflow head (unpredictable head lifetime) ──
class _FakeSub:
    name = "slurm"
    def __init__(self): self.submitted = []
    def submit(self, job): self.submitted.append(job["id"])


def test_resume_on_infra_death():
    from core import projects
    from core.jobs.runner import submit_nextflow_job, _maybe_resume_nextflow_job
    from core.graph.jobs import get_job
    projects.init(); pid = projects.create_project("nfresume1")["id"]
    job = submit_nextflow_job(pipeline="nf-core/rnaseq", title="t", focus_entity_id=None,
                              project_id=pid, run_id="run_r", timeout_s=3600)
    sub = _FakeSub()
    result = {"error": "slurm job TIMEOUT (no result written)", "returncode": 1,
              "slurm_terminal_fail": "TIMEOUT"}
    assert _maybe_resume_nextflow_job(sub, job, result, pid) is True
    assert sub.submitted == [job["id"]]                     # re-submitted
    fresh = get_job(job["id"], project_id=pid)
    assert (fresh["params"].get("nf_resumes")) == 1 and fresh["status"] == "queued"


def test_no_resume_on_pipeline_failure():
    # a result.json that REPORTS a non-zero exit (real pipeline error) → no slurm_terminal_fail
    from core.jobs.runner import _maybe_resume_nextflow_job
    sub = _FakeSub()
    job = {"id": "job_x", "kind": "run_nextflow", "params": {"project_id": "p", "nf_resumes": 0}}
    result = {"returncode": 1, "error": "Nextflow pipeline failed (exit 1). Failed: TRIM."}
    assert _maybe_resume_nextflow_job(sub, job, result, "p") is False
    assert sub.submitted == []                              # NOT resubmitted (don't loop on real errors)


def test_resume_cap_exhausted():
    from core.jobs.runner import _maybe_resume_nextflow_job, _NF_MAX_RESUMES
    sub = _FakeSub()
    job = {"id": "job_y", "kind": "run_nextflow",
           "params": {"project_id": "p", "nf_resumes": _NF_MAX_RESUMES}}
    result = {"error": "slurm job TIMEOUT (no result written)", "returncode": 1,
              "slurm_terminal_fail": "TIMEOUT"}
    assert _maybe_resume_nextflow_job(sub, job, result, "p") is False
    assert sub.submitted == [] and "gave up after" in result["error"]


def test_poll_flags_infra_death(monkeypatch):
    # SlurmSubmitter.poll annotates slurm_terminal_fail when the job is gone from
    # squeue, past the grace, no sentinel, and sacct reports a terminal kill.
    from core.jobs.slurm_submitter import SlurmSubmitter
    s = SlurmSubmitter()
    job = {"id": "job_z", "kind": "run_nextflow",
           "params": {"project_id": "p", "slurm_id": "999", "run_dir": "/nope"}}
    monkeypatch.setattr(s, "_result_from_sentinel", lambda rd: None)
    monkeypatch.setattr(s, "_in_squeue", lambda sid: False)
    monkeypatch.setattr(s, "_too_young", lambda job, **k: False)
    monkeypatch.setattr(s, "_sacct_state", lambda sid: "TIMEOUT")
    r = s.poll(job)
    assert r and r.get("slurm_terminal_fail") == "TIMEOUT" and r["returncode"] == 1


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
