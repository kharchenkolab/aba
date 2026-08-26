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
    add = _reg(home, "prj_lived_in")["default"]["r"]["additions"][0]
    assert add["eco"] == "cran"
    assert "cran_repos" not in (add.get("opts") or {})
    assert add["specs"], "an addition with no specs replays as a no-op"


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
