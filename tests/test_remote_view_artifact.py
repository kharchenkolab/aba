"""Viewing an artifact that lives on ANOTHER machine.

`view_artifact` resolved its `path` against the local filesystem only, with no
site-aware branch at all (unlike `get_viewer_url`, which has one). A figure a
remote step had just written was therefore un-viewable, and the live workaround
was submitting a WHOLE EXTRA REMOTE JOB whose only purpose was to copy a 249 KB
png into the harvest path (2026-07-26).

Now a local miss consults the Run graph and pulls the bytes over the PREVIEW
channel (weft `run_file_read`, 8 MB cap), cached under the project's artifacts
area.

ARMED: the fake records whether the remote read was attempted, so a regression
that silently stops trying fails the call-log assertion rather than passing
vacuously. WIDE: covers the local hit (must NOT touch the remote path), a
local-only output, an oversize file (refused with the dataset lever, never a
half-rendered image), unreadable/swept bytes, and a non-run path.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

_TMP = tempfile.mkdtemp(prefix="aba_rview_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = str(Path(_TMP) / "v.db")
os.environ["ABA_PROJECTS_DIR"] = _TMP + "/projects"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from content.bio.mcp_servers.aba_core.tools import entity_ops as EO  # noqa: E402


@pytest.fixture()
def runs(monkeypatch):
    """Stub the Run-graph resolver + preview read; record every call."""
    state = {"located": None, "read": (b"", False, 0), "reads": [], "locates": []}
    import content.bio.lifecycle.runs as R

    def _located(name, **kw):
        state["locates"].append(name)
        return state["located"]

    def _read(run_id, rel, max_bytes=None):
        state["reads"].append((run_id, rel))
        return state["read"]
    monkeypatch.setattr(R, "resolve_project_run_output_located", _located)
    monkeypatch.setattr(R, "read_run_file", _read)
    return state


def test_remote_run_output_is_fetched_and_cached(runs):
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    runs["located"] = ("ana_1", "plot.png", "siteA", len(png), True)
    runs["read"] = (png, False, len(png))
    got, note = EO._fetch_remote_view_file("/scratch/somewhere/plot.png")
    assert runs["locates"] == ["/scratch/somewhere/plot.png"], "ARMED: resolver consulted"
    assert runs["reads"] == [("ana_1", "plot.png")], "ARMED: preview read attempted"
    assert got is not None and got.exists()
    assert got.read_bytes() == png
    assert "siteA" in note
    # cached under the project's artifacts area, not a temp dir that vanishes
    assert "_remote_view" in str(got)
    # a second view is free — and must produce the same bytes
    got2, _ = EO._fetch_remote_view_file("/scratch/somewhere/plot.png")
    assert got2.read_bytes() == png


def test_local_output_is_left_to_the_disk_resolver(runs):
    """CEILING: is_remote=False means the disk resolver already had its chance;
    this path must not invent a fetch."""
    runs["located"] = ("ana_1", "/local/plot.png", "local", 10, False)
    got, note = EO._fetch_remote_view_file("/local/plot.png")
    assert got is None and note == ""
    assert runs["reads"] == [], "a local output must not be read over the wire"


def test_unknown_path_is_not_a_remote_fetch(runs):
    runs["located"] = None
    got, note = EO._fetch_remote_view_file("/nope/x.png")
    assert got is None and note == ""
    assert runs["reads"] == []


def test_oversize_file_is_refused_with_the_dataset_lever(runs):
    """WIDE — the degenerate size: a file past the preview cap must be REFUSED
    with the right lever, never rendered from a truncated read (half a PNG is
    not a figure)."""
    runs["located"] = ("ana_1", "big.png", "siteA", 99_000_000, True)
    runs["read"] = (b"partial", True, 99_000_000)
    got, note = EO._fetch_remote_view_file("/scratch/big.png")
    assert got is None
    assert "too large" in note and "register" in note.lower()
    assert "99000000" in note or "99_000_000" in note.replace(",", "_")


def test_unreadable_remote_bytes_explain_themselves(runs):
    """A swept file: say where it was and what to do, not just 'not found'."""
    runs["located"] = ("ana_1", "gone.png", "siteB", 10, True)
    runs["read"] = (None, False, 0)
    got, note = EO._fetch_remote_view_file("/scratch/gone.png")
    assert got is None
    assert "siteB" in note and ("swept" in note or "could not be read" in note)


def test_resolver_failure_never_breaks_the_tool(runs, monkeypatch):
    """WIDE — instrumentation-style ceiling: a broken resolver degrades to a
    normal miss, it does not raise into the agent's face."""
    import content.bio.lifecycle.runs as R

    def boom(*a, **k):
        raise RuntimeError("graph unavailable")
    monkeypatch.setattr(R, "resolve_project_run_output_located", boom)
    got, note = EO._fetch_remote_view_file("/scratch/x.png")
    assert got is None and "remote lookup failed" in note


def test_empty_path_is_a_noop(runs):
    assert EO._fetch_remote_view_file("") == (None, "")
    assert runs["locates"] == []


# ── tier 2: an ARBITRARY absolute path on a site ────────────────────────────
#
# Tier 1 only resolves RECORDED run outputs. A step that writes to a directory
# of its own choosing produces files no run knows about — live thr_a1f7f687 wrote
# five figures to a hand-picked dir and view_artifact refused all five. The path
# carries no machine, so the site comes from `site=` or is inferred from the
# thread's own recent remote steps.

@pytest.fixture()
def dataplane(monkeypatch):
    """Fake data-plane: register (identity, no copy) + ranged read."""
    import base64
    from core.compute import retention
    state = {"registered": [], "bytes": b"", "size": None, "reads": 0}

    class _Comp:
        def sync_call(self, verb, *a, **kw):
            assert verb == "data_register", verb
            state["registered"].append({"path": a[0], **kw})
            n = state["size"] if state["size"] is not None else len(state["bytes"])
            return {"ref": "ref-x", "bytes": n}
    monkeypatch.setattr("core.compute.adapter.get_compute", lambda: _Comp())

    def _read(ref, rel=None, *, offset=0, length=None, site=None, rels=None):
        state["reads"] += 1
        chunk = state["bytes"][offset:offset + 4]
        return {"nbytes": len(chunk), "size": len(state["bytes"]),
                "eof": offset + len(chunk) >= len(state["bytes"]),
                "capped": offset + len(chunk) < len(state["bytes"]),
                "bytes_b64": base64.b64encode(chunk).decode()}
    monkeypatch.setattr(retention, "data_read_range", _read)
    return state


def test_explicit_site_reaches_an_arbitrary_remote_path(runs, dataplane):
    """THE gap: not a run output, but we know the machine."""
    runs["located"] = None
    png = b"\x89PNG\r\n\x1a\n" + b"z" * 40
    dataplane["bytes"] = png
    got, note = EO._fetch_remote_view_file(
        "/home/u/hand/picked/umap_ref_celltype_l1.png", site="mendel")
    assert got is not None and got.read_bytes() == png
    assert dataplane["registered"][0]["site"] == "mendel"
    assert dataplane["registered"][0]["ingest"] is False, \
        "a VIEW must not copy the dataset — identity only"
    assert "mendel" in note
    assert dataplane["reads"] > 1, "ARMED: multi-chunk read actually looped"


def test_site_is_inferred_from_the_threads_recent_remote_step(runs, dataplane, monkeypatch):
    """An absolute path names no machine, but the agent is looking at a file the
    step it just ran produced — so the thread's own records answer it."""
    runs["located"] = None
    dataplane["bytes"] = b"data"
    from core.graph import exec_records
    monkeypatch.setattr(exec_records, "list_by_thread",
                        lambda t, **k: [{"exec_id": "e1"}, {"exec_id": "e2"}])
    monkeypatch.setattr(exec_records, "get", lambda eid: (
        {"payload": {"compute": {"site": "mendel"}}} if eid == "e2"
        else {"payload": {"compute": {"site": "local"}}}))
    got, note = EO._fetch_remote_view_file("/home/u/x.png", thread_id="t1")
    assert got is not None and "mendel" in note
    assert dataplane["registered"][0]["site"] == "mendel"


def test_no_site_and_no_history_declines_quietly(runs, dataplane, monkeypatch):
    """CEILING: with nothing to infer from, do NOT guess a site — decline and
    let the caller's not-found message stand."""
    runs["located"] = None
    from core.graph import exec_records
    monkeypatch.setattr(exec_records, "list_by_thread", lambda t, **k: [])
    got, note = EO._fetch_remote_view_file("/home/u/x.png", thread_id="t1")
    assert got is None
    assert dataplane["registered"] == [], "must not touch the data plane blind"


def test_relative_paths_never_enter_the_remote_tier(runs, dataplane):
    """CEILING: only an ABSOLUTE path can be a remote path. A bare filename is
    the local resolver's business."""
    runs["located"] = None
    got, _ = EO._fetch_remote_view_file("plot.png", site="mendel")
    assert got is None and dataplane["registered"] == []


def test_oversize_remote_file_is_refused_before_transfer(runs, dataplane):
    """WIDE — the budget: refuse on the REGISTERED size, before pulling bytes."""
    runs["located"] = None
    dataplane["bytes"] = b"x" * 32
    dataplane["size"] = 900 * 1024 * 1024
    got, note = EO._fetch_remote_view_file("/home/u/huge.bin", site="mendel")
    assert got is None
    assert "too large" in note and "mirror" in note
    assert dataplane["reads"] == 0, "must not stream a file it will refuse"


def test_run_output_tier_still_wins_and_needs_no_site(runs, dataplane):
    """CEILING: a recorded run output must NOT go through the data plane — the
    run already knows where it ran, so tier 1 stays cheaper and inference-free."""
    png = b"\x89PNG\r\n\x1a\n"
    runs["located"] = ("ana_1", "plot.png", "siteA", len(png), True)
    runs["read"] = (png, False, len(png))
    got, note = EO._fetch_remote_view_file("/whatever/plot.png", site="mendel")
    assert got is not None and "siteA" in note
    assert dataplane["registered"] == [], "tier 1 must not use the data plane"
