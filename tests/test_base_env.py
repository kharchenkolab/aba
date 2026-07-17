"""W3.0 (weft rewrite): the default env lanes follow a bundle-declared BASE
pack — data-driven, generic. No pack declared → the served base, byte for
byte (today's behavior). A pack declared (envs/ facet, role: base) → the
default python/R kernels and one-shot runs use the pack's realized
interpreter, standalone (no overlay path-stacking; additions layer via
extends_env). Declared-but-unavailable is LOUD (ComputeError), never a
silent downgrade.
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_baseenv_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")   # weft workspace derives here
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core import projects  # noqa: E402
from core.bundle.loader import EnvPack  # noqa: E402
from core.compute import base_env  # noqa: E402

projects.init()


class _FakeBundle:
    def __init__(self, packs):
        self.env_packs = packs


def _pack(name, languages, *, role="base", deps=None):
    return EnvPack(name, {
        "name": name, "languages": languages, "role": role,
        "spec": {"deps": deps or {"conda": ["python =3.12", "ipykernel"]}},
    }, "system")


@pytest.fixture(autouse=True)
def _fresh_cache():
    base_env.reset_cache()
    yield
    base_env.reset_cache()


@pytest.fixture()
def no_packs(monkeypatch):
    import core.bundle.active as active
    monkeypatch.setattr(active, "get_bundle", lambda: _FakeBundle([]))


@pytest.fixture()
def generic_packs(monkeypatch):
    import core.bundle.active as active
    monkeypatch.setattr(active, "get_bundle", lambda: _FakeBundle([
        _pack("lang-python-base", ["python"]),
        _pack("lang-r-base", ["r"], deps={"conda": ["r-base =4.4.*", "r-irkernel"]}),
        _pack("helper-pack", ["python"], role="library"),   # not a base — ignored
    ]))


# ── resolution ───────────────────────────────────────────────────────────────

def test_no_pack_means_served_base(no_packs):
    assert base_env.pack_name("python") is None
    assert base_env.active("python") is False
    assert base_env.env_id("python") is None
    assert base_env.interpreter("python") is None


def test_pack_resolution_by_role_and_language(generic_packs):
    assert base_env.pack_name("python") == "lang-python-base"   # role=base only
    assert base_env.pack_name("r") == "lang-r-base"
    assert base_env.pack_name("julia") is None


def test_multiple_bases_warn_and_pick_deterministically(monkeypatch, capsys):
    import core.bundle.active as active
    monkeypatch.setattr(active, "get_bundle", lambda: _FakeBundle([
        _pack("zeta-base", ["python"]), _pack("alpha-base", ["python"])]))
    assert base_env.pack_name("python") == "alpha-base"          # sorted-first
    assert "2 base packs" in capsys.readouterr().out
    base_env.pack_name("python")                                  # warn ONCE
    assert "base packs" not in capsys.readouterr().out


def test_declared_but_substrate_offline_is_loud(generic_packs, monkeypatch):
    import core.compute.adapter as ad
    from core.compute.errors import ComputeError
    monkeypatch.setattr(ad, "_adapter", None)
    monkeypatch.setattr(ad, "_status", {"ok": False, "severity": "warning",
                                        "detail": "down for test"})
    assert base_env.active("python") is True     # declared → the deployment intends it
    with pytest.raises(ComputeError):
        base_env.env_id("python")                # …so unavailability RAISES


def test_env_id_cached_per_spec(generic_packs, monkeypatch):
    calls = []

    class _Stub:
        async def env_ensure(self, spec, **kw):
            calls.append(spec)
            return {"env_id": "env:v1:stub", "status": "cached"}
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "get_compute", lambda: _Stub())
    assert base_env.env_id("python") == "env:v1:stub"
    assert base_env.env_id("python") == "env:v1:stub"
    assert len(calls) == 1                        # solve consulted once


# ── the default one-shot lane follows the pack ───────────────────────────────

def _plant_prefix(with_ipykernel: bool = True) -> Path:
    d = Path(tempfile.mkdtemp(prefix="aba_baseprefix_"))
    subprocess.run([sys.executable, "-m", "venv", str(d)], check=True,
                   capture_output=True)
    return d


def test_run_python_code_uses_pack_interpreter(generic_packs, monkeypatch):
    from core.compute import project_env
    from core.exec.run import run_python_code
    prefix = _plant_prefix()
    monkeypatch.setattr(project_env, "interpreter",
                        lambda pid, lang: prefix / "bin" / "python")

    pid = projects.create_project("basepy")["id"]
    projects.set_current(pid)
    r = run_python_code(
        "import sys; print('PFX', sys.prefix); "
        "print('OVERLAY' if any('pylib_proj' in p for p in sys.path) else 'CLEAN')",
        project_id=pid, timeout_s=60)
    assert r.get("returncode") == 0, r
    assert str(prefix) in (r.get("stdout") or "")     # the PACK python ran
    assert "CLEAN" in (r.get("stdout") or "")          # standalone — no overlay


def test_run_r_code_uses_pack_rscript(generic_packs, monkeypatch):
    from core.compute import project_env
    import core.exec.run as runmod
    prefix = Path(tempfile.mkdtemp(prefix="aba_baser_"))
    (prefix / "bin").mkdir(parents=True)
    rs = prefix / "bin" / "Rscript"
    rs.write_text("#!/bin/sh\nexit 0\n")
    rs.chmod(0o755)
    monkeypatch.setattr(project_env, "interpreter",
                        lambda pid, lang: prefix / "bin" / "Rscript")

    captured = {}

    class _Res:
        timed_out = False; cancelled = False; returncode = 0; stdout = ""; stderr = ""

    def _exec(self, env, argv, **kw):
        captured["argv"] = list(argv)
        captured["script"] = Path(argv[-1]).read_text()
        return _Res()
    monkeypatch.setattr(runmod.MaterializingExecutor, "materialize",
                        lambda self, prov: type("E", (), {"python": None})())
    monkeypatch.setattr(runmod.MaterializingExecutor, "exec", _exec)
    pid = projects.create_project("baser")["id"]
    projects.set_current(pid)
    runmod.run_r_code("cat('hi')", project_id=pid, timeout_s=30)
    assert captured["argv"][0] == str(rs)
    assert ".libPaths(c(" not in captured.get("script", "")   # standalone


def test_no_pack_run_errors_never_served_base(no_packs):
    # W3.5 weft-only: a deployment with no base pack does NOT fall back to the
    # served base — run_python_code returns a structured error and does not run.
    from core.exec.run import run_python_code
    pid = projects.create_project("served")["id"]
    projects.set_current(pid)
    r = run_python_code("import sys; print('PFX', sys.prefix)", project_id=pid,
                        timeout_s=60)
    assert "error" in r and "pack is not available" in r["error"]
    assert not r.get("stdout"), "no-pack run must not execute on a served base"


# ── kernel spec content contract ─────────────────────────────────────────────

def test_base_kernelspec_requires_ipykernel(generic_packs, monkeypatch):
    from core.compute import project_env
    from core.exec.kernels import jupyter as jk
    prefix = _plant_prefix()          # bare venv — no ipykernel
    monkeypatch.setattr(project_env, "ensure",
                        lambda pid, lang: {"session_id": "ses_test",
                                           "prefix": prefix,
                                           "base_env_id": "env:v1:x"})
    with pytest.raises(RuntimeError, match="ipykernel"):
        jk._ensure_base_python_kernelspec()


# ── LIVE: real weft solve/realize of a tiny generic base (opt-in) ────────────

@pytest.mark.skipif(not os.environ.get("ABA_WEFT_LIVE"),
                    reason="set ABA_WEFT_LIVE=1 for the real base-pack round trip")
def test_live_default_lane_on_real_pack(generic_packs, monkeypatch):
    import core.compute.adapter as ad
    ad.shutdown(); monkeypatch.setattr(ad, "_adapter", None)
    st = ad.configure()
    assert st["ok"], st["detail"]
    from core.exec.run import run_python_code
    pid = projects.create_project("baselive")["id"]
    projects.set_current(pid)
    r = run_python_code("import sys, ipykernel; print('LIVE_BASE', sys.prefix)",
                        project_id=pid, timeout_s=900)
    assert r.get("returncode") == 0, r
    assert "LIVE_BASE" in (r.get("stdout") or "")
    assert str(ad.weft_workspace()) in (r.get("stdout") or "")
    ad.shutdown()
