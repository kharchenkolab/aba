"""A viewer link carries the run key its minter already paid to learn.

Live, 2026-08-08: `get_viewer_url` resolved an absolute kernel path through the
project-wide search, LEARNED which run owned it (`_rid` in hand), then minted
`?path=<abs>` — discarding the key. The launch route re-searched by path with a
weaker searcher and 404'd on a file that existed. Two doors, one handle, and
the handle only opened the door that minted it.

The contract now:

  * MINT — a path-branch link whose resolve identified the owning run carries
    `&run=<rid>` beside `path=`;
  * FORWARD — the launch page passes `run_id` through to the launch POST;
  * RESOLVE — given (run_id, path) the route asks the owning run DIRECTLY
    (one bounded `locate_run_output`, no project-wide scan), falls through to
    the path tiers on a stale key, and hands back the same node shapes the
    path tiers produce (real path when local, logical-name marker when remote).

ARMED where it matters: the no-scan claim is measured by a sentinel over the
project-wide search that RAISES if consulted — the fall-through test asserts
the same sentinel WAS consulted, so the pair cannot both pass vacuously.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

pytestmark = pytest.mark.bio

_RT = tempfile.mkdtemp(prefix="aba_link_runkey_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

ABS = "/remote/.weft/kernels/krn_x/processed.data.zarr"
NAME = "processed.data.zarr"


# ── MINT ─────────────────────────────────────────────────────────────────────

def _mint(monkeypatch, located):
    """Drive the real mint (`open_viewer_impl`) with every tier before the
    run-output resolve stubbed to miss, so the path branch is the one under
    test. The function imports its collaborators per call, so the seams are
    their home modules."""
    import types
    from content.bio.tools import viewers as tv
    from content.bio.lifecycle import runs as R
    from content.bio import data_location as dl
    from content.bio.files import tree as T
    from core.viewers import registry as reg
    from core import projects as P

    monkeypatch.setattr(R, "resolve_project_run_output_located",
                        lambda name, **kw: located)
    monkeypatch.setattr(dl, "entity_for_path", lambda p: None)
    monkeypatch.setattr(T, "build_files_tree",
                        lambda include_archived=False: {"children": []})
    monkeypatch.setattr(T, "find_file_node", lambda tree, p: None)
    monkeypatch.setattr(T, "list_file_matches", lambda tree, p: [])
    monkeypatch.setattr(tv, "_remote_note", lambda *a, **k: None)
    monkeypatch.setattr(reg, "viewers_for", lambda node: [
        types.SimpleNamespace(id="pagoda3-lstar", mode="external",
                              open_external="pagoda3_launcher", label="Explore")])
    monkeypatch.setattr(P, "current_project_id", lambda: "prj_t")
    return tv.open_viewer_impl({"file_path": ABS})


def test_THE_MINT_carries_the_run_key(monkeypatch):
    """The resolve just learned the owner; the link must not forget it."""
    out = _mint(monkeypatch, ("ana_7", NAME, "siteA", 4096, True))
    assert out.get("ok", True), out
    q = parse_qs(urlparse(out["viewer_url"]).query)
    assert q.get("run") == ["ana_7"], out["viewer_url"]
    assert q.get("path"), "path stays for display/fallback"


def test_a_LOCAL_resolve_also_carries_the_key(monkeypatch):
    out = _mint(monkeypatch, ("ana_3", "/tmp/x/" + NAME, "local", 7, False))
    q = parse_qs(urlparse(out["viewer_url"]).query)
    assert q.get("run") == ["ana_3"]


def test_an_entity_link_does_NOT_grow_a_run_param(monkeypatch):
    """CEILING: the entity branch keeps its shape — entity IS the key there."""
    import types
    from content.bio.tools import viewers as tv
    from core.graph import entities as E
    from core.viewers import registry as reg
    from core import projects as P
    monkeypatch.setattr(E, "get_entity", lambda eid: {
        "id": eid, "type": "dataset", "artifact_path": "/x/" + NAME,
        "metadata": {}})
    monkeypatch.setattr(tv, "_entity_location_note", lambda e: None)
    monkeypatch.setattr(reg, "viewers_for", lambda node: [
        types.SimpleNamespace(id="pagoda3-lstar", mode="external",
                              open_external="pagoda3_launcher", label="Explore")])
    monkeypatch.setattr(P, "current_project_id", lambda: "prj_t")
    out = tv.open_viewer_impl({"entity_id": "ds_1"})
    q = parse_qs(urlparse(out["viewer_url"]).query)
    assert "run" not in q and q.get("entity") == ["ds_1"]


# ── FORWARD ──────────────────────────────────────────────────────────────────

def test_the_launch_page_forwards_the_run_param():
    """The page's JS builds the launch POST from the URL params; a link
    carrying run= that the page drops is the same discarded handle one hop
    later. (String-level guard: the JS is not executable here, but the param
    plumbing is one greppable line.)"""
    from core.viewers.launch_page import render
    html = render(Path(_RT))          # dist path only affects the css href
    assert 'run_id: q.get("run")' in html, "launch page dropped the run param"


# ── RESOLVE ──────────────────────────────────────────────────────────────────

def _route(monkeypatch, *, loc, scan_sentinel):
    from content.bio.web.routes import viewers as rv
    from content.bio.lifecycle import runs as R
    monkeypatch.setattr(R, "locate_run_output",
                        lambda rid, name, **kw: loc(rid, name))
    monkeypatch.setattr(R, "resolve_project_run_output", scan_sentinel)
    # the path tiers' earlier stops: no registered entity, empty tree
    from content.bio import data_location as dl
    monkeypatch.setattr(dl, "entity_for_path", lambda p: None)
    from content.bio.files import tree as T
    monkeypatch.setattr(T, "build_files_tree", lambda include_archived=False: {"children": []})
    monkeypatch.setattr(T, "find_file_node", lambda tree, p: None)
    monkeypatch.setattr(T, "list_file_matches", lambda tree, p: [])
    return rv


def test_a_run_keyed_launch_never_scans_the_project(monkeypatch):
    """THE point of carrying the key. The sentinel RAISES on any project-wide
    search — a run-keyed resolve that still scans fails here, not in a latency
    graph three weeks later."""
    def scan(*a, **k):
        raise AssertionError("project-wide search consulted despite a run key")
    rv = _route(monkeypatch,
                loc=lambda rid, name: ({"locality": "remote", "site": "siteA",
                                        "size": 9, "local_path": None}
                                       if rid == "ana_7" else None),
                scan_sentinel=scan)
    node = rv._resolve_files_node(None, ABS, run_id="ana_7")
    assert node["run_id"] == "ana_7"
    assert node["artifact_path"] == NAME, "remote → the logical-name marker"
    assert node["name"] == NAME


def test_a_run_keyed_LOCAL_hit_returns_the_real_path(monkeypatch):
    rv = _route(monkeypatch,
                loc=lambda rid, name: {"locality": "local", "site": "local",
                                       "size": 5, "local_path": "/tmp/real/" + NAME},
                scan_sentinel=lambda *a, **k: None)
    node = rv._resolve_files_node(None, ABS, run_id="ana_7")
    assert node["artifact_path"] == "/tmp/real/" + NAME


def test_a_STALE_run_key_falls_through_to_the_path_tiers(monkeypatch):
    """WIDE: a link can outlive its run (forgotten, foreign project). The key
    must degrade to exactly the path-only behaviour — and the sentinel must
    show the fall-through actually ran, so this test and the no-scan test
    cannot both pass by the tier being dead."""
    from fastapi import HTTPException
    consulted = {"n": 0}

    def scan(path, **kw):
        consulted["n"] += 1
        return None
    rv = _route(monkeypatch, loc=lambda rid, name: None, scan_sentinel=scan)
    with pytest.raises(HTTPException) as ei:
        rv._resolve_files_node(None, ABS, run_id="ana_gone")
    assert ei.value.status_code == 404
    assert consulted["n"] == 1, "stale key must fall through, not dead-end"


def test_a_raising_locate_is_a_stale_key_not_a_500(monkeypatch):
    from fastapi import HTTPException
    def boom(rid, name):
        raise RuntimeError("weft down")
    rv = _route(monkeypatch, loc=boom, scan_sentinel=lambda *a, **k: None)
    with pytest.raises(HTTPException) as ei:
        rv._resolve_files_node(None, ABS, run_id="ana_7")
    assert ei.value.status_code == 404


def test_run_key_without_a_path_is_ignored(monkeypatch):
    """Degenerate shape: nothing to name inside the run — underspecified, the
    existing 400, not a crash in the new tier."""
    from fastapi import HTTPException
    rv = _route(monkeypatch, loc=lambda rid, name: None,
                scan_sentinel=lambda *a, **k: None)
    with pytest.raises(HTTPException) as ei:
        rv._resolve_files_node(None, None, run_id="ana_7")
    assert ei.value.status_code == 400


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
