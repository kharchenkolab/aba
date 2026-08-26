"""Build an ABA_HOME that has a PAST, for gates to run against.

Every instrument in this repo has tested a home created seconds earlier by
`mktemp -d`, holding one project with no envs, no history and no data. That is
the easiest configuration the system has, and nobody uses it. Real homes
accumulate: named envs from earlier attempts, session installs recorded against
a base that has since moved, snapshots, an entity graph.

The gap is not academic. On 2026-08-26 a routine pack bump made a real
project's R session permanently unusable — a recorded `cran` addition replayed
onto a new base that had absorbed the same package, against a repo set that
could not resolve it, with `base_env_id` never advancing so the failure
repeated on every call. Three separate defects, all of them reachable only from
accumulated state, none of them reachable from any fixture we had.

SYNTHESISED, not copied. A snapshot of someone's real home would carry their
content and rot as their work moved on; this reproduces the SHAPE — the fields
that select code paths — with generic names. Add a field here when a code path
turns on it, not when it merely exists.

    from lived_in_home import build
    build(Path(home))          # then boot a server with ABA_HOME=home
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# A base id that is deliberately NOT any pack we ship: the point of the fixture
# is that the project was last used under a base that has since moved, so
# `project_env.ensure` takes the rebuild-and-replay path rather than the
# happy one. Using a real old id would silently stop exercising it the day
# that version left the catalog.
STALE_BASE_R = "env:v2:" + "0" * 64
STALE_BASE_PY = "env:v1:" + "1" * 64

# TWO recorded additions, because the incident had two distinct shapes and only
# one of them is obvious:
#
#  * REDUNDANT — a package the base has since absorbed. This is what actually
#    broke a real session: replaying it was unnecessary AND fatal. It must name
#    something the shipped pack really provides, or the reconcile path is never
#    reached. `Matrix` is in the pack's own verify list and is a general-purpose
#    library, so the fixture stays domain-neutral.
#  * UNRESOLVABLE — a name nothing can supply, standing in for the legacy
#    record whose repo set was never captured. Exercises the quarantine path:
#    the rebuild must survive it rather than strand the session forever.
#
# Neither carries `opts.cran_repos`: these are legacy records, written before
# the repo set was captured, which is exactly why they cannot be re-solved.
REDUNDANT_CRAN_ADDITION = {
    "eco": "cran",
    "specs": ["Matrix"],
    "at": 1787000000.0,
}
UNRESOLVABLE_CRAN_ADDITION = {
    "eco": "cran",
    "specs": ["pkgalpha-does-not-exist"],
    "at": 1787000001.0,
}
LEGACY_CRAN_ADDITION = REDUNDANT_CRAN_ADDITION   # back-compat alias


def _project(root: Path, pid: str, *, named_envs: int = 0,
             additions: "list | None" = None, snapshot: bool = False,
             entities: int = 0) -> None:
    d = projects_dir(root) / pid
    for sub in ("artifacts", "data", "entities", "work"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    (d / "TITLE.txt").write_text(f"fixture {pid}\n")

    reg: dict = {
        "envs": {f"env-{i}": {"language": "r", "env_id": f"env:v1:{i:064d}",
                              "created_at": 1787000000.0 + i}
                 for i in range(named_envs)},
        "active": {},
        "default": {
            "python": {"session_id": "ses_fixture_py",
                       "base_env_id": STALE_BASE_PY, "rev": 0,
                       "snapshot": None, "created_at": 1787000000.0},
            "r": {"session_id": "ses_fixture_r",
                  "base_env_id": STALE_BASE_R,
                  "additions": list(additions or []),
                  "rev": 1 if additions else 0,
                  "snapshot": ({"env_id": "env:v2:" + "2" * 64, "at_rev": 1,
                                "at": 1787000000.0} if snapshot else None),
                  "created_at": 1787000000.0},
        },
    }
    (d / "weft_envs.json").write_text(json.dumps(reg, indent=1))

    # A non-empty entity graph, built with the REAL schema. A hand-rolled db
    # with just `entities` and `entity_edges` looked right and made
    # `POST /projects/{id}/open` return HTTP 500 for every request: the server
    # expects the full set of tables and the bootstrapped `workspace` row. A
    # fixture must CARRY what the real thing always carries — a partial fake
    # does not test an easier case, it tests an impossible one.
    _init_project_db(d / "project.db")
    if entities:
        db = sqlite3.connect(d / "project.db")
        cols = {r[1] for r in db.execute("PRAGMA table_info(entities)")}
        for i in range(entities):
            row = {"id": f"ana_{i:08d}", "type": "analysis",
                   "title": f"run {i}", "status": "active",
                   "created_at": "2026-08-20T00:00:00Z",
                   "updated_at": "2026-08-20T00:00:00Z",
                   "data": "{}", "metadata": "{}"}
            use = {k: v for k, v in row.items() if k in cols}
            db.execute(f"INSERT OR REPLACE INTO entities ({','.join(use)}) "
                       f"VALUES ({','.join('?' * len(use))})", tuple(use.values()))
        db.commit()
        db.close()


def _init_project_db(path: Path) -> None:
    """Create a project db with the server's own schema, not an approximation."""
    import os
    import sys
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "backend"))
    prev = os.environ.get("ABA_DB_PATH")
    os.environ["ABA_DB_PATH"] = str(path)
    try:
        for mod in [m for m in list(sys.modules) if m.startswith("core.")]:
            sys.modules.pop(mod, None)
        from core.graph import _schema
        _schema.DB_PATH = path
        _schema.init_db()
    finally:
        if prev is None:
            os.environ.pop("ABA_DB_PATH", None)
        else:
            os.environ["ABA_DB_PATH"] = prev


# The server reads projects from RUNTIME_DIR/projects, and RUNTIME_DIR defaults
# to $ABA_HOME/runtime — not $ABA_HOME/projects. Seeding one directory up
# produced a home the server could not see at all: three lived-in runs in a row
# printed the banner, reported 33/33, and the surface audit said `audited 0
# project(s)` in the same output. Derive the path from config rather than
# guessing it; `test_lived_in_fixture` pins the derivation.
_PROJECTS_REL = ("runtime", "projects")


def projects_dir(home: Path) -> Path:
    """Where THIS server will look for projects under `home`."""
    return Path(home).joinpath(*_PROJECTS_REL)


def build(home: Path) -> dict:
    """Create a lived-in ABA_HOME at `home`. Returns a summary of what it holds."""
    home = Path(home)
    projects_dir(home).mkdir(parents=True, exist_ok=True)

    # The project that reproduces the incident: named envs from earlier
    # attempts, a recorded cran addition, a snapshot, and a stale base.
    _project(home, "prj_lived_in", named_envs=3,
             additions=[REDUNDANT_CRAN_ADDITION, UNRESOLVABLE_CRAN_ADDITION],
             snapshot=True, entities=5)
    # A second, quieter project — homes hold more than one, and several sweeps
    # iterate every project they find.
    _project(home, "prj_quiet", named_envs=0, entities=1)

    # REGISTER them. A project the server cannot see is not a fixture, it is a
    # directory. `core.projects` lists from `projects/registry.json`, NOT from
    # what is on disk — so the first two lived-in runs seeded the home, printed
    # the banner, and ran against a server reporting ZERO projects. Both passed.
    # The on-disk tree is necessary and not sufficient; this is the sufficient
    # half, and `test_lived_in_fixture` asserts the registry agrees with disk.
    now = "2026-08-20T00:00:00Z"
    (projects_dir(home) / "registry.json").write_text(json.dumps([
        {"id": "prj_lived_in", "name": "lived in", "created_at": now,
         "last_touched": now},
        {"id": "prj_quiet", "name": "quiet", "created_at": now,
         "last_touched": now},
    ], indent=2))

    return {
        "home": str(home),
        "projects": 2,
        "incident_project": "prj_lived_in",
        "named_envs": 3,
        "recorded_additions": 2,
        "stale_base": True,
        "built_at": time.time(),
    }


if __name__ == "__main__":   # pragma: no cover - operator convenience
    import sys
    print(json.dumps(build(Path(sys.argv[1])), indent=1))
