"""Viewer-link resolution: a raw path resolves entity-backed WITHOUT a remote probe.

The class this guards (viewer-launch surfacing): passing a raw REMOTE absolute
path to the viewer-link tool ran a ~10s remote inventory probe, missed, and
returned an opaque "no file matching" that never said the file was remote —
even though the path was byte-identical to a registered by-reference dataset's
recorded home. The fixes:

  F1  reverse-lookup path→entity BEFORE any probe (entity_for_path): a
      byte-identical registered home resolves instantly and entity-backed.
  F2  the location pre-flight note attaches on BOTH branches (entity + remote
      run output), same cost wording, honest lever per source.
  F3  an absolute-path miss names the remote levers (pass the entity id /
      register), not just "no file matching".
  F5  an entity-id miss lists near-match entities ({id,type}), not a dead end.

ARMED: the F1 integration test monkeypatches the run-output probe
(_locate_project_run_output) with a sentinel that RECORDS invocation — a
reverse-lookup hit that still fell through to the probe fails the test, so the
guard measures the actual instant path, not just the result shape.

Drives the impl directly (tmp DB via env vars, generic fixtures) like
tests/test_delete_blockers.py — no server start.

Run:  python tests/test_viewer_link_resolution.py   (or pytest)
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_RT = tempfile.mkdtemp(prefix="aba_viewer_link_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db            # noqa: E402
from core.graph.entities import create_entity, update_entity  # noqa: E402
from content.bio.tools import open_viewer_impl     # noqa: E402

init_db()  # startup hook doesn't fire on import — build the schema ourselves


# ── generic fixtures ─────────────────────────────────────────────────────────

def _mk_dataset(title: str, *, artifact_path=None, home=None, ref_path=None,
                total_bytes=None, archived=False) -> str:
    md: dict = {}
    if home is not None:
        md["home"] = home
        md["by_reference"] = True
    if ref_path is not None:
        md["ref_path"] = ref_path
    if total_bytes is not None:
        md["descriptor"] = {"total_bytes": total_bytes}
    eid = create_entity(entity_type="dataset", title=title,
                        artifact_path=artifact_path, metadata=md)
    eid = eid if isinstance(eid, str) else eid["id"]
    if archived:
        update_entity(eid, status="archived")
    return eid


class _FakeViewer:
    """A stand-in external viewer so a GENERIC fixture extension resolves — the
    real registry keys off single-cell extensions, which generic fixture names
    (data.parquet) deliberately lack."""
    def __init__(self, vid="fake-viewer", label="Fake Viewer"):
        self.id, self.label = vid, label
        self.mode, self.open_external = "external", True


def _q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ── F1 unit: entity_for_path reverse lookup (the core, fully generic) ─────────

def test_reverse_lookup_matches_home_path():
    from content.bio.data_location import entity_for_path
    p = "/remote/siteA/proj/data.parquet"
    eid = _mk_dataset("remote by-ref", artifact_path=p,
                      home={"site": "siteA", "path": p}, ref_path=p)
    hit = entity_for_path(p)
    assert hit is not None and hit["id"] == eid


def test_reverse_lookup_matches_artifact_path_only():
    from content.bio.data_location import entity_for_path
    p = "/remote/siteA/only_artifact/data.parquet"
    eid = _mk_dataset("artifact only", artifact_path=p)
    assert entity_for_path(p)["id"] == eid


def test_reverse_lookup_matches_ref_path_only():
    from content.bio.data_location import entity_for_path
    p = "/remote/siteA/only_ref/data.parquet"
    eid = _mk_dataset("ref only", ref_path=p)
    assert entity_for_path(p)["id"] == eid


def test_reverse_lookup_trailing_slash_normalized():
    # WIDE: a store dir recorded without a trailing slash must match an input
    # that carries one (and vice versa).
    from content.bio.data_location import entity_for_path
    recorded = "/remote/siteA/store_dir"
    eid = _mk_dataset("store dir", artifact_path=recorded,
                      home={"site": "siteA", "path": recorded})
    assert entity_for_path(recorded + "/")["id"] == eid
    assert entity_for_path(recorded)["id"] == eid


def test_reverse_lookup_bare_basename_no_false_hit():
    # WIDE (relative / bare basename): must NOT match a recorded ABSOLUTE path —
    # exact match only, no basename collision.
    from content.bio.data_location import entity_for_path
    _mk_dataset("abs home", artifact_path="/remote/siteA/uniquebase.parquet",
                home={"site": "siteA", "path": "/remote/siteA/uniquebase.parquet"})
    assert entity_for_path("uniquebase.parquet") is None


def test_reverse_lookup_absolute_local_path_that_exists(tmp_path):
    # WIDE (absolute LOCAL path that exists): entity_for_path is string-only, so
    # a local file that IS a registered dataset still resolves entity-backed.
    from content.bio.data_location import entity_for_path
    f = tmp_path / "table.csv"
    f.write_text("a,b\n1,2\n")
    eid = _mk_dataset("local abs", artifact_path=str(f))
    assert entity_for_path(str(f))["id"] == eid


def test_reverse_lookup_archived_excluded():
    # WIDE (archived): a path matching ONLY an archived dataset reads as no hit —
    # a raw path must not silently resurrect a hidden entity.
    from content.bio.data_location import entity_for_path
    p = "/remote/siteA/archived/data.parquet"
    _mk_dataset("archived ds", artifact_path=p,
                home={"site": "siteA", "path": p}, archived=True)
    assert entity_for_path(p) is None


def test_reverse_lookup_entity_without_recorded_path():
    # WIDE (no recorded path): a dataset with no artifact_path/home/ref_path
    # never matches; an empty input never matches.
    from content.bio.data_location import entity_for_path
    _mk_dataset("no path dataset")
    assert entity_for_path("") is None
    assert entity_for_path("/remote/siteA/nowhere.parquet") is None


def test_reverse_lookup_newest_duplicate_wins():
    # Two live registrations of the SAME path: the NEWEST wins — same policy
    # as _locate_project_run_output (list_entities orders pinned-then-oldest,
    # so without the reversal the stale duplicate would shadow the current one).
    from content.bio.data_location import entity_for_path
    p = "/remote/siteA/dup/data.parquet"
    _mk_dataset("dup old", artifact_path=p, home={"site": "siteA", "path": p})
    new = _mk_dataset("dup new", artifact_path=p, home={"site": "siteA", "path": p})
    assert entity_for_path(p)["id"] == new


def test_reverse_lookup_relative_recorded_path_never_matches():
    # WIDE (relative hijack): run-registered datasets can record caller paths
    # VERBATIM (possibly relative). A relative input equal to such a string
    # must NOT reverse-resolve — it would steal the resolution from a real
    # files-tree node. Absolute inputs only.
    from content.bio.data_location import entity_for_path
    rel = "out/table.csv"
    _mk_dataset("verbatim relative", artifact_path=rel, ref_path=rel)
    assert entity_for_path(rel) is None


# ── F1 integration: entity-backed + probe NOT called (ARMED) ─────────────────

def _patch_no_probe(monkeypatch) -> dict:
    """Sentinel over the run-output probe: any invocation is recorded, so a
    reverse-lookup that still reached the probe is caught."""
    seen = {"probed": False}
    def _sentinel(*a, **k):
        seen["probed"] = True
        return None
    monkeypatch.setattr(
        "content.bio.lifecycle.runs._locate_project_run_output", _sentinel)
    monkeypatch.setattr("core.viewers.registry.viewers_for",
                        lambda node: [_FakeViewer()])
    monkeypatch.setattr("content.bio.files.tree.build_files_tree",
                        lambda **kw: {"kind": "root", "name": "", "path": "", "children": []})
    return seen


def test_path_reverse_lookup_is_entity_backed_and_skips_probe(monkeypatch):
    p = "/remote/siteA/armed/data.parquet"
    eid = _mk_dataset("armed remote", artifact_path=p,
                      home={"site": "siteA", "path": p}, ref_path=p,
                      total_bytes=500_000_000)
    seen = _patch_no_probe(monkeypatch)
    r = open_viewer_impl({"file_path": p})
    assert r["ok"] is True, r
    assert _q(r["viewer_url"]).get("entity") == eid, r["viewer_url"]  # entity-backed
    assert seen["probed"] is False, "reverse-lookup hit must NOT touch the remote probe"
    # a path input still echoes the resolved path (parity with the tree branch)
    assert r["resolved_path"] == p, r
    # F2: the entity pre-flight note rides along for a remote source.
    assert r.get("note") and "siteA" in r["note"]


def test_home_only_entity_matches_viewer_by_recorded_basename(monkeypatch):
    # A by-reference dataset with NO artifact_path (URL-import / home-only
    # shape) still dispatches viewers by EXTENSION: the node name derives from
    # the recorded reference/home path's basename, not the extensionless title
    # (which would shadow the miss guidance with "no viewer applies").
    p = "/remote/siteA/homeonly/data.parquet"
    eid = _mk_dataset("home only no extension in title",
                      home={"site": "siteA", "path": p})
    seen = _patch_no_probe(monkeypatch)
    monkeypatch.setattr(                      # extension-gated: title never matches
        "core.viewers.registry.viewers_for",
        lambda node: [_FakeViewer()] if (node.get("name") or "").endswith(".parquet") else [])
    r = open_viewer_impl({"file_path": p})
    assert r["ok"] is True, r
    assert _q(r["viewer_url"]).get("entity") == eid
    assert seen["probed"] is False


# ── F4 server half: the launch route reverse-looks-up a raw path ─────────────

def test_route_reverse_lookup_resolves_entity_backed(monkeypatch):
    # ARMED like the tool-side test: the probe sentinel records invocation, so
    # a route resolution that fell through to the probe fails here.
    import content.bio.web.routes.viewers as vr
    p = "/remote/siteA/route/data.parquet"
    eid = _mk_dataset("route home only", home={"site": "siteA", "path": p})
    seen = {"probed": False}
    def _sentinel(*a, **k):
        seen["probed"] = True
        return None
    monkeypatch.setattr(
        "content.bio.lifecycle.runs._locate_project_run_output", _sentinel)
    monkeypatch.setattr("content.bio.files.tree.build_files_tree",
                        lambda **kw: {"kind": "root", "name": "", "path": "", "children": []})
    node = vr._resolve_files_node(None, p)
    assert node["entity_id"] == eid
    assert node["name"] == "data.parquet"     # recorded-basename dispatch (route side)
    assert seen["probed"] is False, "route reverse-lookup hit must NOT touch the probe"


def test_launch_route_returns_entity_id_on_reverse_hit(monkeypatch):
    # The launch response carries the reverse-looked-up entity_id so the launch
    # page can offer the working mirror lever on a remote-gate failure.
    import content.bio.web.routes.viewers as vr
    p = "/remote/siteA/launchhit/data.parquet"
    eid = _mk_dataset("launch reverse hit", artifact_path=p,
                      home={"site": "siteA", "path": p})
    monkeypatch.setattr("core.viewers.registry.viewers_for",
                        lambda node: [_FakeViewer()])
    monkeypatch.setattr("core.viewers.prepare.start",
                        lambda runner, label=None: "job_test")
    out = vr.viewers_launch(vr.ViewerLaunchIn(path=p), _pid="test")
    assert out["job_id"] == "job_test"
    assert out.get("entity_id") == eid, out


# ── F2 path branch: a REMOTE run output gets the same pre-flight note ────────

def _patch_remote_run_output(monkeypatch, *, site, size):
    monkeypatch.setattr("content.bio.files.tree.build_files_tree",
                        lambda **kw: {"kind": "root", "name": "", "path": "", "children": []})
    monkeypatch.setattr("core.viewers.registry.viewers_for",
                        lambda node: [_FakeViewer()])
    monkeypatch.setattr(
        "content.bio.lifecycle.runs._locate_project_run_output",
        lambda name, **k: ("run_1", site, size, True))


def test_remote_run_output_gets_location_note(monkeypatch):
    _patch_remote_run_output(monkeypatch, site="siteB", size=400_000_000)
    r = open_viewer_impl({"file_path": "out.parquet"})
    assert r["ok"] is True, r
    assert "entity" not in _q(r["viewer_url"])           # path-backed, not entity
    assert r.get("note") and "siteB" in r["note"]
    assert "register it as a dataset" in r["note"]       # honest lever for a run output


def test_remote_run_output_over_gate_note(monkeypatch):
    _patch_remote_run_output(monkeypatch, site="siteB", size=3 * 1024**3)
    r = open_viewer_impl({"file_path": "big.parquet"})
    assert r["ok"] is True, r
    assert "OVER the transfer gate" in (r.get("note") or "")


# ── F3 informative miss ──────────────────────────────────────────────────────

def _patch_total_miss(monkeypatch, *, matches=None):
    monkeypatch.setattr("content.bio.files.tree.build_files_tree",
                        lambda **kw: {"kind": "root", "name": "", "path": "", "children": []})
    monkeypatch.setattr("content.bio.files.tree.list_file_matches",
                        lambda tree, p: (matches or []))
    monkeypatch.setattr(
        "content.bio.lifecycle.runs._locate_project_run_output",
        lambda name, **k: None)


def test_absolute_path_miss_names_remote_levers(monkeypatch):
    _patch_total_miss(monkeypatch)
    r = open_viewer_impl({"file_path": "/remote/siteA/gone/data.parquet"})
    assert r["ok"] is False and "viewer_url" not in r
    low = r["error"].lower()
    assert "no file matching" in low
    assert "absolute path" in low and "remote site" in low
    assert "list_entities" in low


def test_relative_miss_keeps_generic_guidance(monkeypatch):
    _patch_total_miss(monkeypatch)
    r = open_viewer_impl({"file_path": "nope.parquet"})
    assert r["ok"] is False
    low = r["error"].lower()
    assert "absolute path" not in low                    # not an abs path
    assert "check the files tab" in low or "register it as a dataset" in low


def test_miss_keeps_candidate_listing(monkeypatch):
    # The near-miss basename listing is preserved (probe near-misses stay useful).
    _patch_total_miss(monkeypatch, matches=["a/one.parquet", "b/two.parquet"])
    r = open_viewer_impl({"file_path": "/remote/siteA/x.parquet"})
    assert r["ok"] is False
    assert "Matching files" in r["error"] and "one.parquet" in r["error"]


# ── F5 near-match hint on an entity-id miss ─────────────────────────────────

def test_entity_miss_lists_near_matches():
    eid = _mk_dataset("near-match target", artifact_path="/remote/siteA/nm.parquet")
    assert len(eid) >= 8, "entity id shorter than the prefix probe expects"
    bad = eid[:8] + "zzzzzz"           # shares an 8-char prefix → a near match
    r = open_viewer_impl({"entity_id": bad})
    assert r["ok"] is False and "viewer_url" not in r
    assert "No entity" in r["error"] and "Did you mean" in r["error"]
    assert eid in r["error"]           # the real id is offered as a candidate


def test_entity_miss_unrelated_id_generic_message():
    r = open_viewer_impl({"entity_id": "qqq_totally_unrelated_00000"})
    assert r["ok"] is False
    assert "No entity" in r["error"] and "list_entities" in r["error"]
    assert "Did you mean" not in r["error"]


def test_requires_a_target():
    r = open_viewer_impl({})
    assert r["ok"] is False and "entity_id or file_path" in r["error"]


_TESTS = [
    test_reverse_lookup_matches_home_path,
    test_reverse_lookup_matches_artifact_path_only,
    test_reverse_lookup_matches_ref_path_only,
    test_reverse_lookup_trailing_slash_normalized,
    test_reverse_lookup_bare_basename_no_false_hit,
    test_reverse_lookup_absolute_local_path_that_exists,
    test_reverse_lookup_archived_excluded,
    test_reverse_lookup_entity_without_recorded_path,
    test_reverse_lookup_newest_duplicate_wins,
    test_reverse_lookup_relative_recorded_path_never_matches,
    test_path_reverse_lookup_is_entity_backed_and_skips_probe,
    test_home_only_entity_matches_viewer_by_recorded_basename,
    test_route_reverse_lookup_resolves_entity_backed,
    test_launch_route_returns_entity_id_on_reverse_hit,
    test_remote_run_output_gets_location_note,
    test_remote_run_output_over_gate_note,
    test_absolute_path_miss_names_remote_levers,
    test_relative_miss_keeps_generic_guidance,
    test_miss_keeps_candidate_listing,
    test_entity_miss_lists_near_matches,
    test_entity_miss_unrelated_id_generic_message,
    test_requires_a_target,
]


class _MP:
    """Minimal monkeypatch for the standalone (__main__) runner — setattr with
    string 'module.attr' targets, auto-undone after each test."""
    def __init__(self):
        self._undo = []
    def setattr(self, target, value):
        import importlib
        mod_name, attr = target.rsplit(".", 1)
        mod = importlib.import_module(mod_name)
        self._undo.append((mod, attr, getattr(mod, attr)))
        setattr(mod, attr, value)
    def undo(self):
        for mod, attr, old in reversed(self._undo):
            setattr(mod, attr, old)
        self._undo.clear()


def _standalone() -> int:
    import inspect
    import traceback
    rc = 0
    for t in _TESTS:
        mp = _MP()
        try:
            params = inspect.signature(t).parameters
            kw = {}
            if "monkeypatch" in params:
                kw["monkeypatch"] = mp
            if "tmp_path" in params:
                kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="aba_vlr_tp_"))
            t(**kw)
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
