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


# ── cold-base sessions (weft 6070bfc: pylib overlay over a mounted base) ─────

class _ColdBaseStub(_LazyStub):
    """Adopted/unpacked (cold package cache) base: a pypi add materializes a
    PYLIB overlay over the mount — source stays "base", env_id nulls (mutated
    scratch), activation composes overlay.sh, direct exec can't see the layer;
    a conda add refuses with session.cold_base + levers."""

    def _pylib(self, sid):
        return WS / "site-local" / f"sessions/{sid}" / "pylib"

    def _rlib(self, sid):
        return WS / "site-local" / f"sessions/{sid}" / "rlib"

    def _rt(self, sid) -> dict:
        s = self.sessions[sid]
        rt = {"source": "base", "env_id": s["base_env_id"],
              "prefix": str(BASE_PREFIX), "activation": BASE_ACT,
              "ns_wrap": False, "direct_exec": True}
        if s.get("mode") == "pylib" or s.get("rlib"):
            rt.update(env_id=None,
                      activation=f"{BASE_ACT} && . overlay.sh",
                      direct_exec=False)
            if s.get("mode") == "pylib":
                rt["pylib"] = str(self._pylib(sid))
            if s.get("rlib"):
                rt["rlib"] = str(self._rlib(sid))
        return rt

    async def session_install(self, sid, **kw):
        if kw.get("conda"):
            raise ComputeError(
                "session.cold_base",
                "adding conda package(s) needs a writable clone of the base, "
                "but the base was adopted/unpacked on this site (cold cache)",
                stage="realize",
                hints={"options": {"extends": "mint a delta env",
                                   "warm_site": "run where the base was built",
                                   "full_clone": "full_clone=true (needs egress)"}})
        self.installs.append((sid, kw))
        if kw.get("cran"):
            self.sessions[sid]["rlib"] = True
            self._rlib(sid).mkdir(parents=True, exist_ok=True)
            return {"installed": kw, "mode": "rlib", "runtime": self._rt(sid)}
        self.sessions[sid]["mode"] = "pylib"
        self._pylib(sid).mkdir(parents=True, exist_ok=True)
        return {"installed": kw, "mode": "pylib", "runtime": self._rt(sid)}


@pytest.fixture()
def cold(monkeypatch):
    yield _install_stub(monkeypatch, _ColdBaseStub())
    base_env.reset_cache()


def test_cold_base_pypi_pylib_overlay(cold):
    project_env.ensure("prj_c1", "python")
    res = project_env.install("prj_c1", "python", ["toolz"], eco="pypi")
    rt = res["runtime"]
    assert rt.get("pylib") and rt["direct_exec"] is False and rt["env_id"] is None
    out = project_env.ensure("prj_c1", "python")
    assert out["materialized"] is True          # the pylib layer IS the session's own
    argv = project_env.exec_argv("prj_c1", "python", ["-c", "1"])
    assert argv[:2] == ["bash", "-c"] and "overlay.sh" in argv[2]
    # the overlay is env-var composed — invisible to a bare path exec
    with pytest.raises(ComputeError) as ei:
        project_env.interpreter("prj_c1", "python")
    assert ei.value.code == "session.no_direct_exec"


def test_cold_base_conda_refusal_propagates(cold):
    project_env.ensure("prj_c2", "python")
    with pytest.raises(ComputeError) as ei:
        project_env.install("prj_c2", "python", ["somepkg"], eco="conda")
    assert ei.value.code == "session.cold_base"
    assert "options" in (ei.value.hints or {})
    # a refused install records NO phantom addition (nothing to replay)
    assert (project_env.get("prj_c2", "python") or {}).get("additions") == []


def test_cold_base_overview_and_layers(cold):
    from core.exec.env_integrity import env_overview, env_layers
    s0 = env_overview("prj_c3")["session"]
    assert s0["identity"] == "env:v1:packbase"      # unmutated: base identity
    project_env.install("prj_c3", "python", ["toolz"], eco="pypi")
    s1 = env_overview("prj_c3")["session"]
    assert s1["materialized"] is True and "unhashed scratch" in (s1["identity"] or "")
    assert (s1.get("overlays") or {}).get("pylib", "").endswith("pylib")
    layers = env_layers("prj_c3")["python"]["layers"]
    sess = [l for l in layers if l["tier"] == "session"]
    assert sess and sess[0].get("mode") == "pylib-overlay"


# ── R parity: the cran layer rides ANY base (weft 80e609d) ───────────────────

def test_cold_base_cran_rlib_layer(cold):
    project_env.ensure("prj_r1", "r")
    res = project_env.install("prj_r1", "r", ["ggplot2"], eco="cran")
    rt = res["runtime"]
    assert rt.get("rlib") and rt["env_id"] is None
    out = project_env.ensure("prj_r1", "r")
    assert out["materialized"] is True          # the rlib layer is session-owned
    argv = project_env.exec_argv("prj_r1", "r", ["-e", "1"])
    assert argv[:2] == ["bash", "-c"] and "overlay.sh" in argv[2]


# ── isolated-env eco passthrough (the cold-base lever's consumer side) ───────

def test_isolated_env_eco_passthrough(lazy, monkeypatch):
    """A conda-only (wheel-less) dep must be reachable through the isolated
    lane the cold-base refusal advertises: create routes conda_packages into
    the conda layer, extend takes an eco override, R extend splits by the same
    prefix heuristic create uses (it used to force cran), and layers record
    their full deps block so a platform re-lock replays the same ecosystems."""
    from core.compute import named_envs
    specs = []
    async def _env_ensure(spec, **kw):
        specs.append(spec)
        return {"env_id": f"env:v1:iso{len(specs)}", "status": "solved"}
    monkeypatch.setattr(lazy, "env_ensure", _env_ensure, raising=False)
    named_envs.create("prj_e1", "iso", language="python",
                      packages=["numpy"], conda_packages=["samtools"])
    assert "samtools" in specs[-1]["deps"]["conda"]
    assert specs[-1]["deps"]["pypi"] == ["numpy"]
    named_envs.extend("prj_e1", "iso", ["bwa"], eco="conda")
    assert specs[-1]["deps"] == {"conda": ["bwa"]}
    row = named_envs.resolve("prj_e1", "iso")
    assert row["layers"][-1] == {"deps": {"conda": ["bwa"]}}
    named_envs.create("prj_e1", "riso", language="r", packages=[])
    named_envs.extend("prj_e1", "riso", ["r-jsonlite", "ggplot2"])
    assert specs[-1]["deps"] == {"conda": ["r-jsonlite"], "cran": ["ggplot2"]}
    # legacy flat layers still replay, routed to the language default
    assert named_envs._layer_deps(["a", "b"], "python") == {"pypi": ["a", "b"]}
    assert named_envs._layer_deps({"deps": {"conda": ["c"]}}, "r") == {"conda": ["c"]}


# ── HIGH-1: concurrent extend must not lose a delta (optimistic retry) ───────

def test_concurrent_extend_keeps_both_deltas(lazy, monkeypatch):
    """Two extends racing on one handle: the solve runs outside the registry
    lock, so the loser used to clobber env_id — the winner's delta vanished
    from the identity chain while its layer stayed recorded (identity/record
    drift). Now the loser detects the moved tip and re-solves on it: both
    deltas chain, in landing order."""
    from core.compute import named_envs
    solved = []
    async def _env_ensure(spec, **kw):
        solved.append(spec)
        return {"env_id": f"env:v1:c{len(solved)}", "status": "solved"}
    monkeypatch.setattr(lazy, "env_ensure", _env_ensure, raising=False)
    named_envs.create("prj_cc", "shared", language="python", packages=[])
    base_id = named_envs.resolve("prj_cc", "shared")["env_id"]

    # Simulate the race: between OUR solve and OUR apply, a competing extend
    # lands (applied directly through the real registry machinery).
    real_update = named_envs._update
    fired = {"done": False}
    def _racing_update(pid, fn):
        if not fired["done"]:
            fired["done"] = True
            def _competitor(data):
                r = data["envs"]["shared"]
                r.setdefault("history", []).append(r["env_id"])
                r["env_id"] = "env:v1:competitor"
                r.setdefault("layers", []).append({"deps": {"pypi": ["rival"]}})
            real_update(pid, _competitor)
        return real_update(pid, fn)
    monkeypatch.setattr(named_envs, "_update", _racing_update)

    out = named_envs.extend("prj_cc", "shared", ["mypkg"])
    row = named_envs.resolve("prj_cc", "shared")
    # our extend re-solved AGAINST the competitor's tip, not the stale base
    assert solved[-1]["extends_env"] == "env:v1:competitor"
    assert row["env_id"] == out["env_id"]
    # both deltas present in the layer record, competitor first
    assert {"deps": {"pypi": ["rival"]}} in row["layers"]
    assert {"deps": {"pypi": ["mypkg"]}} in row["layers"]
    # and the identity chain kept the competitor's id in history
    assert "env:v1:competitor" in row["history"]


# ── HIGH-2: re-extend with already-present packages is a no-op ───────────────

def test_reextend_same_packages_idempotent(lazy, monkeypatch):
    """Re-requesting packages an env already records must not mint a new
    EnvID (the old path re-solved AND the tool layer then evicted the env's
    live kernels — a retried call destroyed working state). Changed spec
    strings still re-solve."""
    from core.compute import named_envs
    solved = []
    async def _env_ensure(spec, **kw):
        solved.append(spec)
        return {"env_id": f"env:v1:i{len(solved)}", "status": "solved"}
    monkeypatch.setattr(lazy, "env_ensure", _env_ensure, raising=False)
    named_envs.create("prj_id", "pinny", language="python", packages=["numpy"])
    named_envs.extend("prj_id", "pinny", ["scipy"])
    n = len(solved)
    eid = named_envs.resolve("prj_id", "pinny")["env_id"]
    out = named_envs.extend("prj_id", "pinny", ["scipy"])          # exact repeat
    assert out["status"] == "cached" and out["env_id"] == eid
    out2 = named_envs.extend("prj_id", "pinny", ["numpy", "scipy"])  # subset
    assert out2["status"] == "cached" and out2["env_id"] == eid
    assert len(solved) == n                                        # no re-solve
    assert len(named_envs.resolve("prj_id", "pinny")["layers"]) == 1
    out3 = named_envs.extend("prj_id", "pinny", ["scipy==1.12"])   # changed pin
    assert out3["status"] != "cached" and len(solved) == n + 1
