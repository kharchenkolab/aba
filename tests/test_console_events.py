"""Console observability envelope — guards for the `console` wire event, the
obs.emit helper, and the substrate-event → console mapping in the relay.

Armed: the mapping tests assert exact field values a stubbed-out mapping
could not produce (red-proven by reverting console_event_for to `return
None`). Wide: covers the degenerate shapes — unmapped family (dropped, not
crashed), empty payload, non-numeric wall_s, error/warn/info severity marks.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))


# ── wire event exists with the envelope contract ─────────────────────────────

def test_console_wire_event_contract():
    from core.runtime import wire
    spec = wire.EVENTS["console"]
    assert spec.channel == "notify"
    assert set(spec.required) == {"category", "verb"}
    assert set(spec.optional) == {"site", "severity", "summary", "dur_ms",
                                  "bytes", "status", "ref", "detail"}
    p = wire.console(category="data", verb="chunk backhaul", site="siteA",
                     bytes=1024, dur_ms=400, severity="info")
    assert p["type"] == "console" and p["site"] == "siteA"
    with pytest.raises(TypeError):
        wire.console(category="data", verb="x", nope=1)


# ── obs.emit: broadcasts, filters None, never raises ─────────────────────────

def test_obs_emit_broadcasts_clean_payload(monkeypatch):
    from core.runtime import notifications, obs
    got: list[dict] = []
    monkeypatch.setattr(notifications, "broadcast", got.append)
    obs.emit("data", "chunk backhaul", site="siteA", bytes=2048,
             dur_ms=None, status=None)
    assert got == [{"type": "console", "category": "data",
                    "verb": "chunk backhaul", "site": "siteA", "bytes": 2048}]


def test_obs_emit_swallows_failures(monkeypatch, capsys):
    from core.runtime import notifications, obs

    def boom(_):
        raise RuntimeError("bus down")
    monkeypatch.setattr(notifications, "broadcast", boom)
    obs.emit("data", "chunk backhaul")          # must not raise
    assert "dropped console event" in capsys.readouterr().out
    obs.emit("data", "x", not_a_field=1)        # builder rejects → swallowed


# ── substrate event mapping (pure fn in the relay) ───────────────────────────

def _map(ev):
    from core.web.routers.compute import console_event_for
    return console_event_for(ev)


def test_mapping_transfer_done_carries_facts():
    p = _map({"kind": "transfer.done", "site": "siteA", "job_id": "j-1",
              "src": "siteB", "via": "direct", "bytes_total": 88_000_000,
              "wall_s": 12.5})
    assert p is not None
    assert p["type"] == "console" and p["category"] == "data"
    assert p["verb"] == "transfer.done" and p["site"] == "siteA"
    assert p["bytes"] == 88_000_000 and p["dur_ms"] == 12500
    assert p["ref"] == "j-1" and p["severity"] == "info"
    assert p["detail"]["via"] == "direct"      # raw payload kept for expansion
    assert "site" not in p["detail"]           # no duplication of envelope keys


def test_mapping_severity_marks():
    assert _map({"kind": "job.failed", "site": "s"})["severity"] == "error"
    # an UNPLANNED kernel termination, vs the clean stop — these read identically
    # (both `info`) while a kernel was dying in a loop on a live site
    assert _map({"kind": "kernel.died", "site": "s"})["severity"] == "error"
    assert _map({"kind": "kernel.stopped", "site": "s"})["severity"] == "info"
    assert _map({"kind": "site.unreachable", "site": "s"})["severity"] == "error"
    assert _map({"kind": "realize.fallback", "site": "s"})["severity"] == "warn"
    assert _map({"kind": "session.snapshot_unverified"})["severity"] == "warn"
    assert _map({"kind": "kernel.started", "site": "s"})["severity"] == "info"


def test_mapping_families_route_to_categories():
    for kind, cat in [("kernel.started", "run"), ("job.done", "run"),
                      ("env.published", "env"), ("realize.staged", "env"),
                      ("retain.pinned", "data"), ("bootstrap.step", "compute"),
                      ("service.ready", "serve")]:
        p = _map({"kind": kind})
        assert p is not None and p["category"] == cat, kind


def test_mapping_degenerate_shapes():
    assert _map({"kind": "unknown_family.thing"}) is None   # unmapped → dropped
    assert _map({}) is None                                 # kindless → dropped
    p = _map({"kind": "job.done"})                          # bare event survives
    assert p == {"type": "console", "category": "run", "verb": "job.done",
                 "severity": "info"}
    p = _map({"kind": "job.done", "wall_s": "not a number", "exit_code": 0})
    assert "dur_ms" not in p and p["status"] == "0"         # 0 is a real status


def test_relay_pushes_console_and_legacy_compute(monkeypatch):
    """The relay callback double-publishes site kinds: legacy `compute` (the
    Settings tab contract) AND the structured `console` envelope."""
    from core.runtime import notifications
    from core.web.routers import compute as mod
    got: list[dict] = []
    monkeypatch.setattr(notifications, "broadcast", got.append)

    class _Comp:
        def subscribe_events(self, cb):
            self.cb = cb
    comp = _Comp()
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: comp)
    assert mod.wire_event_relay() is True
    comp.cb({"kind": "site.registered", "site": "siteA"})
    types = [p["type"] for p in got]
    assert types == ["compute", "console"]
    comp.cb({"kind": "transfer.done", "site": "siteA", "bytes_total": 5})
    assert [p["type"] for p in got[2:]] == ["console"]      # no legacy for data


# ── session events inherit their site (the ensure_capability pill) ───────────

def test_a_session_event_without_site_inherits_from_session_started():
    """weft's session.* lifecycle events (the ensure_capability path) name the
    SESSION but not the SITE, so their Console rows rendered without the
    local/remote chip every other row gets — and a capability install is one
    of the most site-specific things the console shows (live, 2026-08-09).
    session.started carries both; the relay remembers the mapping."""
    from core.web.routers.compute import console_event_for, _SESSION_SITE
    _SESSION_SITE.clear()
    started = console_event_for({"kind": "session.started",
                                 "session": "ses_1", "site": "mendel"})
    assert started and started.get("site") == "mendel"
    ensured = console_event_for({"kind": "session.ensure_done",
                                 "session": "ses_1", "satisfied": True})
    assert ensured and ensured.get("site") == "mendel", \
        "the ensure event lost its site chip again"


def test_an_unknown_session_stays_chipless_rather_than_guessing():
    """CEILING: no mapping → no site; a wrong chip is worse than none."""
    from core.web.routers.compute import console_event_for, _SESSION_SITE
    _SESSION_SITE.clear()
    ev = console_event_for({"kind": "session.ensure_done",
                            "session": "ses_never_seen", "satisfied": True})
    assert ev is not None and ev.get("site") is None


def test_an_event_with_its_own_site_is_never_overridden():
    from core.web.routers.compute import console_event_for, _SESSION_SITE
    _SESSION_SITE.clear()
    console_event_for({"kind": "session.started", "session": "s2", "site": "a"})
    ev = console_event_for({"kind": "session.installed", "session": "s2",
                            "site": "b"})
    assert ev.get("site") == "b"
