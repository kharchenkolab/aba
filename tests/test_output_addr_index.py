"""Addresses are RECORDED when learned, and lookups stop paying to re-learn.

The class (misc/addressing_and_the_missing_index.md): ABA learned where a
run's outputs live at keep/settle time — keeper attribution, target, site all
in hand — wrote none of it down, and answered later lookups by SEARCHING weft
per candidate run. The search got capped for cost and a click 404'd on a store
that existed (live 2026-08-08).

`core/graph/output_addr` is the write-time half. What this file holds:

  * **The hook is ARMED on the exact live shape**: a keep decision on a
    kernel-backed run — the store made by a still-running kernel, the one case
    with NO terminal receipt — must leave a row carrying (run, rel, site,
    target). Drop the hook and the first test here goes red, not a viewer
    three weeks later.
  * **A row replaces the expensive confirm** — the resolver's remote tier
    costs ~12 s/run; with a row present it must make ZERO locate calls
    (the fake locate raises).
  * **A row must NOT out-vote a complete terminal receipt.** A file kept live
    and deleted before the kernel stopped has a row AND a receipt proving
    absence; the receipt is newer truth. An index that resurrects it serves a
    launch that cannot succeed.
  * **Confirmed scan hits BACKFILL**, so each name pays the slow confirm at
    most once per project — old projects index themselves by being used.
  * Rows are upserts that keep the newest non-null facts: a sparser re-record
    must not erase an earlier `ref`.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.bio

_RT = tempfile.mkdtemp(prefix="aba_outaddr_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

NAME = "processed.data.zarr"


@pytest.fixture(autouse=True)
def _fresh_table():
    from core.graph import _schema, output_addr
    _schema.init_db()
    with _schema._conn() as c:
        output_addr._ensure(c)
        c.execute("DELETE FROM output_addr")
    yield


# ── the table itself ─────────────────────────────────────────────────────────

def test_record_and_lookup_round_trip():
    from core.graph import output_addr as oa
    n = oa.record([{"run_id": "ana_1", "rel": NAME, "site": "siteA",
                    "target": "krn_1", "kind": "dir", "bytes": 1234,
                    "state": "live", "source": "keep"}])
    assert n == 1
    rows = oa.by_name(NAME)
    assert len(rows) == 1 and rows[0]["site"] == "siteA" \
        and rows[0]["target"] == "krn_1" and rows[0]["name"] == NAME


def test_lookup_accepts_a_path_shaped_key():
    """Callers hand in whatever they hold — an abs path resolves by basename."""
    from core.graph import output_addr as oa
    oa.record([{"run_id": "ana_1", "rel": NAME, "site": "siteA"}])
    assert oa.by_name(f"/remote/.weft/kernels/krn_1/{NAME}")


def test_upsert_keeps_the_newest_nonnull_facts():
    """A later, sparser record (state flip at settle) must refresh state
    WITHOUT erasing the ref an earlier mint paid for."""
    from core.graph import output_addr as oa
    oa.record([{"run_id": "ana_1", "rel": NAME, "site": "siteA",
                "ref": "dref:abc", "state": "live", "source": "keep"}])
    oa.record([{"run_id": "ana_1", "rel": NAME, "state": "retained",
                "source": "settle"}])
    row = oa.by_name(NAME)[0]
    assert row["state"] == "retained" and row["ref"] == "dref:abc" \
        and row["site"] == "siteA"


def test_fill_ref_never_creates_a_row():
    """Identity without an address is not knowledge this table can serve."""
    from core.graph import output_addr as oa
    oa.fill_ref("ana_9", "ghost.bin", ref="dref:zzz")
    assert oa.by_name("ghost.bin") == []


def test_recording_never_raises_on_garbage():
    from core.graph import output_addr as oa
    assert oa.record([{"rel": None}, {"run_id": "x"}, {}, None and {}]) == 0


# ── THE hook: keep decision on a kernel-backed run writes the address ────────

def _keep_fixture(monkeypatch, *, live=True):
    """A real project DB with a kernel-backed run entity; the weft seams faked
    at the module boundaries runs.py already owns. Returns the run id."""
    from core.graph.entities import create_entity
    from content.bio.lifecycle import runs as R
    from core.compute import retention

    rid = create_entity(entity_type="analysis", title="run", metadata={
        "weft_targets": ["krn_live"],
        "run": {"sites": ["siteA"]},
    })                                     # returns the id string

    monkeypatch.setattr(R, "artifacts_for_run", lambda r: [], raising=False)
    # the store dir is what the jobdir scan contributes (harvest is file-only)
    monkeypatch.setattr(R, "_jobdir_store_dirs", lambda r: {NAME})
    monkeypatch.setattr(R, "_declared_output_names", lambda md: set())
    monkeypatch.setattr(R, "_disk_truth_includes",
                        lambda r, md, inc, produced: (set(), set(), []))
    monkeypatch.setattr(R, "_retained_so_far", lambda r: (set(), set()))
    monkeypatch.setattr(R, "run_durable_view", lambda r: {"summary": {}})
    monkeypatch.setattr(retention, "retain",
                        lambda t, include=None, label=None, **kw: {"ok": True})
    monkeypatch.setattr(R, "_live_inventory", lambda t, **kw: {
        "site": "siteA", "live": live,
        "entries": [{"path": f"{NAME}/zarr.json", "bytes": 100, "mtime": 5},
                    {"path": f"{NAME}/c/0", "bytes": 900, "mtime": 6}]})
    return rid


def test_THE_HOOK_a_keep_decision_records_the_address(monkeypatch):
    """The exact live shape: kernel still running (no receipt anywhere), the
    keep decision names the store — the moment the address was in hand and
    was historically dropped."""
    from content.bio.lifecycle import runs as R
    from core.graph import output_addr as oa
    rid = _keep_fixture(monkeypatch)
    out = R.set_keep_decision(rid, keep=[NAME])
    assert "error" not in out, out
    rows = [r for r in oa.by_name(NAME) if r["run_id"] == rid]
    assert rows, "keep decision applied but NO address row was written"
    r = rows[0]
    assert r["site"] == "siteA" and r["target"] == "krn_live"
    assert r["kind"] == "dir", "a store records as ONE row for its root"
    assert r["bytes"] == 1000, "dir bytes are the member sum"
    assert r["state"] == "live" and r["source"] == "keep"


def test_the_hook_survives_a_dead_inventory(monkeypatch):
    """WIDE: no inventory at all (site briefly unreachable) still records the
    address — a row with a target and no size is still an address."""
    from content.bio.lifecycle import runs as R
    from core.graph import output_addr as oa
    rid = _keep_fixture(monkeypatch)
    monkeypatch.setattr(R, "_live_inventory",
                        lambda t, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    R.set_keep_decision(rid, keep=[NAME])
    rows = [r for r in oa.by_name(NAME) if r["run_id"] == rid]
    assert rows and rows[0]["target"] == "krn_live"
    assert rows[0]["site"] == "siteA"          # falls back to the run's recorded site


def test_a_recording_failure_never_breaks_the_keep(monkeypatch):
    """Retention must never break a turn; neither may its bookkeeping."""
    from content.bio.lifecycle import runs as R
    from core.graph import output_addr as oa
    rid = _keep_fixture(monkeypatch)
    monkeypatch.setattr(oa, "record",
                        lambda rows: (_ for _ in ()).throw(RuntimeError("disk full")))
    out = R.set_keep_decision(rid, keep=[NAME])
    assert "error" not in out


# ── the read side: a row replaces the 12 s confirm ───────────────────────────

def _matches_fixture(monkeypatch, *, receipt, row=None):
    from content.bio.lifecycle import runs as R
    from core.compute import retention
    ents = [{"id": "ana_1", "type": "analysis",
             "metadata": {"weft_targets": ["krn_live"]}}]
    monkeypatch.setattr(R, "list_entities",
                        lambda type_filter=None, include_archived=False: ents)
    monkeypatch.setattr(retention, "inventories",
                        lambda tgts: {"inventories": {"krn_live": receipt}})
    if row:
        from core.graph import output_addr as oa
        oa.record([row])
    return R


def test_a_row_answers_WITHOUT_any_locate_call(monkeypatch):
    """ARMED: locate raises. With a receipt-less target (live kernel) and an
    index row, the match must come from the row alone."""
    R = _matches_fixture(
        monkeypatch,
        receipt={"error": "data.missing", "detail": "no inventory recorded"},
        row={"run_id": "ana_1", "rel": NAME, "site": "siteA",
             "target": "krn_live", "kind": "dir", "bytes": 1000})
    monkeypatch.setattr(R, "locate_run_output",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("paid the 12s confirm despite a row")))
    m = R._project_output_matches(NAME)
    assert len(m) == 1 and m[0][0] == "ana_1"
    assert m[0][1]["site"] == "siteA" and m[0][1]["target"] == "krn_live"


def test_a_row_does_NOT_resurrect_a_receipt_proven_absence(monkeypatch):
    """The ordering that keeps the index honest: a COMPLETE terminal receipt
    that does not name the file is newer truth than a keep-time row (kept
    live, deleted before stop). The row may only stand in for the confirm."""
    R = _matches_fixture(
        monkeypatch,
        receipt={"files": [{"path": "other.txt", "bytes": 1, "mtime": 1}]},
        row={"run_id": "ana_1", "rel": NAME, "site": "siteA",
             "target": "krn_live"})
    monkeypatch.setattr(R, "locate_run_output",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("locate on a proven absence")))
    assert R._project_output_matches(NAME) == []


def test_a_confirmed_scan_hit_BACKFILLS(monkeypatch):
    """Old projects index themselves by being used: the slow confirm runs once,
    writes its answer, and the next lookup takes the row path."""
    from core.graph import output_addr as oa
    R = _matches_fixture(
        monkeypatch,
        receipt={"error": "data.missing", "detail": "no receipt"})
    calls = {"n": 0}

    def locate(rid, name, **kw):
        calls["n"] += 1
        return {"locality": "remote", "site": "siteB", "size": 77,
                "kind": "dir", "target": "krn_live", "durability": "live"}
    monkeypatch.setattr(R, "locate_run_output", locate)
    R._project_output_matches(NAME)
    assert calls["n"] == 1
    rows = [r for r in oa.by_name(NAME) if r["run_id"] == "ana_1"]
    assert rows and rows[0]["site"] == "siteB" and rows[0]["source"] == "backfill"
    # second lookup: the row answers, the confirm does not run again
    R._project_output_matches(NAME)
    assert calls["n"] == 1, "the backfilled row did not absorb the second confirm"


def test_a_row_without_a_site_still_pays_the_confirm(monkeypatch):
    """WIDE: a degraded row (no site recorded) cannot synthesize a remote
    answer — it must fall through to the real resolver, not fabricate one."""
    R = _matches_fixture(
        monkeypatch,
        receipt={"error": "data.missing", "detail": "no receipt"},
        row={"run_id": "ana_1", "rel": NAME, "site": None, "target": "krn_live"})
    seen = {"n": 0}

    def locate(rid, name, **kw):
        seen["n"] += 1
        return None
    monkeypatch.setattr(R, "locate_run_output", locate)
    assert R._project_output_matches(NAME) == []
    assert seen["n"] == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))


# ── the certainty split: live kernels are probed only as a last resort ───────

def _two_run_fixture(monkeypatch, *, row_for_ana1):
    """ana_1: certain when given a row (receipt missing, row recorded).
    ana_2: UNCERTAIN — receipt missing, no row — a live kernel."""
    from content.bio.lifecycle import runs as R
    from core.compute import retention
    ents = [{"id": "ana_1", "type": "analysis",
             "metadata": {"weft_targets": ["krn_a"]}},
            {"id": "ana_2", "type": "analysis",
             "metadata": {"weft_targets": ["krn_b"]}}]
    monkeypatch.setattr(R, "list_entities",
                        lambda type_filter=None, include_archived=False: ents)
    monkeypatch.setattr(retention, "inventories", lambda tgts: {"inventories": {
        t: {"error": "data.missing", "detail": "no receipt"} for t in tgts}})
    if row_for_ana1:
        from core.graph import output_addr as oa
        oa.record([{"run_id": "ana_1", "rel": NAME, "site": "siteA",
                    "target": "krn_a", "kind": "dir", "bytes": 10}])
    return R


def test_live_kernels_are_NOT_probed_when_a_recorded_owner_exists(monkeypatch):
    """The cost fix, armed. Four unstopped kernels made every warm lookup pay
    ~7 s of live probes that kept answering "no" — with nothing to backfill,
    because a live kernel's absence is re-checkable state. A recorded owner
    answers alone; the maybe-in-flight duplicate defers to recorded
    provenance (and becomes REAL ambiguity the moment its receipt lands)."""
    R = _two_run_fixture(monkeypatch, row_for_ana1=True)
    monkeypatch.setattr(R, "locate_run_output",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("probed a live kernel past a recorded owner")))
    m = R._project_output_matches(NAME)
    assert [(r, l["site"]) for r, l in m] == [("ana_1", "siteA")]


def test_live_kernels_ARE_probed_when_nothing_recorded_answers(monkeypatch):
    """CEILING on the narrowing: with no certain owner the live tier is the
    only honest answer, and skipping it would make every live kernel's outputs
    unresolvable — the original 404's other half."""
    R = _two_run_fixture(monkeypatch, row_for_ana1=False)
    probed = []

    def locate(rid, name, **kw):
        probed.append(rid)
        return ({"locality": "remote", "site": "siteB", "size": 3,
                 "kind": "dir", "target": "krn_b", "durability": "live"}
                if rid == "ana_2" else None)
    monkeypatch.setattr(R, "locate_run_output", locate)
    m = R._project_output_matches(NAME)
    assert [(r, l["site"]) for r, l in m] == [("ana_2", "siteB")]
    assert sorted(probed) == ["ana_1", "ana_2"]
