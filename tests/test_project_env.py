"""W3.4 (weft rewrite, personal installs): per-project DEFAULT envs as weft
sessions — live capability installs (no kernel restart), snapshot EnvIDs for
background jobs, module-toggle gating, rebuild-with-replay. Generic packs.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_projenv_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")   # weft workspace derives here
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core.bundle.loader import EnvPack  # noqa: E402
from core.compute import base_env, project_env  # noqa: E402

WS = Path(_tmp) / "home" / "weft"   # = $ABA_HOME/weft (derived, no setting)


class _StubAdapter:
    """Sessions + envs in memory; prefixes materialized under the workspace so
    project_env._session_prefix finds them."""

    def __init__(self):
        self.sessions: dict[str, dict] = {}
        self.installs: list[tuple[str, dict]] = []
        self.snap_n = 0
        self.n = 0
        _StubAdapter._uid += 1
        self.uid = _StubAdapter._uid

    _uid = 0

    def _mk_session(self, sid):
        loc = f"sessions/{sid}"  # matches weft's deterministic layout
        d = WS / "site-local" / loc / ".pixi" / "envs" / "default" / "bin"
        d.mkdir(parents=True, exist_ok=True)
        if not (d / "python").exists():
            (d / "python").symlink_to(sys.executable)
        if not (d / "Rscript").exists():
            (d / "Rscript").write_text("#!/bin/sh\nexit 0\n")
            (d / "Rscript").chmod(0o755)
        self.sessions[sid] = {"session_id": sid, "location": loc}

    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)

    async def env_ensure(self, spec, **kw):
        return {"env_id": "env:v1:packbase", "status": "cached"}

    async def session_start(self, base, site):
        self.n += 1
        sid = f"ses_{self.uid}_{self.n}"
        self._mk_session(sid)
        return {"session_id": sid, "base_env_id": base}

    async def session_install(self, sid, **kw):
        self.installs.append((sid, kw))
        return {"installed": kw}

    async def session_run_installer(self, sid, cmd, **kw):
        self.installs.append((sid, {"installer": cmd}))
        return {"ran": cmd}

    async def session_snapshot(self, sid, **kw):
        self.snap_n += 1
        return {"env_id": f"env:v1:snap{self.snap_n}"}

    async def session_stop(self, sid):
        self.sessions.pop(sid, None)
        import shutil
        shutil.rmtree(WS / "site-local" / "sessions" / sid, ignore_errors=True)
        return {"stopped": sid}

    def sync_call(self, name, *a, **kw):
        if name == "list_sessions":
            return {"sessions": list(self.sessions.values())}
        raise AssertionError(f"unexpected sync_call {name}")


@pytest.fixture()
def stub(monkeypatch):
    # conftest re-pins runtime dirs per module, but not ABA_HOME (whence the
    # weft workspace derives) — re-pin it here so _session_prefix and the
    # stub agree under a full run.
    monkeypatch.setenv("ABA_HOME", str(Path(_tmp) / "home"))
    monkeypatch.setenv("ABA_PROJECTS_DIR", str(Path(_tmp) / "projects"))
    import core.bundle.active as active
    import core.compute.adapter as ad
    monkeypatch.setattr(active, "get_bundle", lambda: type(
        "B", (), {"env_packs": [
            EnvPack("python-bio", {"name": "python-bio", "languages": ["python"],
                                   "role": "base",
                                   "spec": {"deps": {"conda": ["python =3.12",
                                                               "ipykernel"]}}},
                    "system"),
            EnvPack("r-bio", {"name": "r-bio", "languages": ["r"],
                              "role": "base",
                              "spec": {"deps": {"conda": ["r-base =4.4.*",
                                                          "r-irkernel"]}}},
                    "system"),
        ]})())
    stub = _StubAdapter()
    monkeypatch.setattr(ad, "get_compute", lambda: stub)
    base_env.reset_cache()
    yield stub
    base_env.reset_cache()


def test_ensure_creates_then_reuses(stub):
    s1 = project_env.ensure("prjA", "python")
    assert s1["session_id"].endswith("_1") and s1["prefix"].exists()
    s2 = project_env.ensure("prjA", "python")
    assert s2["session_id"] == s1["session_id"]  # reused, not recreated
    assert stub.n == 1
    # a second project gets its OWN session (isolation)
    assert project_env.ensure("prjB", "python")["session_id"].endswith("_2")


def test_install_records_and_marks_dirty(stub):
    project_env.ensure("prj_rec", "python")
    project_env.install("prj_rec", "python", ["meshlib"], eco="pypi")
    row = project_env.get("prj_rec", "python")
    assert row["additions"][-1] == {"eco": "pypi", "specs": ["meshlib"],
                                    "at": row["additions"][-1]["at"]}
    assert row["rev"] == 1
    assert stub.installs[-1][1] == {"pypi": ["meshlib"]}


def test_snapshot_dirty_caching(stub):
    project_env.ensure("prj_snap", "python")
    e1 = project_env.snapshot("prj_snap", "python")
    e2 = project_env.snapshot("prj_snap", "python")
    assert e1 == e2                              # unchanged → cached
    project_env.install("prj_snap", "python", ["toolz"], eco="pypi")
    e3 = project_env.snapshot("prj_snap", "python")
    assert e3 != e1                              # dirty → fresh snapshot
    assert stub.snap_n == 2


def test_lost_session_rebuilds_and_replays(stub):
    project_env.ensure("prj_lost", "python")
    project_env.install("prj_lost", "python", ["meshlib"], eco="pypi")
    # session pruned out-of-band (weft gc / stop)
    import shutil
    for sid in list(stub.sessions):
        shutil.rmtree(WS / "site-local" / "sessions" / sid, ignore_errors=True)
    stub.sessions.clear()
    n_installs = len(stub.installs)
    s = project_env.ensure("prj_lost", "python")
    assert s["session_id"].endswith("_2")        # recreated
    assert len(stub.installs) == n_installs + 1  # additions REPLAYED
    assert stub.installs[-1][1] == {"pypi": ["meshlib"]}
    assert project_env.get("prj_lost", "python")["snapshot"] is None   # stale


def test_base_pack_upgrade_recreates(stub, monkeypatch):
    project_env.ensure("prj_upg", "python")
    row = project_env.get("prj_upg", "python")
    row["base_env_id"] = "env:v1:OLD"            # simulate a pack upgrade
    project_env._save_row("prj_upg", "python", row)
    s = project_env.ensure("prj_upg", "python")
    assert s["base_env_id"] == "env:v1:packbase" and s["session_id"].endswith("_2")


def test_base_pack_change_is_surfaced_not_silent(stub):
    """I4 — a base-pack change under an existing project is reported on the
    ensure() result (base_changed), with the recorded additions replayed onto
    the new base; a plain new project / pruned-session rebuild has no such flag."""
    # New project: no base_changed flag.
    s0 = project_env.ensure("prj_bc", "python")
    assert "base_changed" not in s0
    # Record an addition, then simulate the pack moving under the project.
    project_env.install("prj_bc", "python", ["meshlib"], eco="pypi")
    row = project_env.get("prj_bc", "python")
    row["base_env_id"] = "env:v1:OLDBASE"
    project_env._save_row("prj_bc", "python", row)
    n_installs = len(stub.installs)
    s1 = project_env.ensure("prj_bc", "python")
    assert s1.get("base_changed") == {"from": "env:v1:OLDBASE",
                                      "to": "env:v1:packbase",
                                      "additions_replayed": 1}
    assert len(stub.installs) == n_installs + 1          # addition REPLAYED
    # A subsequent ensure (base now matches) reuses silently — no flag.
    s2 = project_env.ensure("prj_bc", "python")
    assert "base_changed" not in s2 and s2["session_id"] == s1["session_id"]


def test_stop_all_sessions_frees_project_sessions(stub):
    """I4 — deleting a project stops its live weft sessions (freeing the store)
    while leaving the registry file for recovery; a stopped session self-heals
    on the next ensure()."""
    project_env.ensure("prj_del", "python")
    project_env.ensure("prj_del", "r")
    project_env.install("prj_del", "python", ["meshlib"], eco="pypi")
    live = {v["session_id"] for v in stub.sessions.values()}
    assert len(live) == 2                                # python + r sessions
    out = project_env.stop_all_sessions("prj_del")
    assert set(out["stopped"]) == live and not out["errors"]
    assert stub.sessions == {}                           # both stopped in weft
    # Registry (additions) untouched → a later ensure rebuilds + replays.
    assert project_env.get("prj_del", "python")["additions"][-1]["specs"] == ["meshlib"]
    n = len(stub.installs)
    project_env.ensure("prj_del", "python")
    assert len(stub.installs) == n + 1                   # replayed on rebuild


def test_stop_all_sessions_noop_when_no_registry(stub):
    """No default sessions recorded → a clean no-op (never raises)."""
    out = project_env.stop_all_sessions("prj_never_used")
    assert out == {"stopped": [], "errors": []}


def test_module_off_gate_refuses(stub, monkeypatch):
    from core.modules import registry as mreg, manager as mmgr
    monkeypatch.setattr(mreg, "get",
                        lambda mid: type("S", (), {"id": mid})() if mid == "r-bio" else None)
    monkeypatch.setattr(mmgr, "mode", lambda spec: "off")
    with pytest.raises(RuntimeError, match="enable_module"):
        project_env.ensure("prj_gate", "r")
    # python pack has no module row here → ungated
    assert project_env.ensure("prj_gate", "python")["session_id"]


def test_reset_drops_session(stub):
    project_env.ensure("prj_reset", "python")
    project_env.install("prj_reset", "python", ["meshlib"], eco="pypi")
    project_env.reset("prj_reset", "python")
    assert project_env.get("prj_reset", "python") is None
    s = project_env.ensure("prj_reset", "python")
    assert project_env.get("prj_reset", "python")["additions"] == []   # reset means reset


def test_run_in_r_eco_guard(stub):
    with pytest.raises(ValueError, match="conda-first"):
        project_env.install("prj_eco", "r", ["ggplot2"], eco="cran")


# ── integration seams ────────────────────────────────────────────────────────

def test_run_python_code_interp_uses_resolved_path(stub, monkeypatch):
    """The entry passes a PRE-RESOLVED interpreter (spec['interp']) — no
    substrate in the node process."""
    from core import projects
    from core.exec.run import run_python_code
    projects.init()
    pid = projects.create_project("projenv-run")["id"]
    projects.set_current(pid)
    import subprocess
    prefix = Path(tempfile.mkdtemp(prefix="aba_snappfx_"))
    subprocess.run([sys.executable, "-m", "venv", str(prefix)], check=True,
                   capture_output=True)
    r = run_python_code("import sys; print('SNAP', sys.prefix)",
                        project_id=pid,
                        interp=str(prefix / "bin" / "python"), timeout_s=60)
    assert r.get("returncode") == 0, r
    assert str(prefix) in (r.get("stdout") or "")


def test_weft_submitter_carries_snapshot(stub, monkeypatch):
    import json
    from core import projects
    from core.jobs.weft_submitter import WeftSubmitter
    projects.init()
    pid = projects.create_project("projenv-job")["id"]
    projects.set_current(pid)
    monkeypatch.setattr(project_env, "snapshot",
                        lambda p, lang: "env:v1:jobsnap")
    from core.compute import named_envs
    fake_prefix = WS / "site-local" / "envs" / "jobsnap"
    (fake_prefix / "bin").mkdir(parents=True, exist_ok=True)
    (fake_prefix / "bin" / "python").write_text("")
    monkeypatch.setattr(named_envs, "ensure_realized",
                        lambda eid, **kw: fake_prefix)
    captured = {}

    class _Ad(_StubAdapter):
        def sync_call(self, name, *a, **kw):
            if name == "task_submit":
                captured.update(a[0])
                return {"job_id": "jb_x"}
            return super().sync_call(name, *a, **kw)
    import core.compute
    import core.compute.adapter as ad
    _inst = _Ad()
    monkeypatch.setattr(ad, "get_compute", lambda: _inst)
    monkeypatch.setattr(core.compute, "get_compute", lambda: _inst)
    from core.graph.jobs import create_job
    job = create_job("job_pe1", "run_python", "t", None,
                     {"code": "print(1)", "project_id": pid, "timeout_s": 60},
                     project_id=pid)
    WeftSubmitter().submit(job)
    spec = json.loads(Path(captured["command"].split()[-1]).read_text())
    assert spec["env_id"] == "env:v1:jobsnap"
    # interp is resolved at the NODE from $CONDA_PREFIX (weft activates the EnvID there),
    # not baked aba-side — the raw-prefix path broke under squashfs (#31, Round 20).
    assert spec["interp"] is None


def test_ensure_capability_installs_into_session(stub, monkeypatch):
    import content.bio  # noqa: F401
    from core import projects
    from content.bio.tools import discovery as d
    from core.catalog import register_capability
    projects.init()
    pid = projects.create_project("projenv-cap")["id"]
    projects.set_current(pid)
    register_capability({
        "name": "meshkit", "archetype": "library",
        "summary": "mesh utilities", "provisioning": {"pip": ["meshkit"]},
        "import_name": "meshkit", "scope": "system", "status": "published"})
    calls = {}
    monkeypatch.setattr(project_env, "install",
                        lambda p, lang, specs, eco: calls.update(
                            {"pid": p, "lang": lang, "specs": specs, "eco": eco}))
    # first probe (already importable?) fails, post-install verify succeeds
    seen = {"n": 0}

    def _verify(names, **kw):
        seen["n"] += 1
        return (seen["n"] > 1), ""
    monkeypatch.setattr("core.exec.verify.verify_python_imports", _verify)
    r = d.ensure_capability({"name": "meshkit"})
    assert r["status"] == "ready", r
    assert calls == {"pid": pid, "lang": "python",
                     "specs": ["meshkit"], "eco": "pypi"}


# ── modules absorption ───────────────────────────────────────────────────────

def test_pack_backed_module_probe_and_install(stub, monkeypatch):
    import core.compute
    monkeypatch.setattr(core.compute, "status", lambda: {"ok": True})
    from core.modules import manager
    from core.modules.registry import ModuleSpec
    spec = ModuleSpec(id="python-bio", title="Python bio", description="",
                      size="", est_time="", default_state="on",
                      env_target="", install_script="/nonexistent.sh",
                      removable=False, order=1)
    assert manager.pack_for(spec) == "python-bio"
    # not solved/realized in the stub store → not ready
    monkeypatch.setattr(manager, "_pack_ready", lambda pack: False)
    assert manager.probe_ready(spec) is False
    monkeypatch.setattr(manager, "_pack_ready", lambda pack: True)
    assert manager.probe_ready(spec) is True
    # install path = ensure+realize (strategy-blind), no shell script and no raw
    # prefix resolve — a squashfs pack has no readable prefix at rest (#31, Round 20).
    from core.modules import reconciler
    monkeypatch.setattr(base_env, "ensure_ready", lambda lang, **kw: None)
    ok = reconciler.run_module(spec, log=lambda m: None)
    assert ok is True


# ── LIVE: real session over a tiny pack (opt-in) ─────────────────────────────

@pytest.mark.skipif(not os.environ.get("ABA_WEFT_LIVE"),
                    reason="set ABA_WEFT_LIVE=1 for the live session round trip")
def test_live_session_install_and_snapshot(monkeypatch):
    import subprocess
    import core.bundle.active as active
    import core.compute.adapter as ad
    monkeypatch.setattr(active, "get_bundle", lambda: type(
        "B", (), {"env_packs": [EnvPack("tiny-base", {
            "name": "tiny-base", "languages": ["python"], "role": "base",
            "spec": {"deps": {"conda": ["python =3.12", "ipykernel"]}}},
            "system")]})())
    ad.shutdown()
    monkeypatch.setattr(ad, "_adapter", None)
    st = ad.configure()
    assert st["ok"], st["detail"]
    base_env.reset_cache()

    s = project_env.ensure("prjLive", "python")
    py = s["prefix"] / "bin" / "python"
    assert py.exists()
    # live install → importable in the SAME prefix, no new env
    r0 = subprocess.run([str(py), "-c", "import sortedcontainers"],
                        capture_output=True)
    assert r0.returncode != 0                      # not there yet
    project_env.install("prjLive", "python", ["sortedcontainers"], eco="pypi")
    r1 = subprocess.run([str(py), "-c",
                         "import sortedcontainers; print('LIVE_INSTALL_OK')"],
                        capture_output=True, text=True)
    assert r1.returncode == 0 and "LIVE_INSTALL_OK" in r1.stdout
    # frozen identity for jobs
    eid = project_env.snapshot("prjLive", "python")
    assert eid.startswith("env:v1:")
    assert project_env.snapshot("prjLive", "python") == eid   # cached
    ad.shutdown()
