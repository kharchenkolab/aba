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


def test_a_sandbox_witness_row_does_NOT_resurrect_a_receipt_proven_absence(monkeypatch):
    """The ordering that keeps the index honest — SCOPED to what a receipt can
    actually prove: a COMPLETE terminal receipt that does not name the file is
    newer truth about the SANDBOX, so a row that only witnessed sandbox
    presence (a backfill from a live locate) may not resurrect it (the
    paths.md F1 shadow). A row witnessing a KEEP is a different claim — about
    the retained tree, which the receipt cannot speak to — covered below."""
    R = _matches_fixture(
        monkeypatch,
        receipt={"files": [{"path": "other.txt", "bytes": 1, "mtime": 1}]},
        row={"run_id": "ana_1", "rel": NAME, "site": "siteA",
             "target": "krn_live", "source": "backfill", "state": "in-sandbox"})
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


# ── keeps out-vote a receipt about the SANDBOX — never about the keep tree ───
# The live break (2026-08-12): durable view showed kept.csv retained with a
# URL; the name door 404'd. Candidacy read receipts + the index only, so a
# retained file whose jobdir copy was gone at kernel stop — the very shape
# keeping exists FOR — was excluded as "receipt-proven absent", and the local
# pass (scoped to candidates) never consulted locate's retained-tree tier.
# The corrected rule: a complete receipt proves absence IN THE SANDBOX;
# recorded keeps (retention selections, keep/settle-witness rows) are truth
# about the retained tree and keep the run a candidate.

import json as _json


def _keeps_fixture(monkeypatch, *, receipt, row=None, retained=None):
    """Like _matches_fixture, with weft's retention index under test control."""
    from core.compute import retention
    R = _matches_fixture(monkeypatch, receipt=receipt, row=row)
    if retained is None:
        monkeypatch.setattr(retention, "retained", lambda *a, **k: [])
    elif isinstance(retained, Exception):
        monkeypatch.setattr(retention, "retained",
                            lambda *a, **k: (_ for _ in ()).throw(retained))
    else:
        monkeypatch.setattr(retention, "retained", lambda *a, **k: retained)
    return R


def _no_locate(R, monkeypatch, msg):
    monkeypatch.setattr(R, "locate_run_output",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError(msg)))


def test_a_keep_row_keeps_the_run_reachable_past_a_complete_receipt(monkeypatch):
    """A keep-time row (source='keep' — state may honestly be 'live': keeps
    happen against live kernels) survives a complete receipt that lacks the
    name, and answers WITHOUT a locate round-trip."""
    R = _keeps_fixture(
        monkeypatch,
        receipt={"files": [{"path": "other.txt", "bytes": 1, "mtime": 1}]},
        row={"run_id": "ana_1", "rel": NAME, "site": "siteA",
             "target": "krn_live", "source": "keep", "state": "live"})
    _no_locate(R, monkeypatch, "keep row should answer without a confirm")
    m = R._project_output_matches(NAME)
    assert [(r, l["site"]) for r, l in m] == [("ana_1", "siteA")]


def test_a_backfill_row_that_witnessed_the_retained_tree_survives(monkeypatch):
    """WIDE (the state side of the witness): a backfill row whose locate found
    the file RETAINED records state='retained' — that is keep-tree truth, not a
    sandbox echo, so a later complete receipt cannot erase it."""
    R = _keeps_fixture(
        monkeypatch,
        receipt={"files": [{"path": "other.txt", "bytes": 1, "mtime": 1}]},
        row={"run_id": "ana_1", "rel": NAME, "site": "siteA",
             "target": "krn_live", "source": "backfill", "state": "retained"})
    _no_locate(R, monkeypatch, "retained-witness row should answer alone")
    m = R._project_output_matches(NAME)
    assert [(r, l["site"]) for r, l in m] == [("ana_1", "siteA")]


def test_recorded_retention_keeps_candidacy_with_no_row_at_all(monkeypatch):
    """The reported live shape: keep predates the index (no row), the terminal
    receipt lacks the name — but weft's retention index records the keep
    (selection include, label = run). The run must stay a candidate, and the
    LOCAL pass must consult it (the retained/keep tree is a local tier).
    Also WIDE: the keep rel lives under a subdir; the query is the bare name."""
    R = _keeps_fixture(
        monkeypatch,
        receipt={"files": [{"path": "other.txt", "bytes": 1, "mtime": 1}]},
        retained=[{"label": "ana_1", "state": "done", "target": "krn_live",
                   "selection": _json.dumps({"include": ["outputs/" + NAME]})}])
    cands = R._output_candidacy(NAME)
    assert [(rid, certain) for rid, _row, certain in cands] == [("ana_1", True)]
    monkeypatch.setattr(R, "locate_run_output",
                        lambda rid, name, **kw: {
                            "local_path": "/keep/ana_1/" + NAME, "rel": NAME,
                            "locality": "local", "site": "local",
                            "durability": "retained", "size": 42, "kind": "dir"})
    got = R._locate_project_run_output(NAME)
    assert got is not None and got[0] == "ana_1" and got[3] is False, \
        "retained file unreachable by name while the durable view lists it"


def test_no_keep_no_row_complete_receipt_stays_excluded(monkeypatch):
    """CEILING: the F1-shadow protection is retained — with NO recorded keep
    and no row, a complete receipt that does not name the file still excludes
    the run (a same-named stray in a non-producing run must not shadow)."""
    R = _keeps_fixture(
        monkeypatch,
        receipt={"files": [{"path": "other.txt", "bytes": 1, "mtime": 1}]})
    assert R._output_candidacy(NAME) == []
    _no_locate(R, monkeypatch, "confirm ran for a proven absence")
    assert R._project_output_matches(NAME) == []


def test_retention_read_failure_degrades_without_crashing(monkeypatch):
    """WIDE (absent input): the retention index unreadable → keeps are simply
    unknown; candidacy degrades to receipts+rows, never raises."""
    R = _keeps_fixture(
        monkeypatch,
        receipt={"files": [{"path": "other.txt", "bytes": 1, "mtime": 1}]},
        retained=RuntimeError("substrate offline"))
    assert R._output_candidacy(NAME) == []


def test_receipt_and_keep_agreeing_yield_one_candidate(monkeypatch):
    """No double-count when both sources vouch: exactly one candidate tuple."""
    R = _keeps_fixture(
        monkeypatch,
        receipt={"files": [{"path": NAME, "bytes": 5, "mtime": 1}]},
        retained=[{"label": "ana_1", "state": "done", "target": "krn_live",
                   "selection": _json.dumps({"include": [NAME]})}])
    cands = R._output_candidacy(NAME)
    assert len(cands) == 1 and cands[0][0] == "ana_1" and cands[0][2] is True


def test_two_keep_backed_runs_are_honest_ambiguity(monkeypatch):
    """Two runs with recorded keeps for the same bare name: the locate refuses
    to pick (None) and the surfacing half names both."""
    from content.bio.lifecycle import runs as R
    from core.compute import retention
    from core.graph import output_addr as oa
    ents = [{"id": "ana_1", "type": "analysis",
             "metadata": {"weft_targets": ["krn_a"]}},
            {"id": "ana_2", "type": "analysis",
             "metadata": {"weft_targets": ["krn_b"]}}]
    monkeypatch.setattr(R, "list_entities",
                        lambda type_filter=None, include_archived=False: ents)
    monkeypatch.setattr(retention, "inventories", lambda tgts: {"inventories": {
        t: {"files": [{"path": "other.txt", "bytes": 1, "mtime": 1}]}
        for t in tgts}})
    monkeypatch.setattr(retention, "retained", lambda *a, **k: [])
    oa.record([{"run_id": "ana_1", "rel": NAME, "site": "siteA",
                "target": "krn_a", "source": "keep"},
               {"run_id": "ana_2", "rel": NAME, "site": "siteB",
                "target": "krn_b", "source": "keep"}])
    monkeypatch.setattr(R, "locate_run_output",
                        lambda rid, name, **kw: None)   # local pass finds nothing
    assert R._locate_project_run_output(NAME) is None
    assert sorted(s for _r, s in R.project_run_output_matches(NAME)) == \
        ["siteA", "siteB"]
# ── the UI half: index-known files render when the sidecars are gone ─────────

def test_the_durable_view_lists_an_index_known_store(monkeypatch):
    """Measured live: exec sidecars swept -> artifacts_for_run empty -> the
    run's Files panel rendered ALL ZEROS while weft's receipt named a 185 MB
    store. Index rows are knowledge that must survive that sweep. State stays
    honest: nothing keeps it here, so it reads at-risk/temporary — never
    'cleared' (a dir rel must not take the per-file stat path), never a fake
    'retained'."""
    from core.graph.entities import create_entity
    from content.bio.lifecycle import runs as R
    from core.graph import output_addr as oa
    from core.compute import retention

    rid = create_entity(entity_type="analysis", title="run", metadata={
        "weft_targets": ["krn_x"], "run_state": "closed"})
    oa.record([{"run_id": rid, "rel": NAME, "site": "siteA", "target": "krn_x",
                "kind": "dir", "bytes": 185_000_000, "source": "keep"}])
    monkeypatch.setattr(R, "artifacts_for_run", lambda r: [], raising=False)
    import core.exec.artifacts as _arts
    monkeypatch.setattr(_arts, "artifacts_for_run", lambda r: [])
    monkeypatch.setattr(retention, "retained", lambda **kw: [])
    monkeypatch.setattr(retention, "inventory", lambda *a, **kw: {"entries": []})
    monkeypatch.setattr(retention, "file_stats",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("a store dir took the per-file stat path")))

    v = R.run_durable_view(rid)
    rows = {f["rel"]: f for f in v["files"]}
    assert NAME in rows, "the index-known store is missing from the panel again"
    assert rows[NAME]["kind"] == "store"
    assert rows[NAME]["state"] in ("at-risk", "in-sandbox"), rows[NAME]["state"]


def test_the_tree_graft_gate_opens_on_index_rows_alone(monkeypatch):
    """The graft gated on artifacts_for_run — 'no ledger rows → skip the view'
    — which also gated out index-known files, so the durable-view fix above
    changed nothing in the Files tab until the GATE learned about the index."""
    from content.bio.files import tree as T
    from core.graph import output_addr as oa
    import core.exec.artifacts as _arts

    oa.record([{"run_id": "ana_g", "rel": NAME, "site": "siteA",
                "target": "krn_x", "kind": "dir", "bytes": 10}])
    monkeypatch.setattr(_arts, "artifacts_for_run", lambda r: [])
    from content.bio.lifecycle import runs as R
    monkeypatch.setattr(R, "run_durable_view", lambda r: {"files": [
        {"rel": NAME, "kind": "store", "state": "at-risk", "bytes": 10}]})

    parent = {"path": "threads/t/runs/r/output", "children": []}
    n = T._graft_run_outputs(parent, {"id": "ana_g", "title": "run",
                                      "artifact_path": None, "metadata": {}})
    names = [c.get("name") for c in parent["children"]]
    assert any(NAME in str(x) for x in names) or n > 0, (
        f"gate stayed closed with only index rows: grafted={n}, children={names}")


# ── ambient-run self-heal: retention reconstructs targets from exec records ──

def test_an_AMBIENT_run_with_no_stamped_target_still_retains(monkeypatch):
    """Deterministic live failure (keep_triage, 3x): quick utility work runs
    on a weft kernel BEFORE its ambient run exists, so the dispatch-time
    record_weft_target stamp has no run to stamp — and a keep decision against
    the run was a silent no-op (weft retained_runs empty, the file sitting in
    the kernel sandbox the whole time). Retention must reconstruct the targets
    from the run's own exec records' compute blocks, retain against them, and
    stamp them for later readers."""
    from content.bio.lifecycle import runs as R
    from core.graph import output_addr as oa
    from core.graph import exec_records as er
    from core.graph.entities import create_entity, get_entity
    from core.compute import retention

    rid = create_entity(entity_type="analysis", title="ambient run", metadata={
        "run": {"sites": ["local"]},          # NO weft_targets — the bug shape
    })
    monkeypatch.setattr(er, "list_by_run",
                        lambda r, limit=None: [{"exec_id": "exec_amb1"}])
    monkeypatch.setattr(er, "get", lambda x: {
        "compute": {"substrate": "weft", "kernel_id": "krn_ambient",
                    "site": "local"}} if x == "exec_amb1" else None)

    import core.exec.artifacts as _arts
    monkeypatch.setattr(_arts, "artifacts_for_run", lambda r: [])
    monkeypatch.setattr(R, "_jobdir_store_dirs", lambda r: {"summary.txt"})
    monkeypatch.setattr(R, "_declared_output_names", lambda md: set())
    monkeypatch.setattr(R, "_disk_truth_includes",
                        lambda r, md, inc, produced: (set(), set(), []))
    monkeypatch.setattr(R, "_retained_so_far", lambda r: (set(), set()))
    monkeypatch.setattr(R, "run_durable_view", lambda r: {"summary": {}})
    retained = []
    monkeypatch.setattr(retention, "retain",
                        lambda t, include=None, label=None, **kw:
                        retained.append((t, tuple(include or []))))
    monkeypatch.setattr(R, "_live_inventory", lambda t, **kw: {
        "site": "local", "live": True,
        "entries": [{"path": "summary.txt", "bytes": 20, "mtime": 1}]})

    out = R.set_keep_decision(rid, keep=["summary.txt"])
    assert "error" not in out, out
    assert retained and retained[0][0] == "krn_ambient", (
        f"retention never reached the kernel target: {retained}")
    assert "summary.txt" in retained[0][1]
    # the reconstruction is STAMPED so every later reader has the handle
    md = (get_entity(rid) or {}).get("metadata") or {}
    assert md.get("weft_targets") == ["krn_ambient"]
    # and the address index rows ride the same heal
    rows = [r for r in oa.by_name("summary.txt") if r["run_id"] == rid]
    assert rows and rows[0]["target"] == "krn_ambient"


def test_a_run_with_stamped_targets_never_consults_exec_records(monkeypatch):
    """CEILING: the reconstruction is the fallback, not a second source of
    truth — a stamped run keeps its recorded targets (armed: the record door
    raises)."""
    from content.bio.lifecycle import runs as R
    from core.graph import exec_records as er
    monkeypatch.setattr(er, "list_by_run",
                        lambda r, limit=None: (_ for _ in ()).throw(
                            AssertionError("exec records consulted despite stamps")))
    assert R._targets_from_exec_records is not None   # helper exists
    # drive _retain_run_outputs directly with stamped metadata
    import core.exec.artifacts as _arts
    monkeypatch.setattr(_arts, "artifacts_for_run", lambda r: [])
    monkeypatch.setattr(R, "_jobdir_store_dirs", lambda r: set())
    monkeypatch.setattr(R, "_declared_output_names", lambda md: set())
    monkeypatch.setattr(R, "_disk_truth_includes",
                        lambda r, md, inc, produced: (set(), set(), []))
    monkeypatch.setattr(R, "_retained_so_far", lambda r: (set(), set()))
    out = R._retain_run_outputs("ana_x", {"weft_targets": ["krn_stamped"]})
    assert isinstance(out, dict)


# ── files-only steps get a Run, an attach, and a weft target ─────────────────

def test_a_FILES_ONLY_step_attaches_its_exec_and_target(monkeypatch):
    """The gate upstream of the ambient failure: registry's post-tool hook
    required plots-or-tables, so a step that produced only FILES (write
    summary.txt) never attached its exec to the ambient Run and never
    recorded its weft target — exec run_id NULL, weft_targets absent, and a
    later keep a silent no-op. Plots and tables are just files the harvester
    classified; the bookkeeping must not depend on the classification."""
    from content.bio.lifecycle import registry as REG
    from core.graph import exec_records as er

    ensured = {"n": 0}
    monkeypatch.setattr(REG, "_ensure_analysis",
                        lambda focused, ctx, tid: (ensured.update(n=ensured["n"] + 1)
                                                   or "ana_files"))
    attached = []
    monkeypatch.setattr(er, "attach_to_run",
                        lambda eid, rid: attached.append((eid, rid)) or True)
    monkeypatch.setattr(er, "get", lambda eid: {"weft_target": "krn_files",
                                                "inputs": []})
    stamped = []
    from content.bio.lifecycle import runs as R
    monkeypatch.setattr(R, "record_weft_target",
                        lambda rid, t: stamped.append((rid, t)))
    monkeypatch.setattr(REG, "add_edge", lambda *a, **k: None, raising=False)

    REG.register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": "open('summary.txt','w')"},
        result_obj={"exec_id": "exec_f1", "plots": [], "tables": [],
                    "files": [{"name": "summary.txt"}]},
        thread_id="thr_t", focused_entity_id=None, analysis_ctx={})
    assert ensured["n"] == 1, "a files-only step no longer ensures the Run"
    assert ("exec_f1", "ana_files") in attached, "exec not attached to the Run"
    assert ("ana_files", "krn_files") in stamped, "weft target not recorded"


def test_a_no_output_step_still_creates_NO_run(monkeypatch):
    """CEILING: pure-compute steps (print a number) must not litter the graph
    with an ambient Run per utility call — the gate widened to files, not to
    everything."""
    from content.bio.lifecycle import registry as REG
    monkeypatch.setattr(REG, "_ensure_analysis",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("Run ensured for a no-output step")))
    REG.register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": "print(1)"},
        result_obj={"exec_id": "exec_f2", "plots": [], "tables": [], "files": []},
        thread_id="thr_t", focused_entity_id=None, analysis_ctx={})


# ── the row's TARGET guides the per-rel doors (same-rel collision, resume) ───
#
# A resumed Run spans two sandboxes; each numbers its files independently, so
# two jobdirs can hold the SAME rel with different bytes. The doors walked
# targets in a fixed order — first hit wins — while the listing advertised the
# addr row's bytes (recorded from the kernel that kept it): listing said
# 266 B, the door served the FIRST kernel's 0-byte same-rel copy (live
# 2026-08). The row's `target` is the tie-breaker; without a row (or without
# a usable target) the fixed-order walk is the unchanged, honest ceiling.

CREL = "data.bin"


def _collision_world(monkeypatch, tmp_path, row_target=..., *, record_row=True):
    """Two local jobdirs colliding on CREL: first target's copy EMPTY, the
    second's real. Optionally an addr row naming `row_target`."""
    import content.bio.lifecycle.runs as R
    ws = tmp_path / "ws"
    ja = ws / "site-local" / "jA"
    jb = ws / "site-local" / "jB"
    ja.mkdir(parents=True); jb.mkdir(parents=True)
    (ja / CREL).write_bytes(b"")
    (jb / CREL).write_bytes(b"real-bytes")
    monkeypatch.setattr(R, "get_entity", lambda rid: {
        "id": rid, "metadata": {"weft_targets": ["krn_a", "krn_b"]}})
    import core.compute.adapter as _ad
    monkeypatch.setattr(_ad, "weft_workspace", lambda: ws)

    class _Comp:
        def sync_call(self, verb, *a, **k):
            if verb == "list_kernels":
                return {"kernels": [
                    {"kernel_id": "krn_a", "site": "local", "jobdir": "jA"},
                    {"kernel_id": "krn_b", "site": "local", "jobdir": "jB"}]}
            return {}

    import core.compute as _cc
    monkeypatch.setattr(_cc, "get_compute", lambda: _Comp())
    from core.compute import retention
    monkeypatch.setattr(retention, "retained", lambda **k: [])
    from core.graph import exec_records
    monkeypatch.setattr(exec_records, "list_by_run", lambda rid: [])
    if record_row:
        from core.graph import output_addr as oa
        oa.record([{"run_id": "ana_c", "rel": CREL,
                    "target": (None if row_target is ... else row_target),
                    "site": "local", "kind": "file", "bytes": 10,
                    "state": "live", "source": "keep"}])
    return ja, jb


def test_the_rows_target_breaks_a_same_rel_collision(monkeypatch, tmp_path):
    _ja, jb = _collision_world(monkeypatch, tmp_path, "krn_b")
    from content.bio.lifecycle.runs import locate_run_output
    loc = locate_run_output("ana_c", CREL, match="exact", remote=False)
    assert loc, "collision file did not resolve at all"
    assert Path(loc["local_path"]).read_bytes() == b"real-bytes", \
        "door served a colliding sandbox's copy instead of the recorded one"
    assert loc["size"] == len(b"real-bytes")


def test_no_row_keeps_the_fixed_order_ceiling(monkeypatch, tmp_path):
    # WIDE (absent): without a row the walk is unchanged — first target answers.
    _ja, _jb = _collision_world(monkeypatch, tmp_path, record_row=False)
    from content.bio.lifecycle.runs import locate_run_output
    loc = locate_run_output("ana_c", CREL, match="exact", remote=False)
    assert loc and loc["size"] == 0


def test_a_target_less_row_keeps_the_fixed_order_ceiling(monkeypatch, tmp_path):
    # WIDE (absent field): backfill rows may carry no target.
    _ja, _jb = _collision_world(monkeypatch, tmp_path, None)
    from content.bio.lifecycle.runs import locate_run_output
    loc = locate_run_output("ana_c", CREL, match="exact", remote=False)
    assert loc and loc["size"] == 0


def test_an_unknown_target_falls_back_never_raises(monkeypatch, tmp_path):
    # WIDE (stale): the recording kernel was pruned from the target list.
    _ja, _jb = _collision_world(monkeypatch, tmp_path, "krn_gone")
    from content.bio.lifecycle.runs import locate_run_output
    loc = locate_run_output("ana_c", CREL, match="exact", remote=False)
    assert loc and loc["size"] == 0


def test_preview_read_prefers_the_rows_target(monkeypatch, tmp_path):
    import base64
    import content.bio.lifecycle.runs as R
    monkeypatch.setattr(R, "get_entity", lambda rid: {
        "id": rid, "metadata": {"weft_targets": ["krn_a", "krn_b"]}})
    from core.compute import retention
    served = {"krn_a": b"", "krn_b": b"real-bytes"}
    calls: list = []

    def _fr(t, rel, max_bytes=0):
        calls.append(t)
        return {"bytes_b64": base64.b64encode(served[t]).decode(),
                "bytes_total": len(served[t])}

    monkeypatch.setattr(retention, "file_read", _fr)
    from core.graph import output_addr as oa
    oa.record([{"run_id": "ana_c", "rel": CREL, "target": "krn_b",
                "site": "local", "kind": "file", "bytes": 10,
                "state": "live", "source": "keep"}])
    data, _tr, _tot = R.read_run_file("ana_c", CREL)
    assert data == b"real-bytes"
    assert calls[0] == "krn_b", "preview did not consult the recorded target first"


# ── the panel's fold outranks the addr join (machinery never advertised) ─────

def _panel_world(monkeypatch, artifact_rows):
    import content.bio.lifecycle.runs as R
    monkeypatch.setattr(R, "get_entity", lambda rid: {"id": rid, "metadata": {}})
    from core.exec import artifacts as _arts
    monkeypatch.setattr(_arts, "artifacts_for_run", lambda rid: list(artifact_rows))
    from core.compute import retention
    monkeypatch.setattr(retention, "retained", lambda **k: [])
    return R


def test_the_join_does_not_readmit_folded_machinery(monkeypatch):
    """Legacy machinery rows EXIST in the index (the harvest once recorded
    them); the panel must decline to advertise them — while a real rel keeps
    entering through the same join (its whole purpose)."""
    from core.graph import output_addr as oa
    oa.record([
        {"run_id": "ana_p", "rel": "blocks/0001.err", "site": "local",
         "target": "krn_b", "kind": "file", "bytes": 266,
         "state": "live", "source": "keep"},
        {"run_id": "ana_p", "rel": "result.csv", "site": "local",
         "target": "krn_b", "kind": "file", "bytes": 12,
         "state": "live", "source": "keep"},
    ])
    # ARMED: the join has something to re-admit — both rows really recorded.
    assert {r["rel"] for r in oa.by_run("ana_p")} \
        == {"blocks/0001.err", "result.csv"}
    R = _panel_world(monkeypatch, [])
    view = R.run_durable_view("ana_p")
    rels = [f["rel"] for f in view["files"]]
    assert "result.csv" in rels, "the join stopped admitting real files"
    assert "blocks/0001.err" not in rels, \
        "machinery re-admitted via the addr join (the empty-serve incident)"
    assert view["summary"].get("runtime_files") == 1   # declared, never silent


def test_machinery_counted_once_when_both_sources_carry_it(monkeypatch):
    # WIDE: sidecar row AND index row for the same machinery rel → folded once.
    from core.graph import output_addr as oa
    oa.record([{"run_id": "ana_q", "rel": "blocks/0001.err", "site": "local",
                "target": "krn_b", "kind": "file", "bytes": 266,
                "state": "live", "source": "keep"}])
    R = _panel_world(monkeypatch,
                     [{"original_name": "blocks/0001.err", "bytes": 266}])
    view = R.run_durable_view("ana_q")
    assert [f["rel"] for f in view["files"]] == []
    assert view["summary"].get("runtime_files") == 1
