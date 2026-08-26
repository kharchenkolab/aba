"""Deleting a project must free what only that project held — and nothing else.

Before this guard, `delete_project` stopped the two default sessions and left
every named/isolated env on disk forever: weft's own GC only lists a
realization after `gc_idle_days` (default 14) and never sweeps without an
explicit confirm, so "delete the project" reclaimed ~nothing. A live project
that had accumulated five isolated envs for one library kept all five.

The three classes this pins (docs/arch/envs.md § Project deletion & reclaim):
rebuildable (evicted), shared (never touched), valued (reported, never
deleted). The dangerous direction is always "looks private ⇒ evict", so every
can't-tell case here must come back UNASSESSED, not swept.
"""
import json

import pytest

from core.compute import named_envs, project_env, reclaim

PID = "prj_under_test"
OTHER = "prj_neighbour"


def _seed(root, pid, envs, default=None):
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    (d / "weft_envs.json").write_text(json.dumps(
        {"envs": envs, "active": {}, "default": default or {}}))


def _env_row(env_id, language="python"):
    return {"env_id": env_id, "language": language, "packages": [],
            "conda_packages": [], "history": [], "layers": []}


def _ready(bytes_=1_000_000, read_only=False, site="local"):
    return {"realizations": [{"site": site, "state": "ready",
                              "bytes": bytes_, "read_only": read_only}]}


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """A two-project home, a fake weft, and recorders for the two verbs that
    actually cost something (session_stop, env_evict)."""
    import core.config as cfg
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path, raising=False)

    status: dict = {}
    calls: dict = {"evicted": [], "stopped": [], "order": []}

    monkeypatch.setattr(named_envs, "_sync", lambda x: x)
    monkeypatch.setattr(
        "core.compute.adapter.get_compute",
        lambda: type("C", (), {
            "env_status": lambda self, eid: status.get(eid, {"realizations": []})
        })())

    def _evict(pid, name, site=None):
        calls["evicted"].append(name)
        calls["order"].append(f"evict:{name}")
        row = named_envs.resolve(pid, name) or {}
        return {"env_id": row.get("env_id"), "freed_bytes": 1_000_000}

    def _stop(pid):
        calls["stopped"].append(pid)
        calls["order"].append("stop_sessions")
        return {"stopped": ["sess_1"], "errors": []}

    monkeypatch.setattr(named_envs, "evict", _evict)
    monkeypatch.setattr(project_env, "stop_all_sessions", _stop)
    monkeypatch.setattr("core.projects.list_projects",
                        lambda: [{"id": PID}, {"id": OTHER}])
    return tmp_path, status, calls


def test_a_private_env_is_evicted_on_delete(wired):
    root, status, calls = wired
    _seed(root, PID, {"scrublet-env": _env_row("env:v1:aaa")})
    _seed(root, OTHER, {})
    status["env:v1:aaa"] = _ready()

    out = reclaim.reclaim(PID, confirm=True)

    assert calls["evicted"] == ["scrublet-env"]
    assert out["freed_bytes"] == 1_000_000
    assert out["errors"] == []


def test_an_env_another_project_names_is_never_evicted(wired):
    """EnvIDs are content-addressed: the same packages give two projects the
    same id, and evicting it for one steals the prefix from the other."""
    root, status, calls = wired
    _seed(root, PID, {"shared-env": _env_row("env:v1:shared")})
    _seed(root, OTHER, {"their-name-for-it": _env_row("env:v1:shared")})
    status["env:v1:shared"] = _ready()

    out = reclaim.reclaim(PID, confirm=True)

    assert calls["evicted"] == []
    assert [e["name"] for e in out["kept_shared"]] == ["shared-env"]
    assert out["kept_shared"][0]["also_in"] == [OTHER]


def test_an_adopted_read_only_env_is_never_evicted(wired):
    """A pack env adopted from an institutional root costs this project no
    disk and is not ours to reclaim — weft refuses it, and the plan must not
    promise bytes weft will refuse to give back."""
    root, status, calls = wired
    _seed(root, PID, {"pack-r": _env_row("env:v1:pack", "r")})
    _seed(root, OTHER, {})
    status["env:v1:pack"] = _ready(read_only=True)

    out = reclaim.reclaim(PID, confirm=True)

    assert calls["evicted"] == []
    assert out["kept_shared"][0]["name"] == "pack-r"
    assert "read-only" in out["kept_shared"][0]["reason"]


def test_an_unassessable_registry_evicts_nothing(wired, monkeypatch):
    """No survivors known ⇒ sharing cannot be ruled out. An empty list here
    would mean 'nothing is shared, evict freely' — the wrong guess."""
    root, status, calls = wired
    _seed(root, PID, {"maybe-private": _env_row("env:v1:bbb")})
    status["env:v1:bbb"] = _ready()

    def _boom():
        raise RuntimeError("registry.json unreadable")
    monkeypatch.setattr("core.projects.list_projects", _boom)

    out = reclaim.reclaim(PID, confirm=True)

    assert calls["evicted"] == []
    assert [e["name"] for e in out["unknown"]] == ["maybe-private"]


def test_an_offline_substrate_evicts_nothing_and_still_deletes(wired,
                                                               monkeypatch):
    root, status, calls = wired
    _seed(root, PID, {"unknowable": _env_row("env:v1:ccc")})
    _seed(root, OTHER, {})

    def _boom():
        raise RuntimeError("substrate unreachable")
    monkeypatch.setattr("core.compute.adapter.get_compute", _boom)

    out = reclaim.reclaim(PID, confirm=True)

    assert calls["evicted"] == []
    assert [e["name"] for e in out["unknown"]] == ["unknowable"]
    assert "unreachable" in out["unknown"][0]["reason"]


def test_one_refusing_evict_never_blocks_the_rest(wired, monkeypatch):
    root, status, calls = wired
    _seed(root, PID, {"good": _env_row("env:v1:g"),
                      "bad": _env_row("env:v1:b")})
    _seed(root, OTHER, {})
    status["env:v1:g"] = _ready()
    status["env:v1:b"] = _ready()

    def _evict(pid, name, site=None):
        if name == "bad":
            raise RuntimeError("env.evict_blocked")
        calls["evicted"].append(name)
        return {"freed_bytes": 7}
    monkeypatch.setattr(named_envs, "evict", _evict)

    out = reclaim.reclaim(PID, confirm=True)

    assert calls["evicted"] == ["good"]
    assert any("bad" in e for e in out["errors"])


def test_sessions_stop_before_envs_are_evicted(wired):
    """A live session holds its base env against env_evict (weft raises
    env.evict_blocked), so evicting first refuses on exactly the envs worth
    reclaiming."""
    root, status, calls = wired
    _seed(root, PID, {"e1": _env_row("env:v1:e1")})
    _seed(root, OTHER, {})
    status["env:v1:e1"] = _ready()

    reclaim.reclaim(PID, confirm=True)

    assert calls["order"] == ["stop_sessions", "evict:e1"]


def test_the_preview_touches_nothing(wired):
    root, status, calls = wired
    _seed(root, PID, {"e1": _env_row("env:v1:e1")})
    _seed(root, OTHER, {})
    status["env:v1:e1"] = _ready(bytes_=2_500_000)

    p = reclaim.plan(PID)

    assert calls == {"evicted": [], "stopped": [], "order": []}
    assert p["reclaimable_bytes"] == 2_500_000
    assert "valued" in p and "project_dir" in p["valued"]
    assert "apparent" in p["bytes_note"]        # never sold as freed disk


def test_the_valued_rollup_is_not_reported_for_another_project(wired,
                                                               monkeypatch):
    """`data_ledger`'s project_id is decorative — it reads the ACTIVE
    project's graph. Asking it about the project being deleted, while a
    different one is open, returns the WRONG project's kept results with no
    sign anything is off. A number that describes something else is worse
    than no number."""
    root, status, calls = wired
    _seed(root, PID, {})
    _seed(root, OTHER, {})
    monkeypatch.setattr("core.projects.current", lambda: OTHER)
    monkeypatch.setattr(
        "core.data.ledger.data_ledger",
        lambda pid=None: pytest.fail("the ledger must not be consulted "
                                     "for a project it cannot scope to"))

    p = reclaim.plan(PID)

    assert p["valued"]["unknown"] is True
    assert "ACTIVE project" in p["valued"]["detail"]


def test_the_valued_rollup_is_reported_for_the_open_project(wired,
                                                            monkeypatch):
    root, status, calls = wired
    _seed(root, PID, {})
    _seed(root, OTHER, {})
    monkeypatch.setattr("core.projects.current", lambda: PID)
    monkeypatch.setattr("core.data.ledger.data_ledger",
                        lambda pid=None: {"totals": {"items": 3, "at_risk": 1},
                                          "degraded": False})

    p = reclaim.plan(PID)

    assert p["valued"]["valued_items"] == 3
    assert p["valued"]["at_risk"] == 1


def test_an_unrealized_env_is_not_counted_as_reclaimable(wired):
    """Registered but never built: no disk to give back. Counting it would
    make the delete card promise bytes that do not exist."""
    root, status, calls = wired
    _seed(root, PID, {"never-built": _env_row("env:v1:ddd")})
    _seed(root, OTHER, {})
    status["env:v1:ddd"] = {"realizations": [{"site": "local",
                                              "state": "missing"}]}

    p = reclaim.plan(PID)

    assert p["reclaimable_bytes"] == 0
    assert [e["name"] for e in p["not_realized"]] == ["never-built"]


def test_delete_project_reclaims(tmp_path, monkeypatch, wired):
    """The wiring itself: delete_project must run the sweep, not just stop
    sessions. This is the guard that was red before the reclaim lane existed."""
    import contextlib

    from core import projects

    root, status, calls = wired
    _seed(root, PID, {"scrublet-env": _env_row("env:v1:aaa")})
    _seed(root, OTHER, {})
    status["env:v1:aaa"] = _ready()

    monkeypatch.setattr(projects, "_single", lambda: False)
    monkeypatch.setattr(projects, "_db_file",
                        lambda pid: tmp_path / pid / "project.db")

    @contextlib.contextmanager
    def _reg():
        yield [{"id": PID}, {"id": OTHER}]
    monkeypatch.setattr(projects, "_locked_registry", _reg)

    out = projects.delete_project(PID)

    assert calls["stopped"] == [PID]
    assert calls["evicted"] == ["scrublet-env"], (
        "delete_project left the project's own envs on disk")
    assert out["freed_bytes"] == 1_000_000
