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


def _run(title="a run"):
    """A keep's `label` IS the run's entity id, and the ledger now uses the
    project graph to decide which keeps are this project's — so a test label
    has to be a real analysis, exactly as production ones are."""
    out = create_entity(entity_type="analysis", title=title)
    return out if isinstance(out, str) else out["id"]


def test_outage_is_degraded_never_quietly_safe(monkeypatch):
    """OUTAGE HONESTY: with the substrate CONFIGURED but retained() failing,
    the ledger must say degraded (kept rows are missing) — the quiet "all
    safe" render told users their kept results were safe during an outage,
    and the disconnect card showed a machine as empty. A weft-less fallback
    deployment (substrate never configured) stays quiet — nothing hidden."""
    import core.compute.adapter as admod

    def _boom(**kw):
        raise RuntimeError("substrate unreachable")
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    monkeypatch.setattr(retmod, "retained", _boom)
    monkeypatch.setattr(admod, "status", lambda: {"ok": True})   # configured
    led = lg.data_ledger()
    assert led["degraded"] is True and "unreachable" in led["degraded_note"]
    h = lg.site_holdings("far")
    assert h["unknown"] is True and "cannot be assessed" in h["note"]
    # fallback deployment: substrate never configured → quiet, not degraded
    monkeypatch.setattr(admod, "status", lambda: {"ok": False})
    assert lg.data_ledger()["degraded"] is False
    assert "unknown" not in lg.site_holdings("far")


def test_durable_map_sees_runtime_registered_sites(monkeypatch):
    """A machine CONNECTED AT RUNTIME (weft register_site, durable:True) is
    invisible to the deployment yaml — its keeps/homes rendered at_risk
    despite the durable declaration (browser-study finding). The map unions
    weft's own site store; yaml wins where both name a site."""
    import core.compute.adapter as admod
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteY", "kind": "ssh", "config": {"durable": False}}])

    class _Comp:
        def sync_call(self, name, *a, **kw):
            if name == "sites_list":
                return [{"name": "runtime-box"}, {"name": "siteY"}]
            if name == "sites_describe":
                assert a[0] == "runtime-box"     # yaml-named siteY not re-asked
                return {"storage": {"durable": True, "source": "declared"}}
            raise AssertionError(f"unexpected call {name}")
    monkeypatch.setattr(admod, "get_compute", lambda: _Comp())
    d = lg._durable_map()
    assert d["runtime-box"] is True
    assert d["siteY"] is False               # yaml wins for its own sites
    # a dataset homed on the runtime box is SAFE, not at_risk
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    ds = _ds("runtime-homed", home={"site": "runtime-box", "path": "/d/x"})
    try:
        st = {i["entity_id"]: i["state"] for i in lg.data_ledger()["items"]}
        assert st[ds] == "safe"
    finally:
        from core.graph.entities import archive_entity
        archive_entity(ds)      # the ledger scans globally — don't leak into
                                # the quiet/totals tests below


def test_local_only_project_is_quiet(monkeypatch):
    """The §-quiet contract at the DATA layer: all-local & safe → nothing to
    render (multi_site False, zero non-safe). The UI snapshot test rides this."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    runL = _run("local keep")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": runL, "site": "local", "in_place": 1, "bytes": 7, "state": "done"}])
    led = lg.data_ledger()
    local_items = [i for i in led["items"] if i["entity_id"] == runL]
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


def test_remote_sites_decomposes_composite_keep_sites(monkeypatch):
    """A keep spanning local+remote carries the COMPOSITE display string
    "local/siteB" in its item `site` — remote_sites is an enumeration of real
    site NAMES, so the composite must decompose (it leaked into the UI as a
    phantom third site: "(some on local/siteB, siteB)"). Both sides: the real
    site appears once, the composite and "local" never do."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteB", "kind": "ssh", "config": {"durable": True}}])
    run1 = _run("spanning keep")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": run1, "site": "local", "in_place": 0, "bytes": 1, "state": "done"},
        {"label": run1, "site": "siteB", "in_place": 1, "bytes": 2, "state": "done"},
    ])
    led = lg.data_ledger()
    # graph state persists across tests in this file — select OUR item by
    # kind rather than assuming a clean ledger
    (keep,) = [i for i in led["items"] if i["kind"] == "run_keeps"]
    assert keep["site"] == "local/siteB"          # display string keeps both
    assert "siteB" in led["remote_sites"]         # the real site, enumerated
    assert "local/siteB" not in led["remote_sites"]   # composite never leaks
    assert "local" not in led["remote_sites"]
    assert led["multi_site"] is True


def test_keeps_state_follows_durable_declaration(monkeypatch):
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteB", "kind": "ssh", "config": {"durable": True}},
        {"name": "siteC", "kind": "ssh", "config": {}},   # declaration revoked
    ])
    r1, r2, r3, dead = (_run("r1"), _run("r2"), _run("r3"), _run("dead"))
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": r1, "site": "siteB", "in_place": 1, "bytes": 10, "state": "done"},
        {"label": r2, "site": "siteC", "in_place": 1, "bytes": 20, "state": "done"},
        {"label": r3, "site": "local", "in_place": 0, "bytes": 5, "state": "done"},
        {"label": dead, "site": "siteC", "in_place": 1, "bytes": 9, "state": "failed"},
    ])
    keeps, ok = lg._keep_items(lg._durable_map())
    assert ok is True
    items = {i["entity_id"]: i for i in keeps}
    assert items[r1]["state"] == "safe"
    assert items[r2]["state"] == "at_risk"           # in place, no durable promise
    assert items[r3]["state"] == "safe"              # shipped home
    assert dead not in items                         # failed rows aren't keeps



def test_site_holdings_counts_keeps_and_homes(monkeypatch):
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteB", "kind": "ssh", "config": {"durable": True}}])
    runX = _run("held on B")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": runX, "site": "siteB", "in_place": 1, "bytes": 40, "state": "done"}]
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
    for t in (test_outage_is_degraded_never_quietly_safe,
              test_local_only_project_is_quiet,
              test_ledger_states_and_quiescence,
              test_keeps_state_follows_durable_declaration,
              test_site_holdings_counts_keeps_and_homes,
              test_keep_risk_is_per_row_never_folded_across_a_label,
              test_a_shipped_home_keep_is_never_at_risk_alone,
              test_keeps_are_scoped_to_this_project,
              test_scoped_keeps_carry_their_title_and_link,
              test_at_risk_keep_names_the_targets_a_repair_would_move,
              test_yaml_silence_about_durability_is_not_a_declaration,
              test_local_durability_survives_a_describe_hiccup,
              test_site_holdings_separates_in_place_from_shipped_home):
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



# ── dangling local bytes (the "safe" that wasn't) ───────────────────────────

def _ds_at(title, path, **md):
    """A dataset registered with a concrete artifact_path (no site = local)."""
    out = create_entity(entity_type="dataset", title=title,
                        artifact_path=str(path), metadata=md)
    return out if isinstance(out, str) else out["id"]


def test_local_dataset_with_missing_file_is_not_safe(monkeypatch, tmp_path):
    """A local dataset was called "safe: bytes live in the workspace data
    folder" purely because it HAD an artifact_path — the path was never
    checked. So a dataset whose file had been deleted out of band (live
    2026-07-26: a raw os.remove in a code block) kept reporting safe while the
    entity stayed active pointing at nothing.

    ARMED: the present-file case in the same test must stay `safe`, so a
    blanket "always changed" implementation fails this too."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    here = tmp_path / "present.parquet"
    here.write_bytes(b"data")
    gone = tmp_path / "gone.parquet"          # never created

    ok_id = _ds_at("present", here)
    bad_id = _ds_at("absent", gone)
    items = {i["entity_id"]: i for i in lg._dataset_items(lg._durable_map())}

    assert items[ok_id]["state"] == "safe", "a file that IS there stays safe"
    assert items[bad_id]["state"] == "changed", \
        "a registered dataset whose file is gone must not read as safe"
    assert "no longer on disk" in items[bad_id]["why"]


def test_remote_dataset_paths_are_never_stat_checked(monkeypatch, tmp_path):
    """CEILING: a by-reference dataset's home path lives on ANOTHER machine, so
    stat'ing it locally would report every remote dataset as missing. A site
    means hands off — its verdict comes from the durable declaration."""
    monkeypatch.setattr(scfg, "list_declared_sites",
                        lambda: [{"name": "siteA", "config": {"durable": True}}])
    rid = _ds_at("remote-home", "/scratch/nonexistent/on/this/box.parquet",
                 home={"site": "siteA", "path": "/scratch/nonexistent/on/this/box.parquet"})
    items = {i["entity_id"]: i for i in lg._dataset_items(lg._durable_map())}
    assert items[rid]["state"] == "safe"
    assert "durable" in items[rid]["why"]


def test_recorded_drift_still_wins_over_the_disk_check(monkeypatch, tmp_path):
    """Precedence: an explicit source_missing/source_changed stamp is a
    RECORDED verdict and keeps its own wording — the disk check is only the
    fallback for the previously-unchecked local case."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    gone = tmp_path / "nope.parquet"
    did = _ds_at("stamped", gone, source_missing=True)
    items = {i["entity_id"]: i for i in lg._dataset_items(lg._durable_map())}
    assert items[did]["state"] == "changed"
    assert "gone or unreachable" in items[did]["why"]


def test_probe_error_does_not_manufacture_a_missing_verdict(monkeypatch, tmp_path):
    """WIDE — the degenerate environment: a permissions/OS error must not turn
    into "your data is gone". The bug being fixed is a FALSE safe; a false
    alarm is its own dishonesty."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    import os as _os
    monkeypatch.setattr(_os.path, "exists",
                        lambda p: (_ for _ in ()).throw(OSError("EPERM")))
    eid = _ds_at("unreadable", tmp_path / "x.parquet")
    items = {i["entity_id"]: i for i in lg._dataset_items(lg._durable_map())}
    assert items[eid]["state"] == "safe", "a probe error must not read as missing"


def test_relative_artifact_paths_are_left_alone(monkeypatch):
    """A non-absolute artifact_path is registry-relative; this check has no
    business judging it."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    eid = _ds_at("relative", "data/table.parquet")
    items = {i["entity_id"]: i for i in lg._dataset_items(lg._durable_map())}
    assert items[eid]["state"] == "safe"


# ── the verdict is per ROW, and the list is this project's ──────────────────

def test_keep_risk_is_per_row_never_folded_across_a_label(monkeypatch):
    """THE defect (live 2026-08-27): `in_place` is a PER-ROW fact and was
    folded to the label group with `any()`, then asked of every site in the
    group. One run routinely keeps in several places — a kernel on the
    workspace site, a job on a scratch-rooted machine — so the kernel's safe
    in-place-ness was lent to the job's keep, which had been COPIED off that
    machine precisely because it promises nothing. The ledger flagged the
    copy-to-safety as the thing at risk.

    ARMED both ways: the same label with a genuinely in-place row on the
    non-durable site MUST still read at_risk, so an implementation that
    simply stopped flagging fails here too."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteA", "kind": "slurm", "config": {"root": "/scratch/x"}}])
    good, bad = _run("mixed keep"), _run("really at risk")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        # kept in place on the workspace site (durable by construction)
        {"label": good, "target": "krn_1", "site": "local",
         "in_place": 1, "bytes": 10, "state": "done"},
        # SHIPPED HOME off the non-durable machine — its bytes are gone from it
        {"label": good, "target": "jb_1", "site": "siteA",
         "in_place": 0, "moved": 1, "bytes": 5, "state": "done"},
        # the genuine case: same shape, but the remote row never moved
        {"label": bad, "target": "krn_2", "site": "local",
         "in_place": 1, "bytes": 10, "state": "done"},
        {"label": bad, "target": "jb_2", "site": "siteA",
         "in_place": 1, "bytes": 5, "state": "done"},
    ])
    items = {i["entity_id"]: i for i in lg._keep_items(lg._durable_map())[0]}
    assert items[good]["state"] == "safe", \
        "a keep that was copied to safety must not be the one flagged"
    assert items[good]["site"] == "local/siteA"   # display still spans both
    assert items[bad]["state"] == "at_risk"       # ARMED: the real case fires
    assert "siteA" in items[bad]["why"]


def test_a_shipped_home_keep_is_never_at_risk_alone(monkeypatch):
    """WIDE — the degenerate single-row shape: nothing but a moved-home row on
    a machine that promises nothing. Its bytes are in the workspace; there is
    no risk to report and nothing to repair."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteA", "kind": "slurm", "config": {"root": "/scratch/x"}}])
    r = _run("shipped home")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": r, "target": "jb_9", "site": "siteA",
         "in_place": 0, "moved": 1, "bytes": 5, "state": "done"}])
    (item,) = [i for i in lg._keep_items(lg._durable_map())[0]
               if i["entity_id"] == r]
    assert item["state"] == "safe" and "remedy" not in item
    assert item["kept_in_place"] == []


def test_keeps_are_scoped_to_this_project(monkeypatch):
    """Weft's retention index is one per WORKSPACE — `retained()` has no
    project filter and never did, so the project rollup listed every kept run
    the user had ever made anywhere (live: 33 items in a project holding one
    dataset, 32 of them other projects' runs). The label IS the run's entity
    id, so the active graph decides.

    ARMED: a foreign keep that IS at risk must still be COUNTED, not dropped
    — going quiet about it is the same dishonesty as the outage case."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteA", "kind": "slurm", "config": {"root": "/scratch/x"}}])
    mine = _run("this project's run")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": mine, "target": "krn_a", "site": "local",
         "in_place": 1, "bytes": 1, "state": "done"},
        # another project's run: its id resolves in ITS graph, not this one
        {"label": "ana_deadbeef", "target": "jb_b", "site": "siteA",
         "in_place": 1, "bytes": 2, "state": "done"},
    ])
    led = lg.data_ledger()
    ids = {i["entity_id"] for i in led["items"]}
    assert mine in ids
    assert "ana_deadbeef" not in ids, "another project's keep is not ours to list"
    # totals are shared with the dataset tests above (one graph per file), so
    # assert on the KEEPS: none of them may be at risk here
    assert [i for i in led["items"]
            if i["kind"] == "run_keeps" and i["state"] == "at_risk"] == []
    assert led["elsewhere"] == {"items": 1, "at_risk": 1}, \
        "a foreign keep at risk must be counted, never silently dropped"


def test_scoped_keeps_carry_their_title_and_link(monkeypatch):
    """A keep rendered as `ana_a89bd4a1 — at risk: …` named nothing the user
    could act on, and the strip hard-disabled its button. Attribution already
    reads the entity, so the title and the linkability come free."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    r = _run("a titled run")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": r, "target": "krn_t", "site": "local",
         "in_place": 1, "bytes": 1, "state": "done"}])
    (item,) = [i for i in lg.data_ledger()["items"] if i["entity_id"] == r]
    assert item["title"] == "a titled run" and item["linkable"] is True


def test_at_risk_keep_names_the_targets_a_repair_would_move(monkeypatch):
    """The ledger flagged a problem the system had no verb for. A remedy
    carries the ROWS that are actually at risk — not every row of the label,
    or the repair would move bytes that were never in danger."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteA", "kind": "slurm", "config": {"root": "/scratch/x"}}])
    r = _run("needs securing")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": r, "target": "krn_safe", "site": "local",
         "in_place": 1, "bytes": 1, "state": "done"},
        {"label": r, "target": "jb_risky", "site": "siteA",
         "in_place": 1, "bytes": 2, "state": "done"},
    ])
    (item,) = [i for i in lg._keep_items(lg._durable_map())[0]
               if i["entity_id"] == r]
    assert item["remedy"]["action"] == "ship_home"
    assert item["remedy"]["targets"] == ["jb_risky"], \
        "only the rows in danger — a repair must not touch the safe ones"


def test_yaml_silence_about_durability_is_not_a_declaration(monkeypatch):
    """The deployment yaml won merely by NAMING a site, so a deployment that
    declares a cluster and says nothing about its storage — the normal case,
    since durability is a separate assertion — voted "not durable" and
    shadowed weft's own registration."""
    import core.compute.adapter as admod
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteA", "kind": "slurm", "config": {"root": "/scratch/x"}}])

    class _Comp:
        def sync_call(self, name, *a, **kw):
            if name == "sites_list":
                return [{"name": "siteA"}]
            if name == "sites_describe":
                return {"storage": {"durable": True, "source": "declared"}}
            raise AssertionError(f"unexpected call {name}")
    monkeypatch.setattr(admod, "get_compute", lambda: _Comp())
    assert lg._durable_map()["siteA"] is True, \
        "silence in the yaml must not outvote a runtime declaration"


def test_local_durability_survives_a_describe_hiccup(monkeypatch):
    """WIDE — the degenerate substrate answer: the workspace site is durable
    BY CONSTRUCTION. One malformed describe must not downgrade it, or every
    kept result in the workspace reads at risk at once."""
    import core.compute.adapter as admod

    class _Comp:
        def sync_call(self, name, *a, **kw):
            if name == "sites_list":
                return [{"name": "local"}]
            return {}                     # no storage block at all
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [])
    monkeypatch.setattr(admod, "get_compute", lambda: _Comp())
    assert lg._durable_map()["local"] is True


def test_site_holdings_separates_in_place_from_shipped_home(monkeypatch):
    """The durable-off card claims N kept results "would become at risk" —
    true only of the ones whose bytes are STILL on the machine. Counting the
    rows that had already been shipped home overstated the consequence of a
    reversible settings change."""
    monkeypatch.setattr(scfg, "list_declared_sites", lambda: [
        {"name": "siteA", "kind": "ssh", "config": {"durable": True}}])
    here, gone = _run("still there"), _run("already home")
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"label": here, "target": "jb_h", "site": "siteA",
         "in_place": 1, "bytes": 40, "state": "done"},
        {"label": gone, "target": "jb_g", "site": "siteA",
         "in_place": 0, "moved": 1, "bytes": 60, "state": "done"},
    ] if kw.get("site") == "siteA" else [])
    h = lg.site_holdings("siteA")
    assert h["kept_runs"] == 2 and h["kept_bytes"] == 100
    assert h["kept_in_place"] == {"runs": 1, "bytes": 40}

if __name__ == "__main__":
    raise SystemExit(_standalone())
