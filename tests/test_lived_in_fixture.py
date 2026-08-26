"""The gate's fixture must have a PAST, and the fixture itself must be checked.

Every gate in this repo has run against a home created seconds earlier: one
project, no envs, no history, no data. That is the easiest configuration the
system has and nobody uses it. Three defects found on 2026-08-26 were reachable
ONLY from accumulated state — a recorded session addition, a base that had
moved since, named envs from earlier attempts — and therefore invisible to
every instrument we had, while those instruments reported green.

A fixture is also code, and a fixture that quietly degenerates to "empty" turns
every test built on it into a test of nothing. Its own properties are asserted
here, per the standing rule that a fake must CARRY what the real thing always
carries.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "regtest" / "harness"))


@pytest.fixture()
def home(tmp_path):
    from lived_in_home import build
    build(tmp_path)
    return tmp_path


def _reg(home: Path, pid: str) -> dict:
    return json.loads((home / "projects" / pid / "weft_envs.json").read_text())


def test_the_project_has_a_past(home):
    """THE point: named envs, a recorded addition, a snapshot, a stale base."""
    reg = _reg(home, "prj_lived_in")
    assert len(reg["envs"]) >= 3, "named envs from earlier attempts"
    r = reg["default"]["r"]
    assert r["additions"], "a recorded session addition"
    assert r["snapshot"], "a snapshot pinned under the old base"
    assert r["rev"] >= 1, "a session that has been modified"


def test_the_base_is_stale_so_a_bump_is_exercised(home):
    """A fixture whose base matches the current pack takes the HAPPY path and
    never reaches rebuild-and-replay — where all three defects lived."""
    from lived_in_home import STALE_BASE_R
    r = _reg(home, "prj_lived_in")["default"]["r"]
    assert r["base_env_id"] == STALE_BASE_R
    assert set(STALE_BASE_R.split(":")[-1]) == {"0"}, (
        "the stale base must be a synthetic id no pack can ever equal — a real "
        "old id stops exercising the bump the day it leaves the catalog")


def test_the_recorded_addition_reproduces_the_incident_shape(home):
    """cran lane, no captured repo set — the legacy record that could not be
    resolved on a new base. If this ever gains `opts.cran_repos` the fixture
    has stopped reproducing the case it exists for."""
    adds = _reg(home, "prj_lived_in")["default"]["r"]["additions"]
    assert len(adds) >= 2, "both shapes must be present, not just one"
    for add in adds:
        assert add["eco"] == "cran"
        assert "cran_repos" not in (add.get("opts") or {}), (
            "these are LEGACY records — capturing a repo set would stop them "
            "reproducing the case they exist for")
        assert add["specs"], "an addition with no specs replays as a no-op"
    # one must be reconcilable against the shipped pack, one must not
    from lived_in_home import REDUNDANT_CRAN_ADDITION, UNRESOLVABLE_CRAN_ADDITION
    packs = (REPO / "install" / "core" / "envs" / "r_bio.yaml").read_text()
    assert REDUNDANT_CRAN_ADDITION["specs"][0] in packs, (
        "the redundant addition must name something the pack REALLY provides, "
        "or the reconcile path is never exercised")
    assert UNRESOLVABLE_CRAN_ADDITION["specs"][0] not in packs


def test_the_entity_graph_is_not_empty(home):
    """Several paths branch on 'is this a new project'. An empty db takes the
    new-project branch and silently tests the thing we are not testing."""
    db = sqlite3.connect(home / "projects" / "prj_lived_in" / "project.db")
    n = db.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
    db.close()
    assert n >= 5, f"expected a populated graph, got {n} rows"


def test_the_home_holds_more_than_one_project(home):
    """Sweeps and audits iterate every project they find; a single-project home
    never exercises that."""
    assert len({p.name for p in (home / "projects").iterdir()}) >= 2


def test_a_fresh_home_would_fail_these_assertions(tmp_path):
    """ARMED: prove the fixture is doing work.

    If `mktemp -d` satisfied these, the fixture would be decoration and every
    gate built on it would be measuring nothing — which is precisely the state
    the fixture was written to end."""
    (tmp_path / "projects" / "prj_lived_in").mkdir(parents=True)
    with pytest.raises(Exception):
        _reg(tmp_path, "prj_lived_in")


def test_the_probe_can_run_inside_an_existing_project(monkeypatch):
    """Seeding a lived-in home is worthless if the probe still mints a fresh
    project per package.

    That is exactly what happened on the first lived-in run: the home was
    seeded, the banner said so, the gate reported 33/33 — and every package had
    been tested in a brand-new project created three seconds earlier. The
    fixture was decoration, one layer above the armed guard written to stop
    fixtures being decoration."""
    sys.path.insert(0, str(REPO / "regtest" / "harness"))
    import live_install_probe as lip

    created = []

    class _C:
        def post(self, path, **kw):
            created.append(path)
            return type("R", (), {"status_code": 200,
                                  "json": lambda _s: {"id": "thr_x"}})()

        def get(self, path, **kw):
            if path.startswith("/api/entities"):
                return type("R", (), {"json": lambda _s: []})()
            return type("R", (), {"json": lambda _s: {}})()

    monkeypatch.setattr(lip, "_env_count", lambda d, p: (0, 0))
    monkeypatch.setattr(lip, "_drive", lambda *a, **k: {
        "run_id": "r1", "tools": ["ensure_capability"], "errors": [], "text": [],
        "jobs": [], "cap_results": [{"status": "ready"}], "kinds": {"tool_start": 1}})

    lip.probe_one(_C(), {"name": "thing", "language": "r"}, timeout=1,
                  projects_dir=None, pack_names={}, project="prj_lived_in")
    assert not any(p == "/api/projects" for p in created), (
        "with --project set, the probe must NOT create a project: " + str(created))
    assert "/api/projects/prj_lived_in/open" in created, created


def test_an_unopenable_project_is_an_error_not_a_fresh_one(monkeypatch):
    """ARMED: falling back to a new project would turn 'we tested accumulated
    state' into 'we tested nothing' and report it as a pass."""
    sys.path.insert(0, str(REPO / "regtest" / "harness"))
    import live_install_probe as lip

    class _C:
        def post(self, path, **kw):
            return type("R", (), {"status_code": 404,
                                  "json": lambda _s: {}})()

        def get(self, path, **kw):
            return type("R", (), {"json": lambda _s: {}})()

    row = lip.probe_one(_C(), {"name": "thing", "language": "r"}, timeout=1,
                        projects_dir=None, pack_names={}, project="prj_missing")
    assert row["verdict"] == "error", row
    assert "refusing to fall back" in row["detail"], row
