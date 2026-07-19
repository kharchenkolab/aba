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
    monkeypatch.setenv("ABA_NEXTFLOW_JAVA_HOME", "/sw/java/21")
    c = nf.nextflow_config()
    assert c["module"] == "nextflow/24.10.6"
    assert c["profiles"] == ["cbe", "singularity"]
    assert c["singularity_cachedir"] == "/resources/containers"
    assert c["workdir_root"] == "/scratch-cbe/users/me/nxf"
    assert c["java_home"] == "/sw/java/21"
    # head resources default when unset
    assert c["head"]["walltime_h"] == 24 and c["head"]["cores"] == 2


def test_head_resource_env_overrides(monkeypatch):
    monkeypatch.setenv("ABA_NEXTFLOW_HEAD_WALLTIME_H", "8")
    monkeypatch.setenv("ABA_NEXTFLOW_HEAD_CORES", "4")
    monkeypatch.setenv("ABA_NEXTFLOW_HEAD_MEM_GB", "16")
    monkeypatch.setenv("ABA_NEXTFLOW_HEAD_QOS", "short")
    monkeypatch.setenv("ABA_NEXTFLOW_HEAD_PARTITION", "c")
    h = nf.nextflow_config()["head"]
    assert h["walltime_h"] == 8 and h["cores"] == 4 and h["mem_gb"] == 16
    assert h["qos"] == "short" and h["partition"] == "c"
    # a non-numeric override is ignored (keeps the default) rather than crashing
    monkeypatch.setenv("ABA_NEXTFLOW_HEAD_WALLTIME_H", "lots")
    assert nf.nextflow_config()["head"]["walltime_h"] == 24


def test_local_execution_block_and_default_mode(monkeypatch):
    c = nf.nextflow_config()
    assert c["execution"] == "slurm"                         # fan-out is the default
    assert c["local"]["cores"] == 8 and c["local"]["mem_gb"] == 32   # bigger than head
    monkeypatch.setenv("ABA_NEXTFLOW_EXECUTION", "local")
    monkeypatch.setenv("ABA_NEXTFLOW_LOCAL_CORES", "16")
    monkeypatch.setenv("ABA_NEXTFLOW_LOCAL_MEM_GB", "64")
    c2 = nf.nextflow_config()
    assert c2["execution"] == "local"
    assert c2["local"]["cores"] == 16 and c2["local"]["mem_gb"] == 64
    monkeypatch.setenv("ABA_NEXTFLOW_EXECUTION", "bogus")     # invalid → safe fallback
    assert nf.nextflow_config()["execution"] == "slurm"


def test_local_executor_config_and_extra_c_ordering():
    snip = nf.local_executor_config(8, 32)
    assert "executor = 'local'" in snip
    assert "cpus = 8" in snip and "memory = '32 GB'" in snip
    assert "resourceLimits = [ cpus: 8, memory: 32.GB" in snip   # clamps oversized requests
    # the local override is a SECOND -c, after the base config, so it wins
    cmd = nf.nextflow_command("nf-core/rnaseq", outdir="/o", config_file="/cbe.config",
                              extra_configs=["/local.config"])
    i_cbe, i_loc = cmd.index("/cbe.config"), cmd.index("/local.config")
    assert cmd[i_cbe - 1] == "-c" and cmd[i_loc - 1] == "-c" and i_loc > i_cbe


def test_clear_stale_reports_unblocks_resume(tmp_path):
    # A prior run's report files would make Nextflow abort at startup on -resume.
    rd = tmp_path / "nf_reports"; rd.mkdir()
    for f in ("trace.txt", "report.html", "timeline.html", "dag.dot"):
        (rd / f).write_text("old")
    (rd / "keep.txt").write_text("unrelated")
    nf.clear_stale_reports(rd)
    assert not (rd / "trace.txt").exists() and not (rd / "report.html").exists()
    assert not (rd / "timeline.html").exists() and not (rd / "dag.dot").exists()
    assert (rd / "keep.txt").exists()          # only nextflow's own report files are removed
    nf.clear_stale_reports(rd)                  # idempotent / no error on empty


def test_head_timeout_tracks_walltime_not_estimate(monkeypatch):
    # The head app-timeout = generous walltime + margin (NOT a runtime estimate), so a head
    # whose tasks are merely queued on a busy cluster does not self-kill.
    assert nf.head_timeout_s({"walltime_h": 24}) == 24 * 3600 + 1800
    assert nf.head_timeout_s({"walltime_h": 8}) == 8 * 3600 + 1800
    assert nf.head_timeout_s({}) == 24 * 3600 + 1800            # default walltime
    # picks up the env override path via nextflow_config when no head passed
    monkeypatch.setenv("ABA_NEXTFLOW_HEAD_WALLTIME_H", "8")
    assert nf.head_timeout_s() == 8 * 3600 + 1800


def test_java_env_prepends_without_shadowing():
    # Reproduces the CBE case: the nextflow module pins Java 11 and puts its lib on
    # LD_LIBRARY_PATH; our Java 21 must win without dropping the rest of the path.
    base = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/software/2020/software/java/11.0.2/lib"}
    e = nf.java_env("/sw/java/21", base=base)
    assert e["JAVA_HOME"] == "/sw/java/21"
    assert e["PATH"] == "/sw/java/21/bin:/usr/bin"
    assert e["LD_LIBRARY_PATH"] == "/sw/java/21/lib:/sw/java/21/lib/server:" \
        "/software/2020/software/java/11.0.2/lib"
    # no java_home → no overrides at all (use the module/PATH Java)
    assert nf.java_env(None) == {}
    assert nf.java_env("") == {}
    # empty base → set dirs cleanly, no dangling separators
    e2 = nf.java_env("/sw/java/21", base={})
    assert e2["PATH"] == "/sw/java/21/bin"
    assert e2["LD_LIBRARY_PATH"] == "/sw/java/21/lib:/sw/java/21/lib/server"


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
def test_workflow_exec_record_written(tmp_path):
    from core import projects
    from core.jobs.runner import _write_workflow_exec_record_for_job
    projects.init()
    pid = projects.create_project("nfprov")["id"]
    job = {"id": "job_w", "kind": "run_nextflow", "focus_entity_id": None,
           "params": {"thread_id": "t1", "run_id": "run_w", "code": "nextflow run X"}}
    result_obj = {
        # cwd must be WRITABLE — the record sidecar lands under it (a fake
        # /scratch/x silently failed the best-effort writer on macOS)
        "returncode": 0, "stdout": "ok", "stderr": "", "cwd": str(tmp_path),
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


def test_parse_failure_abort_cause_from_log(tmp_path):
    # Pre-task abort (bad --input): nf-core prints only 'check the log' to stderr; the DETAILS are
    # in .nextflow.log after 'Session aborted -- Cause:'. parse_failure must surface them (cleaned
    # of ANSI + the trailing stack) so the agent isn't left with a useless 'unknown process'.
    log = tmp_path / ".nextflow.log"
    log.write_text(
        "Jul-01 DEBUG nextflow.Session - Session aborted -- Cause: \x1b[0;31mThe following invalid "
        "input values have been detected:\n\n* --input (https://x/s.csv): the file or directory "
        "'https://x/s.csv' does not exist\x1b[0m\n"
        "Thread[#33,process reaper]\n  java.base/jdk.internal...\n")
    f = nf.parse_failure("ERROR ~ Validation of pipeline parameters failed -- Check '.nextflow.log'",
                         rows=[], log_path=str(log))
    assert f["failed_processes"] == []                            # no task ran
    assert "--input" in f["abort_cause"] and "does not exist" in f["abort_cause"]
    assert "Thread[" not in f["abort_cause"] and "\x1b[" not in f["abort_cause"]  # stack + ANSI trimmed


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


def test_poll_flags_infra_death(monkeypatch, tmp_path):
    # The nextflow head runs as a WEFT task now; WeftSubmitter.poll flags
    # slurm_terminal_fail when the task died at the scheduler level (terminal state,
    # no result.json) so the poll loop auto-resumes it (-resume from the work-dir).
    import core.jobs.weft_submitter as WM
    from core.jobs.weft_submitter import WeftSubmitter
    sub = WeftSubmitter(site="cluster")
    monkeypatch.setattr(sub, "_run_dir", lambda job: tmp_path)        # no result.json here
    monkeypatch.setattr(sub, "_compute_block", lambda wid, state: {"substrate": "weft"})
    # this scenario IS the shared-fs lane: declare the site's contract (an
    # undeclared non-local site now falls back to the detached branch at poll
    # — the safe default for the transport-honesty fix)
    monkeypatch.setattr(WM, "site_contract", lambda s: "shared-fs")

    class _Fake:
        def sync_call(self, name, *a, **k):
            return [{"state": "FAILED"}] if name == "task_status" else {}
    monkeypatch.setattr(WM, "_adapter", lambda: _Fake())

    r = sub.poll({"id": "job_z", "kind": "run_nextflow",
                  "params": {"project_id": "p", "weft_id": "jb_9"}})
    assert r and r.get("slurm_terminal_fail") == "FAILED"
    # a NON-nextflow infra death is NOT auto-resume-flagged
    r2 = sub.poll({"id": "job_y", "kind": "run_python",
                   "params": {"project_id": "p", "weft_id": "jb_8"}})
    assert r2 and "slurm_terminal_fail" not in r2


# ── P3b: MultiQC interpretation ───────────────────────────────────────────────
def _write_multiqc(tmp_path):
    """A trimmed multiqc_data.json under <out>/multiqc/multiqc_data/, like nf-core writes."""
    import json
    out = tmp_path / "results"
    d = out / "multiqc" / "multiqc_data"; d.mkdir(parents=True)
    (out / "multiqc" / "multiqc_report.html").write_text("<html>report</html>")
    data = {
        "report_general_stats_headers": [
            {"percent_duplicates": {"title": "% Dups", "description": "% Duplicate Reads"}},
            {"percent_aligned": {"title": "% Aligned", "description": "% Aligned reads"}},
        ],
        "report_general_stats_data": [
            {"WT_REP1": {"percent_duplicates": 12.3}, "WT_REP2": {"percent_duplicates": 11.8},
             "WT_REP3": {"percent_duplicates": 12.0}, "WT_REP4": {"percent_duplicates": 12.5},
             "BAD": {"percent_duplicates": 80.0}},                       # outlier
            {"WT_REP1": {"percent_aligned": 95.1}, "WT_REP2": {"percent_aligned": 96.0},
             "WT_REP3": {"percent_aligned": 95.5}, "WT_REP4": {"percent_aligned": 94.8},
             "BAD": {"percent_aligned": 41.0}},                          # outlier
        ],
        "report_data_sources": {"FastQC": {}, "STAR": {}, "Salmon": {}},
    }
    (d / "multiqc_data.json").write_text(json.dumps(data))
    return out


def test_parse_multiqc(tmp_path):
    out = _write_multiqc(tmp_path)
    mq = nf.parse_multiqc(out)
    assert mq["n_samples"] == 5
    assert sorted(m["title"] for m in mq["metrics"]) == ["% Aligned", "% Dups"]
    assert mq["samples"]["WT_REP1"]["% Dups"] == 12.3 and mq["samples"]["BAD"]["% Aligned"] == 41.0
    assert set(mq["tools"]) == {"FastQC", "STAR", "Salmon"}
    assert mq["report"] == "multiqc/multiqc_report.html"
    # the BAD sample is a >2σ outlier on BOTH metrics
    bad = {(o["sample"], o["metric"]) for o in mq["outliers"]}
    assert ("BAD", "% Dups") in bad and ("BAD", "% Aligned") in bad
    assert all(o["sample"] == "BAD" for o in mq["outliers"])             # only the outlier flagged


def test_parse_multiqc_absent(tmp_path):
    assert nf.parse_multiqc(tmp_path) == {}                              # no multiqc_data.json → {}


def test_nextflow_job_progress(tmp_path):
    # Live progress read from the run's trace.txt (under the project scratch), for the Jobs card.
    from core.data.workspace import project_work_dir
    pid, rid = "prj_prog", "run_prog"
    rep = project_work_dir(pid) / rid / "nf_reports"
    rep.mkdir(parents=True, exist_ok=True)
    (rep / "trace.txt").write_text(
        "task_id\tname\tstatus\texit\n"
        "1\tNFCORE:RNASEQ:STAR_ALIGN (s1)\tCOMPLETED\t0\n"
        "2\tNFCORE:RNASEQ:SALMON_QUANT (s1)\tCOMPLETED\t0\n"
        "3\tNFCORE:RNASEQ:BAM_RSEQC:RSEQC_BAMSTAT (s2)\tRUNNING\t-\n"
        "4\tNFCORE:RNASEQ:MULTIQC (s2)\tSUBMITTED\t-\n")
    job = {"id": "job_x", "kind": "run_nextflow",
           "params": {"pipeline": "nf-core/rnaseq", "run_id": rid, "project_id": pid}}
    p = nf.nextflow_job_progress(job)
    assert p["total"] == 4 and p["completed"] == 2 and p["running"] == 1 and p["submitted"] == 1
    assert p["current"] == ["NFCORE:RNASEQ:BAM_RSEQC:RSEQC_BAMSTAT"]     # (s2) tag stripped
    assert "MULTIQC" in p["latest"]                                      # last row's process
    # non-pipeline job, or no trace yet → {} (card shows nothing, no crash)
    assert nf.nextflow_job_progress({"id": "j", "kind": "run_python", "params": {}}) == {}
    assert nf.nextflow_job_progress(
        {"id": "j2", "kind": "run_nextflow", "params": {"pipeline": "x", "run_id": "nope", "project_id": pid}}) == {}


def _write_multiqc_data(tmp_path, data):
    """Write an arbitrary multiqc_data.json (for the outlier / dedup edge cases)."""
    import json
    out = tmp_path / "results"
    d = out / "multiqc" / "multiqc_data"; d.mkdir(parents=True)
    (d / "multiqc_data.json").write_text(json.dumps(data))
    return out


def test_parse_multiqc_mad_zero_lone_outlier(tmp_path):
    # 5 samples identical → MAD collapses to 0. The old `if mad > 0` guard SKIPPED the whole
    # metric, so a real lone outlier was MISSED. The mean-abs-deviation fallback catches it.
    vals = {"S1": 50.0, "S2": 50.0, "S3": 50.0, "S4": 50.0, "S5": 50.0, "BAD": 500.0}
    data = {"report_general_stats_headers": [{"m": {"title": "Reads M"}}],
            "report_general_stats_data": [{s: {"m": v} for s, v in vals.items()}]}
    mq = nf.parse_multiqc(_write_multiqc_data(tmp_path, data))
    assert {o["sample"] for o in mq["outliers"]} == {"BAD"}, mq["outliers"]


def test_parse_multiqc_tiny_diff_not_flagged(tmp_path):
    # Same tie pattern, but the odd sample differs TRIVIALLY (50.00 vs 50.01). The old MAD form
    # blew the z-score up and flagged it; the 1%-of-median gate now treats it as noise.
    vals = {"S1": 50.0, "S2": 50.0, "S3": 50.0, "S4": 50.0, "S5": 50.0, "S6": 50.01}
    data = {"report_general_stats_headers": [{"m": {"title": "Reads M"}}],
            "report_general_stats_data": [{s: {"m": v} for s, v in vals.items()}]}
    mq = nf.parse_multiqc(_write_multiqc_data(tmp_path, data))
    assert mq["outliers"] == [], mq["outliers"]


def test_scale_direction_helper():
    from core.exec.nextflow import _scale_direction
    assert _scale_direction("RdYlGn") == 1           # green-at-high → higher is better
    assert _scale_direction("OrRd") == -1            # red-at-high → higher is worse
    assert _scale_direction("RdYlGn-rev") == -1      # -rev flips
    assert _scale_direction("OrRd-rev") == 1
    assert _scale_direction("RdBu") is None          # neutral diverging → no direction
    assert _scale_direction("") is None and _scale_direction(None) is None


def test_parse_multiqc_outlier_concern_from_direction(tmp_path):
    # 2.5: directionality from the `scale` field. An outlier on the metric's BAD side is a
    # concern; an outlier in the FAVORABLE direction is flagged notable but concern=False.
    data = {
        "report_general_stats_headers": [
            {"dups": {"title": "% Dups", "scale": "OrRd"}},           # higher is worse
            {"aligned": {"title": "% Aligned", "scale": "RdYlGn"}},   # higher is better
        ],
        "report_general_stats_data": [
            {"S1": {"dups": 10.0}, "S2": {"dups": 10.2}, "S3": {"dups": 9.8},
             "S4": {"dups": 10.1}, "BAD": {"dups": 80.0}},            # high on higher-worse → concern
            {"S1": {"aligned": 95.0}, "S2": {"aligned": 95.1}, "S3": {"aligned": 94.9},
             "S4": {"aligned": 95.0}, "HI": {"aligned": 99.9}},       # high on higher-better → good
        ],
    }
    mq = nf.parse_multiqc(_write_multiqc_data(tmp_path, data))
    dirs = {m["title"]: m["direction"] for m in mq["metrics"]}
    assert dirs["% Dups"] == "higher_worse" and dirs["% Aligned"] == "higher_better"
    by = {(o["sample"], o["metric"]): o for o in mq["outliers"]}
    assert by[("BAD", "% Dups")]["concern"] is True and by[("BAD", "% Dups")]["side"] == "high"
    assert by[("HI", "% Aligned")]["concern"] is False       # favorable outlier, not a concern
    assert by[("HI", "% Aligned")]["side"] == "high"


def test_parse_multiqc_outlier_no_direction_when_scale_neutral(tmp_path):
    # No usable scale → concern is None (agent judges from value + docs), still flagged.
    vals = {"S1": 50.0, "S2": 50.1, "S3": 49.9, "S4": 50.0, "BAD": 500.0}
    data = {"report_general_stats_headers": [{"m": {"title": "Reads M", "scale": "Blues"}}],
            "report_general_stats_data": [{s: {"m": v} for s, v in vals.items()}]}
    mq = nf.parse_multiqc(_write_multiqc_data(tmp_path, data))
    o = next(o for o in mq["outliers"] if o["sample"] == "BAD")
    assert o["concern"] is None and o["side"] == "high"
    assert mq["metrics"][0]["direction"] is None


def test_parse_multiqc_cross_tool_title_dedup(tmp_path):
    # Two general-stats columns share the title "% Dups" (FastQC vs Picard). They must NOT
    # collide in the per-sample table (one silently overwriting the other) — disambiguate by ns.
    data = {
        "report_general_stats_headers": [
            {"fastqc_dups": {"title": "% Dups", "namespace": "FastQC"}},
            {"picard_dups": {"title": "% Dups", "namespace": "Picard"}},
        ],
        "report_general_stats_data": [
            {"S1": {"fastqc_dups": 10.0}, "S2": {"fastqc_dups": 11.0}},
            {"S1": {"picard_dups": 20.0}, "S2": {"picard_dups": 22.0}},
        ],
    }
    mq = nf.parse_multiqc(_write_multiqc_data(tmp_path, data))
    assert sorted(m["title"] for m in mq["metrics"]) == ["% Dups (FastQC)", "% Dups (Picard)"]
    assert mq["samples"]["S1"]["% Dups (FastQC)"] == 10.0          # both retained, no overwrite
    assert mq["samples"]["S1"]["% Dups (Picard)"] == 20.0


def test_parse_multiqc_modern_dict_format(tmp_path):
    # Modern MultiQC keys general-stats by MODULE (a dict), not a list of blocks. parse_multiqc
    # must handle it (older versions → 'n_samples: 0', QC silently empty), and a column repeated
    # across modules (FastQC raw vs trimmed 'pct_dups') must stay distinct, not overwrite.
    data = {
        "report_general_stats_headers": {
            "fastqc_raw": {"pct_dups": {"title": "% Dups", "namespace": "FastQC (raw)", "scale": "OrRd"}},
            "fastqc_trimmed": {"pct_dups": {"title": "% Dups", "namespace": "FastQC (trimmed)", "scale": "OrRd"}},
            "star": {"pct_aligned": {"title": "% Aligned", "namespace": "STAR", "scale": "RdYlGn"}},
        },
        "report_general_stats_data": {
            "fastqc_raw": {"S1": {"pct_dups": 30.0}, "S2": {"pct_dups": 31.0},
                           "S3": {"pct_dups": 29.0}, "S4": {"pct_dups": 30.5}},
            "fastqc_trimmed": {"S1": {"pct_dups": 12.0}, "S2": {"pct_dups": 11.5},
                               "S3": {"pct_dups": 12.5}, "S4": {"pct_dups": 12.2}},
            "star": {"S1": {"pct_aligned": 95.0}, "S2": {"pct_aligned": 96.0},
                     "S3": {"pct_aligned": 95.5}, "S4": {"pct_aligned": 94.0}},
        },
        "report_data_sources": {"FastQC": {}, "STAR": {}},
    }
    mq = nf.parse_multiqc(_write_multiqc_data(tmp_path, data))
    assert mq["n_samples"] == 4                                     # dict format parsed (was 0)
    assert sorted(m["title"] for m in mq["metrics"]) == \
        ["% Aligned", "% Dups (FastQC (raw))", "% Dups (FastQC (trimmed))"]
    assert mq["samples"]["S1"]["% Dups (FastQC (raw))"] == 30.0     # raw kept
    assert mq["samples"]["S1"]["% Dups (FastQC (trimmed))"] == 12.0 # trimmed kept, not overwritten
    dirs = {m["title"]: m["direction"] for m in mq["metrics"]}
    assert dirs["% Aligned"] == "higher_better" and dirs["% Dups (FastQC (raw))"] == "higher_worse"


def test_publish_multiqc_report(tmp_path):
    # Copies the run's self-contained multiqc_report.html into the project artifacts store under
    # a deterministic name and returns a servable /artifacts URL — so the agent links a CLICKABLE
    # report, not a dead file:// path.
    from core.exec.nextflow import publish_multiqc_report
    from core.config import project_artifacts_dir
    rep = tmp_path / "results" / "multiqc" / "star_salmon"
    rep.mkdir(parents=True)
    (rep / "multiqc_report.html").write_text("<html>report</html>")
    url = publish_multiqc_report(tmp_path / "results", "prj_pub", "run_pub")
    assert url == "/artifacts/prj_pub/multiqc-run_pub.html"
    assert (project_artifacts_dir("prj_pub") / "multiqc-run_pub.html").read_text() == "<html>report</html>"
    assert publish_multiqc_report(tmp_path / "no_such_dir", "prj_pub", "run_pub") is None  # no report → None


def test_inline_silence_gate(tmp_path, monkeypatch):
    # The hang watchdog's FS-only gate (nextflow_inline_silence): a run is a "suspect" ONLY when
    # it has a RUNNING task (.command.begin, no .exitcode) AND has gone silent past the budget.
    # A run with no tasks, a freshly-active task, or only finished tasks is NOT flagged — this is
    # what keeps a slow-but-healthy pipeline from being killed.
    import os as _os
    from core.exec import nextflow as nf
    run_id = "sil1"
    monkeypatch.setenv("ABA_NEXTFLOW_WORKDIR", str(tmp_path))
    monkeypatch.setattr(nf, "_INLINE_STALL_MIN", 20)
    job = {"kind": "run_nextflow", "id": "j1", "params": {"run_id": run_id, "project_id": "p"}}
    wd = tmp_path / run_id
    wd.mkdir()
    assert nf.nextflow_work_dir(job) == wd            # resolves via ABA_NEXTFLOW_WORKDIR/<run_id>
    assert nf.nextflow_inline_silence(job) is None    # no task dirs yet → not suspect

    t0 = 1_000_000.0
    d = wd / "aa" / "bbbbccccdddd"; d.mkdir(parents=True)
    (d / ".command.begin").write_text("x"); (d / ".command.log").write_text("running")
    _os.utime(d / ".command.begin", (t0, t0)); _os.utime(d / ".command.log", (t0, t0))
    assert nf.nextflow_inline_silence(job, now=t0 + 60) is None        # 1 min idle → progressing
    s = nf.nextflow_inline_silence(job, now=t0 + 21 * 60)              # 21 min idle → suspect
    assert s and s["running_tasks"] == 1 and s["idle_min"] >= 20

    (d / ".exitcode").write_text("0"); _os.utime(d / ".exitcode", (t0, t0))
    assert nf.nextflow_inline_silence(job, now=t0 + 21 * 60) is None   # task finished → not running


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
