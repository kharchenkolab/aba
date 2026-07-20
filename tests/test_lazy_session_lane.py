"""Step 2 — the strategy-blind default-env lane (lazy-session adaptation).

The substrate may clone sessions EAGERLY (pre-runtime weft: a live session
always owns an on-disk prefix) or LAZILY (runtime-contract weft: a session
runs from its base realization until the first install materializes a clone).
ABA's default lane must behave identically under both — liveness is the
substrate's answer, never prefix existence; interpreters resolve through the
runtime block ({source, env_id, prefix, activation, ns_wrap, direct_exec});
and the topology-blind argv builder is the only way one-shot lanes compose
commands. Both personalities are tested here so no future substrate strategy
change can blind the suite again (the Step 1 lesson, enforced structurally).
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_lazysess_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")   # weft workspace derives here
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core.bundle.loader import EnvPack  # noqa: E402
from core.compute import base_env, project_env  # noqa: E402
from core.compute.errors import ComputeError  # noqa: E402

WS = Path(_tmp) / "home" / "weft"
BASE_PREFIX = WS / "base_realization" / ".pixi" / "envs" / "default"
BASE_ACT = f". {WS / 'base_realization'}/activate.sh"


def _mk_base_prefix():
    d = BASE_PREFIX / "bin"
    d.mkdir(parents=True, exist_ok=True)
    for exe, body in (("python", None), ("Rscript", "#!/bin/sh\nexit 0\n")):
        p = d / exe
        if p.exists():
            continue
        if body is None:
            p.symlink_to(sys.executable)
        else:
            p.write_text(body)
            p.chmod(0o755)
    (WS / "base_realization" / "activate.sh").write_text("# stub activation\n")


class _EagerStub:
    """Pre-runtime weft: eager clones, no session_runtime tool, no runtime
    blocks on results — ABA must fall back to the activation-shaped shim."""

    has_runtime_tool = False
    _uid = 0

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.installs: list[tuple[str, dict]] = []
        self.n = 0
        type(self)._uid += 1
        self.uid = type(self)._uid

    def _sess_prefix(self, sid):
        return WS / "site-local" / f"sessions/{sid}" / ".pixi" / "envs" / "default"

    def _mk_clone(self, sid):
        d = self._sess_prefix(sid) / "bin"
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "python").exists():
            (d / "python").symlink_to(sys.executable)
        if not (d / "Rscript").exists():
            (d / "Rscript").write_text("#!/bin/sh\nexit 0\n")
            (d / "Rscript").chmod(0o755)

    async def env_ensure(self, spec, **kw):
        return {"env_id": "env:v1:packbase", "status": "cached"}

    async def session_start(self, base, site):
        self.n += 1
        sid = f"ses_e{self.uid}_{self.n}"
        self._mk_clone(sid)
        self.sessions[sid] = {"session_id": sid, "base_env_id": base}
        return {"session_id": sid, "base_env_id": base}

    async def session_install(self, sid, **kw):
        self.installs.append((sid, kw))
        return {"installed": kw}

    async def session_run_installer(self, sid, cmd, **kw):
        self.installs.append((sid, {"installer": cmd}))
        return {"ran": cmd}

    async def session_snapshot(self, sid, **kw):
        return {"env_id": "env:v1:snap"}

    async def session_stop(self, sid):
        self.sessions.pop(sid, None)
        import shutil
        shutil.rmtree(WS / "site-local" / "sessions" / sid, ignore_errors=True)
        return {"stopped": sid}


class _LazyStub:
    """Runtime-contract weft: lazy clones — session_start lays down NO prefix
    and returns a source=base runtime; the first install is the FLIP moment
    (clone materializes, install result carries the fresh source=session
    runtime); session_runtime is the observation-only accessor."""

    has_runtime_tool = True
    _uid = 0

    def __init__(self, *, base_direct_exec: bool = True,
                 base_ns_wrap: bool = False):
        self.sessions: dict[str, dict] = {}
        self.installs: list[tuple[str, dict]] = []
        self.n = 0
        self.base_direct_exec = base_direct_exec
        self.base_ns_wrap = base_ns_wrap
        type(self)._uid += 1
        self.uid = type(self)._uid

    def _sess_prefix(self, sid):
        return WS / "site-local" / f"sessions/{sid}" / ".pixi" / "envs" / "default"

    def _rt(self, sid) -> dict:
        s = self.sessions[sid]
        if s["materialized"]:
            return {"source": "session", "env_id": None,
                    "prefix": str(self._sess_prefix(sid)),
                    "activation": f"pixi-hook {sid}", "ns_wrap": False,
                    "direct_exec": True}
        return {"source": "base", "env_id": s["base_env_id"],
                "prefix": (str(BASE_PREFIX) if self.base_direct_exec else None),
                "activation": BASE_ACT, "ns_wrap": self.base_ns_wrap,
                "direct_exec": self.base_direct_exec and not self.base_ns_wrap}

    def _flip(self, sid):
        d = self._sess_prefix(sid) / "bin"
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "python").exists():
            (d / "python").symlink_to(sys.executable)
        if not (d / "Rscript").exists():
            (d / "Rscript").write_text("#!/bin/sh\nexit 0\n")
            (d / "Rscript").chmod(0o755)
        self.sessions[sid]["materialized"] = True

    async def env_ensure(self, spec, **kw):
        return {"env_id": "env:v1:packbase", "status": "cached"}

    async def session_start(self, base, site):
        self.n += 1
        sid = f"ses_l{self.uid}_{self.n}"
        self.sessions[sid] = {"session_id": sid, "base_env_id": base,
                              "materialized": False}
        return {"session_id": sid, "base_env_id": base, "materialized": False,
                "runtime": self._rt(sid),
                "note": "running from the base realization"}

    async def session_runtime(self, sid):
        if sid not in self.sessions:
            raise ComputeError("task.invalid", f"no active session {sid}",
                               stage="infra")
        return self._rt(sid)

    async def session_install(self, sid, **kw):
        self.installs.append((sid, kw))
        self._flip(sid)
        return {"installed": kw, "runtime": self._rt(sid)}

    async def session_run_installer(self, sid, cmd, **kw):
        self.installs.append((sid, {"installer": cmd}))
        self._flip(sid)
        return {"ran": cmd, "runtime": self._rt(sid)}

    async def session_snapshot(self, sid, **kw):
        s = self.sessions[sid]
        if not s["materialized"]:      # zero delta → the base IS the snapshot
            return {"env_id": s["base_env_id"],
                    "note": "session added nothing — the base env is the snapshot"}
        return {"env_id": "env:v1:snap"}

    async def session_stop(self, sid):
        self.sessions.pop(sid, None)
        return {"stopped": sid}


def _install_stub(monkeypatch, stub):
    monkeypatch.setenv("ABA_HOME", str(Path(_tmp) / "home"))
    monkeypatch.setenv("ABA_PROJECTS_DIR", str(Path(_tmp) / "projects"))
    import core.bundle.active as active
    import core.compute.adapter as ad
    monkeypatch.setattr(active, "get_bundle", lambda: type(
        "B", (), {"env_packs": [
            EnvPack("python-bio", {"name": "python-bio", "languages": ["python"],
                                   "role": "base",
                                   "spec": {"deps": {"conda": ["python =3.12"]}}},
                    "system"),
            EnvPack("r-bio", {"name": "r-bio", "languages": ["r"], "role": "base",
                              "spec": {"deps": {"conda": ["r-base =4.4.*"]}}},
                    "system"),
        ]})())
    monkeypatch.setattr(ad, "get_compute", lambda: stub)
    _mk_base_prefix()
    base_env.reset_cache()
    return stub


@pytest.fixture(params=["eager", "lazy"])
def any_stub(request, monkeypatch):
    """The dual-behavior matrix: every test on this fixture runs under BOTH
    substrate personalities."""
    stub = _EagerStub() if request.param == "eager" else _LazyStub()
    yield _install_stub(monkeypatch, stub)
    base_env.reset_cache()


@pytest.fixture()
def lazy(monkeypatch):
    yield _install_stub(monkeypatch, _LazyStub())
    base_env.reset_cache()


@pytest.fixture()
def lazy_activation_only(monkeypatch):
    """Lazy session over a mount-scoped (squashfs/userns) base: no usable
    prefix outside the activation's namespace."""
    yield _install_stub(monkeypatch, _LazyStub(base_direct_exec=False,
                                               base_ns_wrap=True))
    base_env.reset_cache()


# ── the leak + false-failure class (both personalities) ──────────────────────

def test_ensure_single_session_and_usable_argv(any_stub):
    """Repeated ensure() never spawns a duplicate session, never raises a
    false realize-failure, and always yields an exec-able argv — under BOTH
    clone strategies (the exact combination the prefix-existence probe broke:
    a lazy live session read as dead → re-create per call → session leak)."""
    s1 = project_env.ensure("prj_m", "python")
    s2 = project_env.ensure("prj_m", "python")
    assert s2["session_id"] == s1["session_id"]
    assert any_stub.n == 1                       # ONE session, ever
    argv = project_env.exec_argv("prj_m", "python", ["-c", "print(1)"])
    assert argv and all(isinstance(a, str) for a in argv)
    if argv[0] != "bash":                        # direct form must not dangle
        assert Path(argv[0]).exists()


def test_runtime_shape_present(any_stub):
    out = project_env.ensure("prj_shape", "python")
    rt = out["runtime"]
    assert set(rt) >= {"source", "env_id", "prefix", "activation",
                       "ns_wrap", "direct_exec"}
    assert out["materialized"] == (rt["source"] == "session")


# ── lazy-specific semantics ──────────────────────────────────────────────────

def test_lazy_fresh_session_runs_from_base(lazy):
    out = project_env.ensure("prj_l1", "python")
    assert out["materialized"] is False
    assert out["runtime"]["source"] == "base"
    assert out["runtime"]["env_id"] == "env:v1:packbase"   # identity intact
    # zero-delta ⇒ the base interpreter IS the session's interpreter
    assert project_env.interpreter("prj_l1", "python") == BASE_PREFIX / "bin" / "python"
    # ...and no install happened as a side effect of LOOKING (locate ≠ transfer)
    assert lazy.installs == [] and lazy.sessions[out["session_id"]]["materialized"] is False


def test_lazy_install_flips_runtime(lazy):
    project_env.ensure("prj_l2", "python")
    res = project_env.install("prj_l2", "python", ["toolz"], eco="pypi")
    assert res["runtime"]["source"] == "session"           # the FLIP moment
    out = project_env.ensure("prj_l2", "python")
    assert out["materialized"] is True
    assert Path(out["runtime"]["prefix"]).exists()
    # identity honesty: mutated scratch has no env id until snapshot
    assert out["runtime"]["env_id"] is None


def test_lazy_pruned_session_rebuilds_and_replays(lazy):
    project_env.ensure("prj_l3", "python")
    project_env.install("prj_l3", "python", ["meshlib"], eco="pypi")
    lazy.sessions.clear()                                  # pruned out-of-band
    n = len(lazy.installs)
    out = project_env.ensure("prj_l3", "python")
    assert lazy.n == 2                                     # ONE rebuild
    assert len(lazy.installs) == n + 1                     # addition replayed
    assert out["materialized"] is True                     # replay materialized it


def test_lazy_zero_delta_snapshot_is_base_id(lazy):
    project_env.ensure("prj_l4", "python")
    assert project_env.snapshot("prj_l4", "python") == "env:v1:packbase"


# ── topology honesty (activation-only bases) ─────────────────────────────────

def test_activation_only_interpreter_refuses_typed(lazy_activation_only):
    out = project_env.ensure("prj_a1", "python")
    assert out["prefix"] is None and out["materialized"] is False
    with pytest.raises(ComputeError) as ei:
        project_env.interpreter("prj_a1", "python")
    assert ei.value.code == "session.no_direct_exec"


def test_activation_only_argv_wraps(lazy_activation_only):
    argv = project_env.exec_argv("prj_a2", "python", ["-c", "print(1)"])
    assert argv[:2] == ["bash", "-c"]
    script = argv[2]
    assert BASE_ACT in script and "exec" in script
    assert "unshare -rm" in script                         # ns_wrap honored


# ── argv builder (pure) ──────────────────────────────────────────────────────

def test_argv_for_runtime_direct():
    rt = {"source": "session", "prefix": "/opt/envs/x", "activation": "A",
          "ns_wrap": False, "direct_exec": True}
    assert project_env.argv_for_runtime(rt, "python", ["-c", "1"]) == \
        ["/opt/envs/x/bin/python", "-c", "1"]
    assert project_env.argv_for_runtime(rt, "r", ["--vanilla", "s.R"]) == \
        ["/opt/envs/x/bin/Rscript", "--vanilla", "s.R"]


def test_argv_for_runtime_activation_quoting_and_pre():
    rt = {"source": "base", "prefix": None, "activation": ". /b/activate.sh",
          "ns_wrap": False, "direct_exec": False}
    argv = project_env.argv_for_runtime(rt, "python", ["-c", "print('a b')"],
                                        pre=["stdbuf", "-oL"])
    assert argv[:2] == ["bash", "-c"]
    assert argv[2] == ". /b/activate.sh && exec stdbuf -oL python -c 'print('\"'\"'a b'\"'\"')'"
    # pre lands in the DIRECT shape too
    rt2 = dict(rt, prefix="/p", direct_exec=True)
    assert project_env.argv_for_runtime(rt2, "python", ["x.py"],
                                        pre=["stdbuf", "-oL"]) == \
        ["stdbuf", "-oL", "/p/bin/python", "x.py"]


# ── presentation honesty ─────────────────────────────────────────────────────

def test_env_overview_reports_lazy_truthfully(lazy):
    from core.exec.env_integrity import env_overview
    ov = env_overview("prj_ov")
    s = ov["session"]
    assert s["active"] is True and s["materialized"] is False
    assert s["source"] == "base" and s["identity"] == "env:v1:packbase"
    project_env.install("prj_ov", "python", ["toolz"], eco="pypi")
    s2 = env_overview("prj_ov")["session"]
    assert s2["materialized"] is True
    assert "unhashed scratch" in (s2["identity"] or "")


def test_env_overview_reports_eager_truthfully(monkeypatch):
    stub = _install_stub(monkeypatch, _EagerStub())
    from core.exec.env_integrity import env_overview
    s = env_overview("prj_ov_e")["session"]
    assert s["active"] is True and s["materialized"] is True
    assert s["prefix"] and Path(s["prefix"]).exists()
    base_env.reset_cache()


# ── capability probes (the mounted-base extend bug) ──────────────────────────

def test_capability_probe_builder_activation_only(lazy_activation_only):
    """The field bug: on a mounted (squashfs/userns) base the default env has
    no directly-usable prefix, and the capability layer's import probe resolved
    a raw interpreter path — so ensure_capability's default-lane extend died on
    session.no_direct_exec even though session_install itself is topology-
    blind. The probe must compose through the session runtime like the exec
    lane does."""
    from content.bio.tools import discovery as d
    builder = d._default_probe_argv()
    assert builder is not None
    argv = builder(["-c", "import json"])
    assert argv[:2] == ["bash", "-c"]
    assert BASE_ACT in argv[2] and "unshare -rm" in argv[2]


def test_capability_probe_reresolves_after_flip(lazy):
    """A post-install verify must probe the FLIPPED session (its own clone),
    not the stale pre-install base — the builder re-resolves per call."""
    from content.bio.tools import discovery as d
    builder = d._default_probe_argv()
    before = builder(["-c", "import x"])[0]
    assert before == str(BASE_PREFIX / "bin" / "python")
    project_env.install("_none", "python", ["toolz"], eco="pypi")
    after = builder(["-c", "import x"])[0]
    assert after != before and "sessions/" in after


def test_verify_python_imports_argv_builder():
    from core.exec.verify import verify_python_imports
    ok, detail = verify_python_imports(
        ["json"], argv_builder=lambda args: [sys.executable, *args])
    assert ok, detail
    # the builder wins over python_exe — a dangling path must never be exec'd
    ok2, _ = verify_python_imports(
        ["json"], python_exe="/nonexistent/bin/python",
        argv_builder=lambda args: [sys.executable, *args])
    assert ok2
