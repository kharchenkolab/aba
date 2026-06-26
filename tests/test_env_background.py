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
