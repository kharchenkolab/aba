"""Reclaiming the substrate held by DELETED projects.

A project's directory and its `weft_envs.json` survive the delete on purpose —
they are the recovery archive — so a project deleted before the reclaim lane
existed still names, on disk, exactly which envs it held. No extra bookkeeping
was needed; the orphan sweep is the ordinary plan run over ids that have a
directory but no registry row.

Everything here is about the ways that identification can go wrong. Getting it
wrong in the permissive direction evicts live projects' environments, so each
can't-tell case is tested for REFUSAL, not for cleverness.
"""
import json
import time

import pytest

from core.compute import named_envs, project_env, reclaim

LIVE = "prj_live"
GONE = "prj_deleted"
OLD = time.time() - 86400


def _seed(root, pid, envs, *, mtime=OLD):
    d = root / pid
    d.mkdir(parents=True, exist_ok=True)
    f = d / "weft_envs.json"
    f.write_text(json.dumps({"envs": envs, "active": {}, "default": {}}))
    import os
    os.utime(f, (mtime, mtime))
    os.utime(d, (mtime, mtime))


def _registry(root, ids):
    (root / "registry.json").write_text(
        json.dumps([{"id": i, "name": i} for i in ids]))


def _env_row(env_id, language="python"):
    return {"env_id": env_id, "language": language, "packages": [],
            "conda_packages": [], "history": [], "layers": []}


def _ready(bytes_=1_000_000, read_only=False):
    return {"realizations": [{"site": "local", "state": "ready",
                              "bytes": bytes_, "read_only": read_only}]}


@pytest.fixture
def home(tmp_path, monkeypatch):
    import core.config as cfg
    from core import projects
    monkeypatch.setattr(cfg, "PROJECTS_DIR", tmp_path, raising=False)
    monkeypatch.setattr(projects, "REGISTRY", tmp_path / "registry.json",
                        raising=False)
    monkeypatch.setattr(projects, "current", lambda: LIVE)
    monkeypatch.setattr(
        projects, "list_projects",
        lambda: json.loads((tmp_path / "registry.json").read_text()))

    status: dict = {}
    calls: dict = {"evicted": [], "stopped": []}
    monkeypatch.setattr(named_envs, "_sync", lambda x: x)
    monkeypatch.setattr(
        "core.compute.adapter.get_compute",
        lambda: type("C", (), {
            "env_status": lambda self, eid: status.get(eid, {"realizations": []})
        })())

    def _evict(pid, name, site=None):
        calls["evicted"].append(f"{pid}/{name}")
        return {"freed_bytes": 1_000_000}
    monkeypatch.setattr(named_envs, "evict", _evict)
    monkeypatch.setattr(project_env, "stop_all_sessions",
                        lambda pid: (calls["stopped"].append(pid),
                                     {"stopped": [], "errors": []})[1])
    return tmp_path, status, calls


def test_a_deleted_projects_envs_are_reclaimable(home):
    root, status, calls = home
    _registry(root, [LIVE])
    _seed(root, LIVE, {"live-env": _env_row("env:v1:live")})
    _seed(root, GONE, {"gone-env": _env_row("env:v1:gone")})
    status["env:v1:gone"] = _ready()

    out = reclaim.orphans(confirm=True)

    assert calls["evicted"] == [f"{GONE}/gone-env"]
    assert out["freed_bytes"] == 1_000_000


def test_a_live_projects_env_is_never_swept(home):
    """The live project holds the SAME env id. An orphan may not reclaim it."""
    root, status, calls = home
    _registry(root, [LIVE])
    _seed(root, LIVE, {"ours": _env_row("env:v1:shared")})
    _seed(root, GONE, {"theirs": _env_row("env:v1:shared")})
    status["env:v1:shared"] = _ready()

    out = reclaim.orphans(confirm=True)

    assert calls["evicted"] == []
    assert out["orphans"][0]["kept_shared"][0]["also_in"] == [LIVE]


def test_an_unreadable_registry_refuses_the_whole_sweep(home):
    """The catastrophic direction: `projects._load()` swallows a read error and
    returns [], which here would mean EVERY project on disk is an orphan."""
    root, status, calls = home
    _seed(root, LIVE, {"live-env": _env_row("env:v1:live")})
    _seed(root, GONE, {"gone-env": _env_row("env:v1:gone")})
    status["env:v1:live"] = _ready()
    status["env:v1:gone"] = _ready()
    (root / "registry.json").write_text('[{"id": "prj_live",')   # truncated

    out = reclaim.orphans(confirm=True)

    assert reclaim.orphan_ids() is None
    assert "refusing" in out["refused"]
    assert calls["evicted"] == []


def test_a_missing_registry_refuses_too(home):
    root, status, calls = home
    _seed(root, GONE, {"gone-env": _env_row("env:v1:gone")})
    status["env:v1:gone"] = _ready()

    out = reclaim.orphans(confirm=True)

    assert "refused" in out and calls["evicted"] == []


def test_an_empty_registry_is_trusted(home):
    """WIDE: the other side. An empty list is the legitimate state after
    deleting the last project — refusing there would strand every env."""
    root, status, calls = home
    _registry(root, [])
    _seed(root, GONE, {"gone-env": _env_row("env:v1:gone")})
    status["env:v1:gone"] = _ready()

    out = reclaim.orphans(confirm=True)

    assert calls["evicted"] == [f"{GONE}/gone-env"]


def test_a_freshly_created_project_is_not_an_orphan(home):
    """`_db_file` creates the project directory BEFORE the registry row is
    written, so a project being created looks exactly like an orphan."""
    root, status, calls = home
    _registry(root, [LIVE])
    _seed(root, LIVE, {})
    _seed(root, "prj_being_born", {"new-env": _env_row("env:v1:new")},
          mtime=time.time())
    status["env:v1:new"] = _ready()

    assert reclaim.orphan_ids() == []
    reclaim.orphans(confirm=True)
    assert calls["evicted"] == []


def test_the_current_project_is_never_an_orphan(home, monkeypatch):
    """Belt and braces: even if the registry row went missing, the project the
    server is serving right now is not deleted."""
    root, status, calls = home
    _registry(root, [])
    _seed(root, LIVE, {"live-env": _env_row("env:v1:live")})
    status["env:v1:live"] = _ready()

    assert reclaim.orphan_ids() == []


def test_a_directory_that_never_held_an_env_is_not_listed(home):
    root, status, calls = home
    _registry(root, [LIVE])
    _seed(root, LIVE, {})
    (root / "prj_empty").mkdir()

    assert reclaim.orphan_ids() == []


def test_reserved_and_internal_directories_are_skipped(home):
    """`_workspace`, `single` and the registry files share PROJECTS_DIR with
    real projects."""
    root, status, calls = home
    _registry(root, [LIVE])
    _seed(root, LIVE, {})
    for name in ("_workspace", "single"):
        _seed(root, name, {"x": _env_row("env:v1:x")})
    status["env:v1:x"] = _ready()

    assert reclaim.orphan_ids() == []


def test_the_plan_touches_nothing(home):
    root, status, calls = home
    _registry(root, [LIVE])
    _seed(root, LIVE, {})
    _seed(root, GONE, {"gone-env": _env_row("env:v1:gone")})
    status["env:v1:gone"] = _ready(bytes_=4_000_000)

    out = reclaim.orphans()

    assert calls == {"evicted": [], "stopped": []}
    assert out["reclaimable_bytes"] == 4_000_000
    assert "never touched" in out["note"]
