"""Repairing an un-snapshottable default session.

A session stops being freezable when a recorded addition contradicts the base
pack's pins. Because a snapshot is how the default environment travels to
another machine, that one bad addition takes the project's whole remote lane
with it — and the substrate has no un-install verb. Repair is therefore a
registry edit plus a stop: prune the addition, stop the session, and let
`ensure()` rebuild from the base with the REMAINING additions replayed.

ARMED: the fakes record session_stop / rebuild calls, and the health tests
assert the snapshot was actually attempted — a repair that quietly did nothing
would fail these, not pass them. WIDE: covers dropping by spec name, dropping
the last addition, the refusal when nothing matches (must NOT stop the session),
an already-healthy session, and a project with no session at all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from core.compute.errors import ComputeError  # noqa: E402

CONFLICT = ComputeError(
    "env.solve_conflict", "spec 'aba-p1-default-python' is unsatisfiable as pinned",
    stage="solve", hints={"solver_message": "PKG-A needs D==1.6; base pins D==1.9"})


@pytest.fixture()
def env(monkeypatch):
    """An in-memory project_env registry + a recording fake substrate."""
    from core.compute import named_envs, project_env
    state = {
        "row": {"session_id": "ses_1", "base_env_id": "env:v1:base",
                "additions": [{"eco": "pypi", "specs": ["PKG-OK"], "at": 1.0},
                              {"eco": "pypi", "specs": ["PKG-BAD"], "at": 2.0}],
                "rev": 2,
                "snapshot": {"env_id": "env:v1:old", "at_rev": 1, "at": 1.0}},
        "stopped": [], "ensured": [], "snapshots": [],
        # which specs the (fake) solver refuses
        "unsolvable": {"pkg-bad"},
    }

    def _snapshot(pid, language):
        state["snapshots"].append((pid, language))
        specs = {str(s).lower()
                 for a in (state["row"].get("additions") or [])
                 for s in (a.get("specs") or [])}
        if specs & state["unsolvable"]:
            raise CONFLICT
        return "env:v1:fresh"

    def _ensure(pid, language):
        state["ensured"].append((pid, language))
        return {"session_id": state["row"]["session_id"]}

    class _Ad:
        def session_stop(self, sid):
            state["stopped"].append(sid)
            return {"stopped": True}

    monkeypatch.setattr(project_env, "get", lambda pid, lang: state["row"])
    monkeypatch.setattr(project_env, "_save_row",
                        lambda pid, lang, row: state.__setitem__("row", row))
    monkeypatch.setattr(project_env, "snapshot", _snapshot)
    monkeypatch.setattr(project_env, "ensure", _ensure)
    monkeypatch.setattr(named_envs, "_sync", lambda v: v)
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _Ad())
    return project_env, state


# ── diagnosis ────────────────────────────────────────────────────────────────

def test_health_reports_the_conflict_and_the_stale_snapshot(env):
    project_env, state = env
    h = project_env.snapshot_health("p1", "python")
    assert state["snapshots"], "ARMED: health must actually attempt the snapshot"
    assert h["ok"] is False
    assert h["error"]["code"] == "env.solve_conflict"
    assert "unsatisfiable as pinned" in h["error"]["detail"]
    assert h["error"]["hints"]["solver_message"]
    # the visible symptom that used to go unnoticed
    assert h["rev"] == 2 and h["at_rev"] == 1 and h["stale"] is True
    assert [a["specs"] for a in h["additions"]] == [["PKG-OK"], ["PKG-BAD"]]
    assert "repair" in h["fix"]


def test_health_of_a_solvable_session_is_ok(env):
    project_env, state = env
    state["unsolvable"] = set()
    # a real snapshot() stamps the row; mirror that so the re-read is exercised
    state["row"]["snapshot"] = {"env_id": "env:v1:fresh",
                                "at_rev": state["row"]["rev"]}
    h = project_env.snapshot_health("p1", "python")
    assert h["ok"] is True and h["env_id"] == "env:v1:fresh"
    assert "error" not in h
    # "ok but stale" is self-contradictory — a successful freeze updates the
    # row, so the report must reflect the POST-freeze state, not the pre- one
    # (seen live: a repaired session reported ok:true / at_rev:None / stale:true)
    assert h["stale"] is False and h["at_rev"] == h["rev"]


def test_health_never_raises_on_an_untyped_failure(env, monkeypatch):
    """WIDE — a diagnosis tool that can itself explode is useless in the exact
    situation it exists for."""
    project_env, _ = env
    monkeypatch.setattr(project_env, "snapshot",
                        lambda *a, **k: (_ for _ in ()).throw(KeyError("runtime")))
    h = project_env.snapshot_health("p1", "python")
    assert h["ok"] is False and h["error"]["code"] == "KeyError"


# ── repair ───────────────────────────────────────────────────────────────────

def test_repair_drops_named_spec_stops_session_and_replays_rest(env):
    project_env, state = env
    out = project_env.repair("p1", "python", drop_specs=["PKG-BAD"])
    assert [d["specs"] for d in out["dropped"]] == [["PKG-BAD"]]
    assert [k["specs"] for k in out["kept"]] == [["PKG-OK"]]
    assert state["stopped"] == ["ses_1"], "ARMED: the session must be stopped"
    assert state["ensured"] == [("p1", "python")], "ARMED: rebuild+replay must run"
    assert out["rebuilt"] is True
    # the registry is re-based on the kept set and the stale snapshot cleared,
    # so the next freeze re-solves the repaired spec instead of a cached id
    assert state["row"]["rev"] == 1 and state["row"]["snapshot"] is None
    assert [a["specs"] for a in state["row"]["additions"]] == [["PKG-OK"]]
    # and the verdict is verified, not assumed
    assert out["health"]["ok"] is True


def test_repair_drop_last_targets_the_newest_addition(env):
    project_env, state = env
    out = project_env.repair("p1", "python", drop_last=True)
    assert [d["specs"] for d in out["dropped"]] == [["PKG-BAD"]]
    assert out["health"]["ok"] is True


def test_repair_refuses_when_nothing_matches(env):
    """CEILING: a no-op repair must NOT stop a working session — that would
    turn a diagnostic call into an outage."""
    project_env, state = env
    out = project_env.repair("p1", "python", drop_specs=["not-installed"])
    assert out["dropped"] == [] and out["rebuilt"] is False
    assert state["stopped"] == [], "a no-match repair must not stop the session"
    assert state["ensured"] == []
    assert len(state["row"]["additions"]) == 2, "registry untouched"


def test_repair_of_unknown_project_is_a_noop(env, monkeypatch):
    project_env, state = env
    monkeypatch.setattr(project_env, "get", lambda pid, lang: None)
    out = project_env.repair("nope", "python", drop_last=True)
    assert out["rebuilt"] is False and out["dropped"] == []
    assert state["stopped"] == []


def test_repair_reports_a_still_broken_session_honestly(env):
    """If the dropped addition wasn't the culprit, `health` must say so rather
    than the caller assuming the repair worked."""
    project_env, state = env
    state["unsolvable"] = {"pkg-ok"}          # the OTHER one is the problem
    out = project_env.repair("p1", "python", drop_specs=["PKG-BAD"])
    assert out["dropped"] and out["rebuilt"] is True
    assert out["health"]["ok"] is False, "a repair that didn't fix it must say so"
