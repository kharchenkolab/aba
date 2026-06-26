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
for k in ("ABA_DB_PATH", "ABA_DB_PATH_OVERRIDE"):
    os.environ.pop(k, None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core import projects                                    # noqa: E402
from core.graph.jobs import get_job                          # noqa: E402
from core.jobs.runner import submit_python_job               # noqa: E402
from core.exec.run import run_python_code, run_r_code         # noqa: E402
from core.exec import isolated_env as iso                    # noqa: E402

projects.init()


def _make_venv_env(pid: str, name: str) -> Path:
    """A throwaway venv at the isolated-env path so env_python resolves (stands in
    for a real make_isolated_env build, which needs uv + minutes)."""
    d = iso.env_dir(name, pid)
    d.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", str(d)], check=True, capture_output=True)
    return d


def test_submit_carries_env():
    pid = projects.create_project("envbg-submit")["id"]
    job = submit_python_job("print(1)", "t", None, project_id=pid, env="myenv")
    assert get_job(job["id"], project_id=pid)["params"]["env"] == "myenv"


def test_run_python_code_uses_isolated_env():
    pid = projects.create_project("envbg-run")["id"]
    projects.set_current(pid)
    envdir = _make_venv_env(pid, "iso1")
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
    _make_venv_env(pid, "iso2")
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


def test_run_r_code_isolated_env_preamble(monkeypatch):
    """run_r_code(env=) prepends the isolated R lib to .libPaths() (standalone) —
    mocks Rscript+executor so it runs without a provisioned R."""
    pid = projects.create_project("envbg-rpre")["id"]; projects.set_current(pid)
    lib = iso.r_env_lib("renv", pid); lib.mkdir(parents=True, exist_ok=True)
    import core.exec.run as runmod, core.exec.r as rmod
    fake = Path(tempfile.mktemp() + "_rscript"); fake.write_text("x")
    monkeypatch.setattr(rmod, "_rscript", lambda: fake)
    captured = {}
    class _Res:
        timed_out = False; cancelled = False; returncode = 0; stdout = ""; stderr = ""
    def _exec(self, env, argv, **kw):
        captured["script"] = Path(argv[-1]).read_text(); return _Res()
    monkeypatch.setattr(runmod.MaterializingExecutor, "materialize",
                        lambda self, prov: type("E", (), {"python": None})())
    monkeypatch.setattr(runmod.MaterializingExecutor, "exec", _exec)
    run_r_code("cat('hi')", project_id=pid, env="renv", timeout_s=30)
    s = captured.get("script", "")
    assert str(lib) in s and ".libPaths(c(" in s, s


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
    assert {"script.py", "requirements.txt", "exec_record.json", "README.md"} <= files, b


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
