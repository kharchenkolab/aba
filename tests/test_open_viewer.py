"""open_viewer tool impl (content/bio/tools/viewers.py) — resolves an external
viewer for a file/entity and returns a /viewer-launch link Guide surfaces in
chat. See misc/pagoda3_integration.md (Tier 2)."""
from urllib.parse import parse_qs, urlparse

from content.bio.tools import open_viewer_impl


def _q(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_h5ad_file_resolves_to_pagoda3():
    r = open_viewer_impl({"file_path": "work/processed.h5ad"})
    assert r["ok"] is True
    assert r["viewer_id"] == "pagoda3-anndata"
    q = _q(r["viewer_url"])
    assert r["viewer_url"].startswith("/viewer-launch?")
    assert q["viewer"] == "pagoda3-anndata"
    assert q["path"] == "work/processed.h5ad"
    assert q["label"] == "Explore in pagoda3"
    assert "project" in q


def test_native_lstar_store_resolves_to_pagoda3_lstar():
    r = open_viewer_impl({"file_path": "out/sample.lstar.zarr"})
    assert r["ok"] is True
    assert r["viewer_id"] == "pagoda3-lstar"


def test_non_viewable_file_is_rejected_with_reason():
    r = open_viewer_impl({"file_path": "results/table.csv"})
    assert r["ok"] is False
    assert "no external viewer" in r["error"].lower()


def test_requires_a_target():
    r = open_viewer_impl({})
    assert r["ok"] is False
    assert "entity_id or file_path" in r["error"]


def test_unknown_viewer_id_is_rejected():
    r = open_viewer_impl({"file_path": "a.h5ad", "viewer_id": "nope"})
    assert r["ok"] is False


def test_open_viewer_tool_is_registered_on_the_server():
    import asyncio
    from content.bio.mcp_servers.aba_core.server import make_server
    mcp = make_server()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert "open_viewer" in names
