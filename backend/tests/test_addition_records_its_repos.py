"""A recorded session addition must carry the REPO SET it was solved against.

The ranked lane (`ensure_capability` → `project_env.ensure_ranked`) hands weft
`cran_repos` (the Bioconductor set) so a Bioc package resolves. It then recorded
the addition WITHOUT them. Everything looked fine — the package installed, the
session worked — until the base pack changed. Then the rebuild replays the
addition against the base's CRAN snapshot alone, cannot find the package, and
EVERY r call in that session fails at realize. weft answers `cran_no_candidates`,
whose r_repositories lever names exactly this cause.

That is a defect with a delay fuse: it is invisible at install time and fires on
someone else's release. So the guard is on the RECORD, not on the install.

Live 2026-08-26 (prj_c30313c2): five isolated envs and a permanently broken R
session, from additions that had installed cleanly.
"""
import pytest

from core.compute import named_envs, project_env

PID = "prj_repos"
BIOC = ["https://bioconductor.org/packages/3.20/bioc",
        "https://bioconductor.org/packages/3.20/data/annotation"]


@pytest.fixture
def ranked(monkeypatch):
    """A substrate whose ranked verb installs whatever it is asked for, and a
    registry row we can read back."""
    row = {"session_id": "sess_1", "base_env_id": "env:v1:base",
           "additions": [], "rev": 0}
    seen: dict = {}

    class _Ad:
        def ensure_available(self, target, names, lanes=None, verify=None,
                             **kw):
            seen["kw"] = kw
            return {"attempts": [{"lane": lanes[0], "package": names[0],
                                  "spelling": names[0],
                                  "outcome": "installed"}],
                    "runtime": {"source": "session", "prefix": "/p"}}

        def session_install(self, sid, **kw):
            seen["install"] = kw
            return {"runtime": {"source": "session", "prefix": "/p"}}

    monkeypatch.setattr(named_envs, "_sync", lambda x: x)
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _Ad())
    monkeypatch.setattr(project_env, "ensure",
                        lambda pid, lang: {"session_id": "sess_1",
                                           "runtime": {"source": "session",
                                                       "prefix": "/p"}})
    monkeypatch.setattr(project_env, "get", lambda pid, lang: row)
    monkeypatch.setattr(project_env, "_save_row",
                        lambda pid, lang, r: row.update(r))
    monkeypatch.setattr(project_env, "_check_envelope_soft", lambda out: [])
    monkeypatch.setattr(project_env, "_current_runtime",
                        lambda sid: {"source": "session", "prefix": "/p"})
    return row, seen


def test_a_cran_addition_records_the_repos_it_was_solved_against(ranked):
    row, seen = ranked

    project_env.ensure_ranked(PID, "r", ["DESeq2"], lanes=["cran"],
                              cran_repos=BIOC)

    add = row["additions"][-1]
    assert add["eco"] == "cran" and add["specs"] == ["DESeq2"]
    assert (add.get("opts") or {}).get("cran_repos") == BIOC, (
        "the addition was recorded without the repo set that made it "
        "resolvable — the next base change replays it against CRAN alone")


def test_a_recorded_addition_replays_with_its_repos(ranked):
    """The round trip: what the record holds is what the replay sends."""
    row, seen = ranked
    project_env.ensure_ranked(PID, "r", ["DESeq2"], lanes=["cran"],
                              cran_repos=BIOC)

    from core.compute import adapter as _adapter
    project_env._replay_one(_adapter.get_compute(), "sess_2",
                            row["additions"][-1])

    assert seen["install"].get("cran_repos") == BIOC


def test_a_lane_that_does_not_consume_repos_records_none(ranked):
    """Only the cran lane takes cran_repos; recording it on a pypi addition
    would put a meaningless kwarg into a future replay."""
    row, seen = ranked

    project_env.ensure_ranked(PID, "python", ["scanpy"], lanes=["pypi"],
                              cran_repos=BIOC)

    assert "opts" not in row["additions"][-1]


def test_no_repos_passed_records_no_opts(ranked):
    row, seen = ranked

    project_env.ensure_ranked(PID, "r", ["Matrix"], lanes=["cran"])

    assert "opts" not in row["additions"][-1]
