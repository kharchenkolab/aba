"""The repair for the one state the ledger can flag: a run whose outputs are
kept IN PLACE on a machine that no longer declares durable storage.

`ship_home` re-retains one row with dest='@workspace'; `secure_run_keeps` is
the run-level verb the Guide calls. Both are reached from a chat turn, so an
expected bad outcome (already home, no record, swept sandbox, too big) is a
RESULT, never an exception.

Run: python backend/tests/test_keep_repair.py   (or via pytest)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_keeprepair_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "k.db"))
_BACKEND = str(Path(__file__).resolve().parents[1])
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import core.compute.retention as ret            # noqa: E402
import core.data.ledger as lg                   # noqa: E402


def _row(target, site, in_place, **kw):
    r = {"target": target, "site": site, "in_place": in_place, "label": "run-1",
         "bytes": 1000, "state": "done",
         "selection": json.dumps({"include": ["figs/*.png"], "exclude": None,
                                  "layout": "label"})}
    r.update(kw)
    return r


def test_ship_home_replays_the_original_selection(monkeypatch):
    """The row records WHAT was kept. A repair that re-retained everything
    would quietly widen the keep — the user asked to move these files, not to
    make a new decision about which files matter."""
    seen = {}
    monkeypatch.setattr(ret, "retained",
                        lambda **kw: [_row("jb_1", "siteA", 1)])

    def _fake_retain(target, **kw):
        seen.update({"target": target, **kw})
        return {"files": 3, "bytes": 900, "state": "done",
                "location": {"site": "@workspace", "path": "/w/runs/run-1"}}
    monkeypatch.setattr(ret, "retain", _fake_retain)

    out = ret.ship_home("jb_1")
    assert out["ok"] is True and out["files"] == 3
    assert seen["dest"] == "@workspace"
    assert seen["include"] == ["figs/*.png"]
    assert seen["label"] == "run-1" and seen["layout"] == "label"
    assert seen["background"] is False, \
        "a queued retain flips the index row to in_place=0 BEFORE the bytes " \
        "move — the ledger would call it safe while it still was not"


def test_ship_home_refuses_a_set_over_the_cap(monkeypatch):
    """ARMED against the opposite mistake: the under-cap case in the same
    test must still ship, so a blanket refusal fails here too."""
    big = _row("jb_big", "siteA", 1, bytes=int(9e9))
    small = _row("jb_small", "siteA", 1, bytes=10)
    monkeypatch.setattr(ret, "retained", lambda **kw: [big, small])
    calls = []
    monkeypatch.setattr(ret, "retain",
                        lambda t, **kw: calls.append(t) or {"files": 1, "bytes": 10})

    refused = ret.ship_home("jb_big")
    assert refused["ok"] is False and refused["error"] == "too_large"
    assert "9.0 GB" in refused["note"] and "Settings" in refused["note"]
    assert calls == [], "a refusal must not have moved anything"

    assert ret.ship_home("jb_small")["ok"] is True
    assert calls == ["jb_small"]


def test_ship_home_reports_expected_outcomes_instead_of_raising(monkeypatch):
    """Called from a button and from a chat turn: an already-home keep, an
    unknown target and a swept sandbox are ANSWERS."""
    monkeypatch.setattr(ret, "retained",
                        lambda **kw: [_row("jb_home", "siteA", 0, moved=1)])
    home = ret.ship_home("jb_home")
    assert home["ok"] is True and home["already_home"] is True

    missing = ret.ship_home("jb_nope")
    assert missing["ok"] is False and missing["error"] == "unknown_keep"

    monkeypatch.setattr(ret, "retained", lambda **kw: [_row("jb_1", "siteA", 1)])

    def _boom(target, **kw):
        raise RuntimeError("selection matched no files")
    monkeypatch.setattr(ret, "retain", _boom)
    swept = ret.ship_home("jb_1")
    assert swept["ok"] is False and swept["error"] == "ship_failed"
    assert "matched no files" in swept["note"], \
        "the loss this button exists to prevent must be said out loud"


def test_secure_run_keeps_touches_only_the_rows_at_risk(monkeypatch):
    """A repair that moves more than the thing it was asked to repair is its
    own incident. Rows already home, and rows in place on DURABLE storage,
    are reported and left alone."""
    monkeypatch.setattr(lg, "_durable_map",
                        lambda: {"local": True, "siteA": None})
    rows = [_row("krn_ok", "local", 1),             # in place, durable
            _row("jb_home", "siteA", 0, moved=1),   # already shipped
            _row("jb_risky", "siteA", 1)]           # the only one at risk
    monkeypatch.setattr(ret, "retained", lambda **kw: rows)
    moved = []
    monkeypatch.setattr(ret, "ship_home",
                        lambda t: moved.append(t) or {"target": t, "ok": True})

    out = ret.secure_run_keeps("run-1")
    assert out["ok"] is True and out["moved"] == 1
    assert moved == ["jb_risky"]


def test_secure_run_keeps_says_so_when_nothing_is_wrong(monkeypatch):
    """The state the live ledger was actually in: every keep either durable or
    already home. The verb must not invent work."""
    monkeypatch.setattr(lg, "_durable_map",
                        lambda: {"local": True, "siteA": None})
    monkeypatch.setattr(ret, "retained", lambda **kw: [
        _row("krn_ok", "local", 1), _row("jb_home", "siteA", 0, moved=1)])
    monkeypatch.setattr(ret, "ship_home",
                        lambda t: (_ for _ in ()).throw(AssertionError("moved!")))
    out = ret.secure_run_keeps("run-1")
    assert out["ok"] is True and out["secured"] == [] and out["already_safe"] == 2


def test_secure_run_keeps_refuses_when_durability_is_unknown(monkeypatch):
    """WIDE — the degenerate substrate: with the durable map unreadable we
    cannot tell risk from safety, and guessing either way is worse than
    saying so. Nothing moves."""
    monkeypatch.setattr(lg, "_durable_map",
                        lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    monkeypatch.setattr(ret, "ship_home",
                        lambda t: (_ for _ in ()).throw(AssertionError("moved!")))
    out = ret.secure_run_keeps("run-1")
    assert out["ok"] is False and out["error"] == "durability_unknown"


def _standalone() -> int:
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in (test_ship_home_replays_the_original_selection,
              test_ship_home_refuses_a_set_over_the_cap,
              test_ship_home_reports_expected_outcomes_instead_of_raising,
              test_secure_run_keeps_touches_only_the_rows_at_risk,
              test_secure_run_keeps_says_so_when_nothing_is_wrong,
              test_secure_run_keeps_refuses_when_durability_is_unknown):
        mp = _MP()
        try:
            t(mp)
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
