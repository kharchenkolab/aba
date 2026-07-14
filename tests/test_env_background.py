"""run_python(env=…, background=True) — a backgrounded job runs IN the isolated env.

Validates the env threads submit → job params → executor, and that
run_python_code(env=) uses the env's OWN python STANDALONE (no project overlay),
per the gate-drop in run_exec. The cluster path is covered in live_slurm_real.py.
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_envbg_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core import projects                                    # noqa: E402
from core.graph.jobs import get_job                          # noqa: E402
from core.jobs.runner import submit_python_job               # noqa: E402
from core.exec.run import run_python_code, run_r_code         # noqa: E402
from core.compute import named_envs                          # noqa: E402

projects.init()

# Planted prefixes by EnvID — a module-level stand-in for weft realization
# (a real solve needs network + minutes; the dispatch under test is aba's).
_PLANTED: dict[str, Path] = {}
_real_ensure_realized = named_envs.ensure_realized
named_envs.ensure_realized = (
    lambda env_id, **kw: _PLANTED.get(env_id) or _real_ensure_realized(env_id, **kw))


def _plant_named_env(pid: str, name: str, language: str = "python") -> Path:
    """Register a named env handle whose 'realization' is a local venv (python)
    or a stub prefix with bin/Rscript (r) — stands in for a weft realize."""
    d = Path(tempfile.mkdtemp(prefix=f"aba_plant_{name}_"))
    if language == "python":
        subprocess.run([sys.executable, "-m", "venv", str(d)],
                       check=True, capture_output=True)
    else:
        (d / "bin").mkdir(parents=True, exist_ok=True)
        rs = d / "bin" / "Rscript"
        rs.write_text("#!/bin/sh\nexit 0\n")
        rs.chmod(0o755)
    env_id = f"env:v1:test-{pid}-{name}"
    data = named_envs._load(pid)
    data["envs"][name] = {"env_id": env_id, "language": language,
                          "packages": [], "history": [],
                          "created_at": 0, "updated_at": 0}
    named_envs._save(pid, data)
    _PLANTED[env_id] = d
    return d


def test_submit_carries_env():
    pid = projects.create_project("envbg-submit")["id"]
    job = submit_python_job("print(1)", "t", None, project_id=pid, env="myenv")
    assert get_job(job["id"], project_id=pid)["params"]["env"] == "myenv"


def test_run_python_code_uses_isolated_env():
    pid = projects.create_project("envbg-run")["id"]
    projects.set_current(pid)
    envdir = _plant_named_env(pid, "iso1")
    r = run_python_code("import sys; print('PFX', sys.prefix)", project_id=pid,
                        env="iso1", timeout_s=60)
    assert r.get("returncode") == 0, r
    # The job ran with the ENV's python → sys.prefix is the env dir, not the base.
    assert str(envdir) in (r.get("stdout") or ""), r.get("stdout")


def test_isolated_env_is_standalone_no_overlay():
    """An isolated env must NOT get the project pylib overlay on sys.path
    (standalone), unlike the default run."""
    pid = projects.create_project("envbg-standalone")["id"]
    projects.set_current(pid)
    _plant_named_env(pid, "iso2")
    code = ("import sys; "
            "print('OVERLAY' if any('pylib_proj' in p for p in sys.path) else 'CLEAN')")
    r = run_python_code(code, project_id=pid, env="iso2", timeout_s=60)
    assert "CLEAN" in (r.get("stdout") or ""), r.get("stdout")


def test_run_python_code_missing_env_errors():
    pid = projects.create_project("envbg-missing")["id"]
    projects.set_current(pid)
    r = run_python_code("print(1)", project_id=pid, env="nope", timeout_s=30)
    assert "error" in r and "not available" in r["error"], r


def test_default_run_unaffected():
    pid = projects.create_project("envbg-default")["id"]
    projects.set_current(pid)
    r = run_python_code("print('hello-default')", project_id=pid, timeout_s=30)
    assert r.get("returncode") == 0 and "hello-default" in (r.get("stdout") or "")


# ── R isolated env (its lib FIRST on .libPaths(), standalone) ────────────────
def test_submit_r_carries_env():
    from core.jobs.runner import submit_r_job
    pid = projects.create_project("envbg-rsubmit")["id"]
    job = submit_r_job("cat(1)", "t", None, project_id=pid, env="renv")
    assert get_job(job["id"], project_id=pid)["params"]["env"] == "renv"


def test_run_r_code_isolated_env_uses_own_rscript(monkeypatch):
    """run_r_code(env=) runs the NAMED env's own Rscript (a full standalone weft
    env — no .libPaths() stacking) — mocks the executor so no real R runs."""
    pid = projects.create_project("envbg-rpre")["id"]; projects.set_current(pid)
    prefix = _plant_named_env(pid, "renv", language="r")
    import core.exec.run as runmod
    captured = {}
    class _Res:
        timed_out = False; cancelled = False; returncode = 0; stdout = ""; stderr = ""
    def _exec(self, env, argv, **kw):
        captured["argv"] = list(argv)
        captured["script"] = Path(argv[-1]).read_text(); return _Res()
    monkeypatch.setattr(runmod.MaterializingExecutor, "materialize",
                        lambda self, prov: type("E", (), {"python": None})())
    monkeypatch.setattr(runmod.MaterializingExecutor, "exec", _exec)
    run_r_code("cat('hi')", project_id=pid, env="renv", timeout_s=30)
    assert captured["argv"][0] == str(prefix / "bin" / "Rscript"), captured.get("argv")
    assert ".libPaths(c(" not in captured.get("script", "")   # standalone env


def test_run_r_code_missing_env_errors(monkeypatch):
    pid = projects.create_project("envbg-rmiss")["id"]; projects.set_current(pid)
    import core.exec.r as rmod
    fake = Path(tempfile.mktemp() + "_rs"); fake.write_text("x")
    monkeypatch.setattr(rmod, "_rscript", lambda: fake)
    r = run_r_code("cat(1)", project_id=pid, env="nope", timeout_s=20)
    assert "error" in r and "not available" in r["error"], r


# ── Provenance Phase 1: background runs write an exec record ─────────────────
def test_background_run_writes_exec_record():
    """A background-style run gets a full exec record (code + env descriptor +
    produced + seed + kind) so its artifacts become revisable/reproducible."""
    from core.exec.run import run_python_code
    from core.jobs.runner import _write_exec_record_for_job
    from core.graph import exec_records as er
    pid = projects.create_project("prov-bg")["id"]; projects.set_current(pid)
    code = "print('hi'); open('out.csv','w').write('a,b\\n1,2\\n')"
    res = run_python_code(code, project_id=pid, run_id="r-prov", timeout_s=60)
    assert res.get("returncode") == 0, res
    # the executor surfaced the env descriptor + seed for the record
    assert "package_versions" in res and res.get("seed") == 0 and res.get("language") == "python"
    job = {"id": "job_prov", "kind": "run_python", "focus_entity_id": None,
           "params": {"code": code, "thread_id": "t1", "run_id": "r-prov", "project_id": pid}}
    _write_exec_record_for_job(job, res, pid, pid)
    eid = res.get("exec_id")
    assert eid, "exec_id should be injected into the result"
    rec = er.get(eid)
    assert rec is not None
    assert "open('out.csv'" in (rec.get("code") or "")
    assert rec.get("language") == "python" and rec.get("kind") == "script"
    assert rec.get("seed") == 0
    assert isinstance(rec.get("package_versions"), dict)
    assert any(p.get("kind") for p in (rec.get("produced") or [])), "produced recorded"


def test_background_provenance_is_sufficient_to_reproduce():
    """Memory-wipe simulation: given ONLY the exec record a background job wrote
    (no conversation context), the captured code reproduces the figure."""
    from core.exec.run import run_python_code
    from core.jobs.runner import _write_exec_record_for_job
    from core.graph import exec_records as er
    pid = projects.create_project("prov-recover")["id"]; projects.set_current(pid)
    plot_code = ("import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt\n"
                 "plt.scatter(range(10), [x*x for x in range(10)]); plt.title('Original')\n"
                 "plt.savefig('plot.png')")
    res = run_python_code(plot_code, project_id=pid, run_id="r1", timeout_s=120)
    assert res.get("returncode") == 0, res
    assert res.get("plots"), "a figure should be produced"
    job = {"id": "j1", "kind": "run_python", "focus_entity_id": None,
           "params": {"code": plot_code, "thread_id": "t1", "run_id": "r1", "project_id": pid}}
    _write_exec_record_for_job(job, res, pid, pid)
    # ── memory wipe: keep ONLY the exec_id; recover everything from the record ──
    rec = er.get(res["exec_id"])
    recovered_code = rec.get("code")
    assert recovered_code and "plt.scatter" in recovered_code, "code recoverable from provenance"
    res2 = run_python_code(recovered_code, project_id=pid, run_id="r2", timeout_s=120)
    assert res2.get("returncode") == 0 and res2.get("plots"), \
        "reproduction from provenance alone should re-create the figure"


def test_provenance_phase4_5_diff_and_export():
    """Phase 4/5: diff_env reports env delta; export_bundle writes a portable
    reproduction bundle. Uses a background exec record + a pinned figure entity."""
    import sys as _sys
    from core.exec.run import run_python_code
    from core.jobs.runner import _write_exec_record_for_job
    from content.bio.lifecycle.artifacts import pin_artifact
    from content.bio.lifecycle.revisions import diff_env, export_bundle
    pid = projects.create_project("prov-45")["id"]; projects.set_current(pid)
    code = ("import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt\n"
            "plt.plot([0,1,2],[0,1,4]); plt.title('P45'); plt.savefig('p.png')")
    res = run_python_code(code, project_id=pid, run_id="r45", timeout_s=120)
    assert res.get("returncode") == 0 and res.get("plots")
    job = {"id": "j45", "kind": "run_python", "focus_entity_id": None,
           "params": {"code": code, "thread_id": "t1", "run_id": "r45", "project_id": pid}}
    _write_exec_record_for_job(job, res, pid, pid)
    out = pin_artifact(res["exec_id"], "figure", 0, title="P45")
    fid = out["entity_id"]
    # diff_env: env unchanged (same env back-to-back) → n_changed 0
    d = diff_env(fid)
    assert "n_changed" in d and d["n_changed"] == 0, d
    # export_bundle: a portable dir with the code + requirements + record
    b = export_bundle(fid)
    files = set(b["files"])
    assert {"script.py", "requirements.txt", "exec_record.json", "inputs.json", "README.md"} <= files, b


def test_provenance_phase3_envelope_generalizes():
    """Phase 3 foundation: the exec-record envelope holds non-script producers
    uniformly (kind: cli/workflow + engine + params + container env). Full
    Nextflow ingestion is gated on a real producer + the #5 schema sign-off."""
    import tempfile
    from core.graph import exec_records as er
    pid = projects.create_project("prov-env3")["id"]; projects.set_current(pid)
    cwd = tempfile.mkdtemp()
    eid = er.create(thread_id="t", run_id=None, tool_use_id=None, tool_name="run_cli",
                    status="ok", code="samtools sort -o out.bam in.bam",
                    started_at="2026-06-26T00:00:00+00:00", cwd=cwd,
                    payload={"kind": "cli",
                             "engine": {"name": "samtools", "version": "1.20"},
                             "params": {"threads": 4},
                             "env": "sha256:deadbeef",  # a container digest, in the workflow case
                             "inputs": [{"ref": "in.bam", "kind": "file", "fp": "in.bam|1024|123"}],
                             "produced": [{"kind": "file", "idx": 0, "name": "out.bam"}]})
    rec = er.get(eid)
    assert rec["kind"] == "cli" and rec["engine"]["name"] == "samtools"
    assert rec["params"]["threads"] == 4 and rec["inputs"][0]["ref"] == "in.bam"
    assert rec["produced"][0]["name"] == "out.bam"


def test_background_figure_is_revisable():
    """Revision-first primary verb works for a BACKGROUND figure: make_revision
    re-runs modified code from its exec record, producing a linked revision."""
    from core.exec.run import run_python_code
    from core.jobs.runner import _write_exec_record_for_job
    from content.bio.lifecycle.artifacts import pin_artifact
    from content.bio.lifecycle.revisions import make_revision
    pid = projects.create_project("prov-revise")["id"]; projects.set_current(pid)
    code = ("import matplotlib; matplotlib.use('Agg'); import matplotlib.pyplot as plt\n"
            "plt.plot([0,1,2],[0,1,4]); plt.title('Original'); plt.savefig('p.png')")
    res = run_python_code(code, project_id=pid, run_id="rv", timeout_s=120)
    assert res.get("returncode") == 0 and res.get("plots")
    job = {"id": "jrv", "kind": "run_python", "focus_entity_id": None,
           "params": {"code": code, "thread_id": "t1", "run_id": "rv", "project_id": pid}}
    _write_exec_record_for_job(job, res, pid, pid)
    fid = pin_artifact(res["exec_id"], "figure", 0, title="Original")["entity_id"]
    rev = make_revision(fid, code.replace("'Original'", "'Revised'"), thread_id="t1")
    assert not rev.get("error"), rev
    assert rev.get("exec_id"), f"revision should have a new exec record: {rev}"


def test_nextflow_container_trace_parse():
    """Phase 3 container env: _parse_nextflow_containers extracts the unique
    container images Nextflow used from a `-with-trace` TSV."""
    import tempfile
    from pathlib import Path
    from content.bio.tools.plan_etc import _parse_nextflow_containers
    trace = ("task_id\thash\tname\tstatus\tcontainer\n"
             "1\tab/cd\tFASTQC\tCOMPLETED\tquay.io/biocontainers/fastqc:0.12.1\n"
             "2\tef/gh\tMULTIQC\tCOMPLETED\tquay.io/biocontainers/multiqc:1.21\n"
             "3\tij/kl\tFASTQC2\tCOMPLETED\tquay.io/biocontainers/fastqc:0.12.1\n"
             "4\tmn/op\tNOIMG\tCOMPLETED\t-\n")
    p = Path(tempfile.mktemp()); p.write_text(trace)
    assert _parse_nextflow_containers(p) == [
        "quay.io/biocontainers/fastqc:0.12.1", "quay.io/biocontainers/multiqc:1.21"]
    assert _parse_nextflow_containers(Path("/nonexistent")) == []


def test_env_manifest_dedup_roundtrip():
    """provenance.md §3.1: package_versions is stored once content-addressed and
    re-inflated on read — the sidecar stays slim, callers still see the versions."""
    import json, tempfile
    from pathlib import Path
    from core.graph import exec_records as er
    from core.exec.fingerprint import env_fingerprint
    from core.exec.env_manifest import load as manifest_load
    pid = projects.create_project("prov-dedup")["id"]; projects.set_current(pid)
    pkg = {"numpy": "1.26.0", "pandas": "2.2.0", "scipy": "1.13.0"}
    fp = env_fingerprint("3.12.0", pkg)
    eid = er.create(thread_id="t", run_id=None, tool_use_id=None, tool_name="run_python",
                    status="ok", code="x=1", started_at="2026-06-26T00:00:00+00:00",
                    cwd=tempfile.mkdtemp(),
                    payload={"language": "python", "language_version": "3.12.0",
                             "package_versions": pkg, "env_fingerprint": fp})
    rec = er.get(eid)
    raw = json.loads(Path(rec["record_path"]).read_text())
    assert "package_versions" not in raw, "sidecar should be deduped (no inline pkg list)"
    assert raw.get("env_fingerprint") == fp
    assert rec.get("package_versions") == pkg, "get() re-inflates package_versions"
    assert manifest_load(fp).get("package_versions") == pkg, "shared manifest stored once"
