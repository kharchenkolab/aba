"""Phase 1 guards for agent-managed named envs — the track/discover/locate/
reclaim surface (misc/env_agency_plan.md decisions 1-4).

Covers inspect_env()'s no-arg catalog, ensure_capability(env=) routing to
`named_envs.extend`, evict_env (evict / forget), and the per-turn context clause
— all over a tmp registry + a fake weft compute (no substrate, no network).

Run: python tests/test_env_agency.py
"""
from __future__ import annotations
import hashlib
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_envagency_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "e.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import core.config as _cfg                       # noqa: E402
from core.compute import adapter as _ad          # noqa: E402
from core.compute import named_envs              # noqa: E402
from core import projects                        # noqa: E402
from core.exec import env_integrity              # noqa: E402
from core.exec import compute_env as _ce         # noqa: E402
import core.catalog as _catalog                  # noqa: E402
import content.bio                               # noqa: E402,F401
from content.bio.tools import (                  # noqa: E402
    inspect_env, ensure_capability, evict_env)


class _FakeCompute:
    """Canned weft: env_ensure derives a stable EnvID from the spec; env_status
    returns planted per-site realizations (and can be told to raise for one env);
    env_evict lands via the synchronous sync_call pass-through (reconciler pattern)."""

    def __init__(self):
        self.reals: dict = {}        # env_id -> realizations list
        self.raise_on: set = set()   # env_ids whose env_status raises
        self.status_calls = 0        # every env_status hit (context_line must add 0)
        self.evicted: list = []      # (env_id, site) evicted
        self.ensured: list = []      # env_ensure specs seen

    async def env_ensure(self, spec, update=False, **kw):
        self.ensured.append(spec)
        h = hashlib.sha256((repr(sorted(spec.get("deps", {}).items()))
                            + repr(spec.get("extends_env"))).encode()).hexdigest()[:12]
        return {"env_id": f"env:v1:{h}", "status": "solved"}

    async def env_status(self, env_id):
        self.status_calls += 1
        if env_id in self.raise_on:
            raise RuntimeError("substrate hiccup on this env")
        return {"realizations": list(self.reals.get(env_id, []))}

    def sync_call(self, name, /, *args, **kw):
        if name == "env_evict":
            self.evicted.append(tuple(args))
        return {}


def _setup(mp) -> "_FakeCompute":
    """Fresh tmp registry + fake compute + a fixed current project."""
    tmp = Path(tempfile.mkdtemp(prefix="aba_ea_"))
    mp.setattr(_cfg, "PROJECTS_DIR", tmp / "projects")
    fake = _FakeCompute()
    mp.setattr(_ad, "get_compute", lambda: fake)
    mp.setattr(projects, "current", lambda: "prjT")
    return fake


def _env_id(name: str) -> str:
    return named_envs.resolve("prjT", name)["env_id"]


# ── 1. inspect_env() no-arg catalog ──────────────────────────────────────────

def test_inspect_env_catalog(monkeypatch):
    fake = _setup(monkeypatch)
    # keep the tier overview hermetic — the catalog is what's under test
    monkeypatch.setattr(env_integrity, "env_overview",
                        lambda pid=None: {"python": "x", "session": {}})
    named_envs.create("prjT", "numtools", packages=["numpy", "pandas"])
    named_envs.create("prjT", "rplot", language="r")
    named_envs.set_active("prjT", "numtools", "python")

    reals = [{"site": "local", "state": "ready", "bytes": 123, "idle_days": 1}]
    fake.reals[_env_id("numtools")] = reals
    fake.raise_on.add(_env_id("rplot"))          # this env's status blows up

    out = inspect_env({})
    assert out["status"] == "ok" and out["scope"] == "overview"
    cat = {e["name"]: e for e in out["named_envs"]}
    assert set(cat) == {"numtools", "rplot"}
    nt = cat["numtools"]
    assert nt["language"] == "python" and nt["packages"] == ["numpy", "pandas"]
    assert nt["active"] is True and nt["env_id"] == _env_id("numtools")
    assert nt["realizations"] == reals           # full per-site list from env_status
    rp = cat["rplot"]
    assert rp["language"] == "r" and rp["active"] is False
    assert rp["realizations"] == "unavailable"   # degraded, others intact


# ── 2. ensure_capability(env=X) routes to extend ─────────────────────────────

def test_ensure_capability_env_routes_to_extend(monkeypatch):
    _setup(monkeypatch)
    named_envs.create("prjT", "grow", packages=["six"])
    e1 = _env_id("grow")
    monkeypatch.setattr(_catalog, "resolve_capability", lambda name, *a, **k: {
        "name": "attrs", "archetype": "library",
        "provisioning": {"pip": ["attrs"]}, "status": "published"})

    out = ensure_capability({"name": "attrs", "env": "grow"})
    assert out["status"] == "ready" and out["env"] == "grow"
    assert out["installed"] == ["attrs"]
    assert out["env_id"] != e1 and out["env_id"] == _env_id("grow")
    assert "grow" in out["note"]
    row = named_envs.resolve("prjT", "grow")
    assert e1 in row["history"]                  # old id kept
    assert "attrs" in row["packages"]


def test_ensure_capability_unknown_env_errors(monkeypatch):
    _setup(monkeypatch)
    out = ensure_capability({"name": "attrs", "env": "ghost"})
    assert out["status"] == "error"
    assert "inspect_env" in out["note"]


# ── 3. evict_env — evict / site-scope / forget / active-refusal ──────────────

def test_evict_env_all_sites_keeps_row(monkeypatch):
    fake = _setup(monkeypatch)
    named_envs.create("prjT", "big", packages=["numpy"])
    e = _env_id("big")
    fake.reals[e] = [
        {"site": "local", "state": "ready", "bytes": 1000, "idle_days": 2},
        {"site": "gpu1", "state": "ready", "bytes": 2000, "idle_days": 5}]
    out = evict_env({"name": "big"})
    assert out["status"] == "ok"
    assert set(fake.evicted) == {(e, "local"), (e, "gpu1")}
    assert out["freed_bytes"] == 3000
    assert out["sites"] == {"local": 1000, "gpu1": 2000}
    assert named_envs.resolve("prjT", "big") is not None   # evict-only keeps the row


def test_evict_env_single_site(monkeypatch):
    fake = _setup(monkeypatch)
    named_envs.create("prjT", "big", packages=["numpy"])
    e = _env_id("big")
    fake.reals[e] = [
        {"site": "local", "state": "ready", "bytes": 1000, "idle_days": 2},
        {"site": "gpu1", "state": "ready", "bytes": 2000, "idle_days": 5}]
    out = evict_env({"name": "big", "site": "gpu1"})
    assert fake.evicted == [(e, "gpu1")]                   # only the named site
    assert out["freed_bytes"] == 2000 and out["sites"] == {"gpu1": 2000}


def test_evict_env_forget_removes_row(monkeypatch):
    fake = _setup(monkeypatch)
    named_envs.create("prjT", "gone", packages=["numpy"])
    e = _env_id("gone")
    fake.reals[e] = [{"site": "local", "state": "ready", "bytes": 500, "idle_days": 1}]
    out = evict_env({"name": "gone", "forget": True})
    assert out["status"] == "ok" and out.get("forgotten") is True
    assert (e, "local") in fake.evicted
    assert named_envs.resolve("prjT", "gone") is None      # row removed


def test_evict_env_forget_active_refused_no_partial(monkeypatch):
    fake = _setup(monkeypatch)
    named_envs.create("prjT", "act", packages=["numpy"])
    named_envs.set_active("prjT", "act", "python")
    fake.reals[_env_id("act")] = [
        {"site": "local", "state": "ready", "bytes": 900, "idle_days": 1}]
    out = evict_env({"name": "act", "forget": True})
    assert out["status"] == "error" and "active" in out["note"].lower()
    assert fake.evicted == []                              # NO partial action
    assert named_envs.resolve("prjT", "act") is not None   # still present


def test_evict_env_unknown_hints_inspect(monkeypatch):
    _setup(monkeypatch)
    out = evict_env({"name": "nope"})
    assert out["status"] == "error" and "inspect_env" in out["note"]


# ── 4. per-turn context clause (registry-only, no substrate calls) ───────────

def test_context_line_named_envs_clause(monkeypatch):
    fake = _setup(monkeypatch)
    monkeypatch.setattr(_ce, "compute_env",
                        lambda *a, **k: {"mode": "local", "node_cores": 4,
                                         "node_mem_gb": 8})
    named_envs.create("prjT", "numtools", packages=["numpy", "pandas", "scipy"])
    named_envs.create("prjT", "rplot", language="r")
    named_envs.set_active("prjT", "numtools", "python")

    line = _ce.context_line()
    assert "named envs:" in line
    assert "numtools (py*, numpy+pandas+1)" in line        # active mark + truncation
    assert "rplot (r)" in line
    assert "inspect_env()" in line
    assert fake.status_calls == 0                          # REGISTRY-ONLY: no env_status


def test_context_line_empty_registry_no_clause(monkeypatch):
    _setup(monkeypatch)
    monkeypatch.setattr(_ce, "compute_env",
                        lambda *a, **k: {"mode": "local", "node_cores": 4,
                                         "node_mem_gb": 8})
    line = _ce.context_line()
    assert "named envs" not in line                        # absent → line as before
    assert line.startswith("Compute environment:")


_TESTS = [test_inspect_env_catalog,
          test_ensure_capability_env_routes_to_extend,
          test_ensure_capability_unknown_env_errors,
          test_evict_env_all_sites_keeps_row,
          test_evict_env_single_site,
          test_evict_env_forget_removes_row,
          test_evict_env_forget_active_refused_no_partial,
          test_evict_env_unknown_hints_inspect,
          test_context_line_named_envs_clause,
          test_context_line_empty_registry_no_clause]


def _standalone() -> int:
    import inspect
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v, raising=True):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in _TESTS:
        mp = _MP()
        try:
            t(mp) if "monkeypatch" in inspect.signature(t).parameters else t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
