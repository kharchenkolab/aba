"""open_viewer tool impl (content/bio/tools/viewers.py) — resolves an external
viewer for a file/entity and returns a /viewer-launch link Guide surfaces in
chat. The file is resolved against the project's files tree, so a bare basename
('processed.h5ad') works and a missing file returns a clear ok:false (not a dead
link). See misc/pagoda3_integration.md (Tier 2)."""
from urllib.parse import parse_qs, urlparse

import pytest

from content.bio.tools import open_viewer_impl


# A synthetic files tree: bare-basename resolution should find these.
_TREE = {
    "kind": "root", "name": "", "path": "", "children": [
        {"kind": "folder", "name": "threads", "path": "threads", "children": [
            {"kind": "file", "name": "processed.h5ad",
             "path": "threads/02_analyze/runs/01_run/output/processed.h5ad",
             "artifact_path": None, "mtime": 200},
        ]},
        {"kind": "folder", "name": "work", "path": "work", "children": [
            {"kind": "file", "name": "table.csv", "path": "work/table.csv", "mtime": 100},
            {"kind": "file", "name": "sample.lstar.zarr", "path": "work/sample.lstar.zarr", "mtime": 150},
        ]},
    ],
}


@pytest.fixture(autouse=True)
def _fake_tree(monkeypatch):
    monkeypatch.setattr("content.bio.files.tree.build_files_tree", lambda **kw: _TREE)


def _q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_bare_basename_resolves_to_canonical_path():
    # The reported bug: agent passes 'processed.h5ad'; it must resolve to the
    # real tree path so the launch link works.
    r = open_viewer_impl({"file_path": "processed.h5ad"})
    assert r["ok"] is True, r
    assert r["viewer_id"] == "pagoda3-anndata"
    assert r["resolved_path"] == "threads/02_analyze/runs/01_run/output/processed.h5ad"
    q = _q(r["viewer_url"])
    assert q["viewer"] == "pagoda3-anndata"
    assert q["path"] == "threads/02_analyze/runs/01_run/output/processed.h5ad"


def test_native_lstar_store_resolves():
    r = open_viewer_impl({"file_path": "sample.lstar.zarr"})
    assert r["ok"] is True and r["viewer_id"] == "pagoda3-lstar"


def test_missing_file_gives_clear_error_not_a_link():
    r = open_viewer_impl({"file_path": "does_not_exist.h5ad"})
    assert r["ok"] is False
    assert "no file matching" in r["error"].lower()
    assert "viewer_url" not in r


def test_wrong_type_lists_candidates_or_explains():
    # A real file, but not a single-cell store → no external viewer applies.
    r = open_viewer_impl({"file_path": "table.csv"})
    assert r["ok"] is False
    assert "no external viewer" in r["error"].lower()


def test_requires_a_target():
    r = open_viewer_impl({})
    assert r["ok"] is False
    assert "entity_id or file_path" in r["error"]


def test_unknown_viewer_id_is_rejected():
    r = open_viewer_impl({"file_path": "processed.h5ad", "viewer_id": "nope"})
    assert r["ok"] is False


def test_open_viewer_tool_is_registered_on_the_server():
    # The agent-facing viewer tool is get_viewer_url (external-viewer launch link);
    # the legacy 'open_viewer' name was retired.
    import asyncio
    from content.bio.mcp_servers.aba_core.server import make_server
    mcp = make_server()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "get_viewer_url" in names
