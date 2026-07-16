"""The agent-facing isolated-env control surface, weft-backed (weft rewrite W1).

make_isolated_env / run_in_isolated_env / set_active_env now ride
core/compute/named_envs (per-project name→EnvID handles; extends_env layering;
weft owns solve/realize). These tests pin the TOOL contract + the handle
bookkeeping with a stubbed compute adapter — no weft/network needed. The real
substrate path is covered by test_compute_ports.py (live echo task) and the
opt-in live test at the bottom (ABA_WEFT_LIVE=1).
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import content.bio  # noqa: E402,F401
from content.bio.tools import make_isolated_env, run_in_isolated_env, run_python  # noqa: E402

pytestmark = pytest.mark.bio


class _StubAdapter:
    """Canned env_ensure: EnvID derived from the spec (stable), no I/O."""
    def __init__(self):
        self.ensured: list[dict] = []

    async def env_ensure(self, spec, update=False, **kw):
        self.ensured.append(spec)
        import hashlib
        h = hashlib.sha256(repr(sorted(spec.get("deps", {}).items())).encode()
                           + repr(spec.get("extends_env")).encode()).hexdigest()
        return {"env_id": f"env:v1:{h}", "status": "solved", "summary": "stub"}


@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    """Project registry under tmp + stubbed adapter; realization returns a
    planted venv-shaped prefix so interpreter dispatch is real."""
    import core.config as _cfg
    from core.compute import named_envs, adapter as ad
    monkeypatch.setattr(_cfg, "PROJECTS_DIR", tmp_path / "projects")
    stub = _StubAdapter()
    monkeypatch.setattr(ad, "get_compute", lambda: stub)
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    # a real interpreter at the planted prefix → run_in works end-to-end
    (prefix / "bin" / "python").symlink_to(sys.executable)
    monkeypatch.setattr(named_envs, "ensure_realized",
                        lambda env_id, **kw: prefix)
    from core import projects
    monkeypatch.setattr(projects, "current", lambda: "prjT", raising=False)
    return stub


# ── argument/contract validation (no machinery) ──────────────────────────────

def test_make_requires_name():
    assert make_isolated_env({})["status"] == "error"


def test_run_requires_name_and_code():
    assert run_in_isolated_env({"name": "x"})["status"] == "error"


def test_make_isolated_env_rejects_reserved():
    for n in ("default", "base", "shared", "project"):
        r = make_isolated_env({"name": n})
        assert r["status"] == "error" and "reserved" in r["note"].lower(), n


def test_is_default_env_resolution():
    from content.bio.tools.run_exec import _is_default_env
    for v in (None, "", "default", "DEFAULT", "base", "shared", "project"):
        assert _is_default_env(v) is True, v
    for v in ("scrna", "legacy_tf", "myenv"):
        assert _is_default_env(v) is False, v


def test_is_constraint_conflict():
    from content.bio.tools.discovery import _is_constraint_conflict
    assert _is_constraint_conflict("ERROR: ResolutionImpossible")
    assert _is_constraint_conflict("The conflict is caused by numpy==2.4.6 (from -c)")
    assert _is_constraint_conflict("these have conflicting dependencies")
    assert not _is_constraint_conflict("Connection timed out")
    assert not _is_constraint_conflict("No matching distribution found for typopkg")


# ── handle bookkeeping over the stubbed adapter ──────────────────────────────

def test_make_env_and_run(stubbed):
    r = make_isolated_env({"name": "toolA"})
    assert r["status"] == "ok" and r["engine"] == "weft"
    assert r["env_id"].startswith("env:v1:")
    run = run_in_isolated_env({"name": "toolA", "code": "print('HELLO_ISO')"})
    assert run["status"] == "ok" and "HELLO_ISO" in run["stdout"]


def test_python_named_env_bakes_ipykernel(stubbed):
    make_isolated_env({"name": "kernelable"})
    spec = stubbed.ensured[-1]
    assert "ipykernel" in spec["deps"]["conda"]   # frozen env → baked at solve


def test_second_make_layers_via_extends_env(stubbed):
    r1 = make_isolated_env({"name": "grow", "packages": ["six"]})
    r2 = make_isolated_env({"name": "grow", "packages": ["attrs"]})
    assert r2["status"] == "ok" and r2["env_id"] != r1["env_id"]
    assert stubbed.ensured[-1].get("extends_env") == r1["env_id"]   # frozen base
    from core.compute import named_envs
    row = named_envs.resolve("prjT", "grow")
    assert row["env_id"] == r2["env_id"]
    assert r1["env_id"] in row["history"]                            # provenance kept
    assert set(row["packages"]) == {"six", "attrs"}


def test_run_in_missing_env_is_helpful(stubbed):
    r = run_in_isolated_env({"name": "ghost", "code": "print(1)"})
    assert r["status"] == "error" and "make_isolated_env" in r["note"]


def test_run_python_env_missing_is_helpful(stubbed):
    r = run_python({"code": "print(1)", "env": "ghost"})
    assert r["status"] == "error" and "make_isolated_env" in r["note"]


def test_run_python_env_executes_isolated_stateless(stubbed, monkeypatch):
    """Kernels off → the named env runs one-shot via its own interpreter."""
    import core.config as _cfg
    monkeypatch.setattr(_cfg, "KERNEL_ENABLED", False)
    assert make_isolated_env({"name": "envrun"})["status"] == "ok"
    r = run_python({"code": "print('ENV_RUN_OK')", "env": "envrun"})
    assert r["status"] == "ok" and "ENV_RUN_OK" in r["stdout"] and r["env"] == "envrun"


def test_envs_are_project_scoped(stubbed, monkeypatch):
    from core.compute import named_envs
    from core import projects
    make_isolated_env({"name": "dup"})
    monkeypatch.setattr(projects, "current", lambda: "prjB", raising=False)
    assert named_envs.resolve("prjB", "dup") is None       # other project blind
    assert named_envs.resolve("prjT", "dup") is not None


def test_active_env_pointer_roundtrip(stubbed):
    from content.bio.tools import set_active_env
    from core.compute import named_envs
    make_isolated_env({"name": "act"})
    assert set_active_env({"name": "act"})["active_python_env"] == "act"
    assert named_envs.get_active("prjT", "python") == "act"
    assert set_active_env({"name": "default"})["active_python_env"] == "default"
    assert set_active_env({"name": "ghost"})["status"] == "error"


# ── auto-isolation (UNSAT-against-base → isolate, not fail) ──────────────────

def test_auto_isolate_success(stubbed, monkeypatch):
    from core.compute import named_envs
    from content.bio.tools.discovery import _auto_isolate
    monkeypatch.setattr(named_envs, "verify_imports", lambda *a, **k: (True, ""))
    r = _auto_isolate("tflike", ["tflike==9"], {"import_name": "tflike"})
    assert r["status"] == "ready_isolated" and r["isolated_env"] == "cap-tflike"
    assert "run_in_isolated_env" in r["note"]


def test_auto_isolate_verify_fails(stubbed, monkeypatch):
    from core.compute import named_envs
    from content.bio.tools.discovery import _auto_isolate
    monkeypatch.setattr(named_envs, "verify_imports",
                        lambda *a, **k: (False, "ImportError: boom"))
    r = _auto_isolate("x", ["x"], {"import_name": "x"})
    assert r["status"] == "error" and "boom" in str(r.get("error"))


def test_ensure_capability_auto_isolates_on_conflict(stubbed, monkeypatch):
    import _packmode
    import core.catalog as cat
    from core.compute import named_envs, project_env as _pe
    from content.bio.tools import discovery as d
    _packmode.enable(monkeypatch)          # W3.5: pack-mode session install
    monkeypatch.setattr(cat, "resolve_capability", lambda name, *a, **k: {
        "name": name, "provisioning": {"pip": ["tflike==9"]},
        "import_name": "tflike", "scope": "project", "status": "published"})

    # W3.5: the conflict now surfaces from the weft SESSION install, not the old
    # MaterializingExecutor — auto-isolate must trigger off it just the same.
    def boom(pid, lang, specs, *, eco="pypi"):
        raise RuntimeError("ERROR: ResolutionImpossible. The conflict is caused by "
                           "numpy==2.4.6 (from -c constraints).")
    monkeypatch.setattr(_pe, "install", boom)
    monkeypatch.setattr(named_envs, "verify_imports", lambda *a, **k: (True, ""))
    r = d.ensure_capability({"name": "tflike"})
    assert r["status"] == "ready_isolated", r
    assert r["isolated_env"] == "cap-tflike" and "run_in_isolated_env" in r["note"]


# ── sync-bridge safety ────────────────────────────────────────────────────────

def test_named_envs_loop_thread_semantics():
    """Main-thread loop → refuse (that's uvicorn's loop); worker-thread loop →
    dispatch to a fresh thread and block (the in-process MCP bridge case,
    found live in the W3.4 gate)."""
    from core.compute import named_envs

    async def on_main_loop():
        coro = asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="worker thread|event loop"):
            named_envs._sync(coro)
        coro.close()
    asyncio.run(on_main_loop())

    import threading
    out = {}

    def worker():
        async def on_worker_loop():
            out["v"] = named_envs._sync(_value())
        asyncio.run(on_worker_loop())

    async def _value():
        return 42
    t = threading.Thread(target=worker)
    t.start(); t.join()
    assert out["v"] == 42


# ── opt-in LIVE test (real weft solve+realize; slow, needs network) ──────────

@pytest.mark.skipif(not os.environ.get("ABA_WEFT_LIVE"),
                    reason="set ABA_WEFT_LIVE=1 for the real solve/realize round-trip")
def test_live_make_and_run(tmp_path, monkeypatch):
    import core.config as _cfg
    from core.compute import adapter as ad
    from core import projects
    monkeypatch.setattr(_cfg, "PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "home"))  # workspace derives here
    monkeypatch.setattr(ad, "_adapter", None)
    monkeypatch.setattr(ad, "_status", {"ok": False, "severity": "info", "detail": "un"})
    st = ad.configure()
    assert st["ok"], st["detail"]
    monkeypatch.setattr(projects, "current", lambda: "prjLive", raising=False)
    r = make_isolated_env({"name": "live1"})
    assert r["status"] == "ok", r
    run = run_in_isolated_env({"name": "live1", "code": "print('LIVE_OK')",
                               "timeout_s": 900})
    assert run["status"] == "ok" and "LIVE_OK" in run["stdout"], run
    ad.shutdown()
