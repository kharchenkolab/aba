"""Data-safety ledger + site holdings (misc/more_weft_ui.md §1/§2).

Pure catalog projection: datasets (by home + durable declarations) and
retained runs land in exactly one state; holdings feed consequence cards.
Local-only quiescence contract: an all-local, all-safe project reports
multi_site=False and zero non-safe items — the UI renders NOTHING.

Run: python tests/test_data_ledger.py   (or via pytest)
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_ledger_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "l.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
import core.data.ledger as lg  # noqa: E402
import core.compute.retention as retmod  # noqa: E402
import core.compute.sites_config as scfg  # noqa: E402

init_db()


def _ds(title, **md):
    out = create_entity(entity_type="dataset", title=title, metadata=md)
    return out if isinstance(out, str) else out["id"]


def test_local_only_project_is_quiet(monkeypatch):
    """The §-quiet contract at the DATA layer: all-local & safe → nothing to
    render (multi_site False, zero non-safe). The UI snapshot test rides this."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": "runL", "site": "local", "in_place": 1, "bytes": 7, "state": "done"}])
    led = lg.data_ledger()
    local_items = [i for i in led["items"] if i["entity_id"] == "runL"]
    assert local_items and local_items[0]["state"] == "safe"
    assert led["multi_site"] is False and led["remote_sites"] == []
    assert led["totals"]["at_risk"] == 0 and led["totals"]["changed"] == 0


def test_ledger_states_and_quiescence(monkeypatch):
    # sites: siteB durable, siteC NOT durable
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteB", "kind": "ssh", "config": {"durable": True}},
        {"name": "siteC", "kind": "ssh", "config": {}},
    ])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    a = _ds("cas-backed", ref="dref:abc", origin_class="url", source_key="u:x")
    b = _ds("durable-home", home={"site": "siteB", "path": "/data/x"},
            descriptor={"bytes": 123})
    c = _ds("risky-home", home={"site": "siteC", "path": "/tmp/y"})
    d = _ds("drifted", home={"site": "siteB", "path": "/data/z"}, source_changed=True)
    led = lg.data_ledger()
    st = {i["entity_id"]: i["state"] for i in led["items"]}
    assert st[a] == "safe" and st[b] == "safe"
    assert st[c] == "at_risk"
    assert st[d] == "changed"
    assert led["totals"]["at_risk"] == 1 and led["totals"]["changed"] == 1
    assert led["multi_site"] is True and "siteC" in led["remote_sites"]


def test_keeps_state_follows_durable_declaration(monkeypatch):
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteB", "kind": "ssh", "config": {"durable": True}},
        {"name": "siteC", "kind": "ssh", "config": {}},   # declaration revoked
    ])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": "run1", "site": "siteB", "in_place": 1, "bytes": 10, "state": "done"},
        {"label": "run2", "site": "siteC", "in_place": 1, "bytes": 20, "state": "done"},
        {"label": "run3", "site": "local", "in_place": 0, "bytes": 5, "state": "done"},
        {"label": "dead", "site": "siteC", "in_place": 1, "bytes": 9, "state": "failed"},
    ])
    items = {i["entity_id"]: i for i in lg._keep_items(lg._durable_map())}
    assert items["run1"]["state"] == "safe"
    assert items["run2"]["state"] == "at_risk"       # in place, no durable promise
    assert items["run3"]["state"] == "safe"          # shipped home
    assert "dead" not in items                       # failed rows aren't keeps



def test_site_holdings_counts_keeps_and_homes(monkeypatch):
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteB", "kind": "ssh", "config": {"durable": True}}])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": "runX", "site": "siteB", "in_place": 1, "bytes": 40, "state": "done"}]
        if kw.get("site") == "siteB" else [])
    h_ds = _ds("home-on-B", home={"site": "siteB", "path": "/data/h"})
    h = lg.site_holdings("siteB")
    assert h["kept_runs"] == 1 and h["kept_bytes"] == 40
    assert any(x["entity_id"] == h_ds for x in h["dataset_homes"])
    assert h["at_risk_if_gone"] == 1 + len(h["dataset_homes"])


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
    for t in (test_local_only_project_is_quiet,
              test_ledger_states_and_quiescence,
              test_keeps_state_follows_durable_declaration,
              test_site_holdings_counts_keeps_and_homes):
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
