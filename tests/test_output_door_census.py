"""Output door census — every lister/server of run outputs reads the LEDGER.

The regression this guards (live, 2026-07): under the weft-kernel substrate a
run's produced files live in the KERNEL WORKSPACE, not in the run's
artifact_path — but the Files tab built each run's output/ by WALKING
artifact_path on disk (which holds only hidden .exec/ records), so every bulk
output (large binaries, CSVs) silently vanished from the tab, from the run
subtree route, from download zips, and from project materialize — while the
Run card (which reads the ledger) showed them fine. One address model
migrated; four doors kept reading the old one.

The doors and their contracts:
  - files tree (build_files_tree): output/ comes from the produced ledger
    (run_durable_view) — states carried, sandbox-lifetime files marked
    ephemeral, cleared files not listed, cap DECLARED — plus a disk top-up
    for legacy jobdir runs, deduped by rel;
  - /api/files/content (and /raw): a ledger-sourced node serves through the
    canonical run resolver; a remote-only file 404s NAMING ITS SITE;
  - /api/files/download (zip): unserveable listed files are NAMED in
    SKIPPED-FILES.txt, never silently omitted;
  - materialize: the caller's resolver supplies bytes for ledger-sourced
    nodes; without bytes they count as missing WITH a warning.
  (The locate door — locate_project_files T2 manifest tier — is guarded in
  test_path_resolution.py; the agent viewer door falls back through
  resolve_project_run_output, covered by its own tests.)

Run: pytest tests/test_output_door_census.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="aba_doors_")
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "doors.db"))
os.environ.setdefault("ARTIFACTS_DIR", str(Path(_tmp) / "artifacts"))
os.environ.setdefault("ABA_WORK_DIR", str(Path(_tmp) / "work"))
os.environ.setdefault("DATA_DIR", str(Path(_tmp) / "data"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402,F401 — register builders/tools
from core.graph.entities import get_entity  # noqa: E402
import core.exec.artifacts as artmod  # noqa: E402
import core.graph.exec_records as execmod  # noqa: E402
import content.bio.lifecycle.runs as runsmod  # noqa: E402
import content.bio.files.tree as treemod  # noqa: E402

pytestmark = pytest.mark.bio


def _mk_run(monkeypatch) -> tuple[str, str]:
    from core.graph.entities import create_entity
    from content.bio.lifecycle.runs import open_run
    tid = create_entity(entity_type="thread", title="t",
                        metadata={"thread_id": None})
    rid = open_run(tid, "an analysis")
    # A kernel run has exec records → aggregated code is non-empty → the run
    # renders (its producing_code.py was among "the handful you DO see" live).
    # The bug was never a hidden run; it was an empty output/ under a VISIBLE
    # run. Stub the code aggregation so the run clears the visibility gate.
    monkeypatch.setattr(execmod, "aggregated_code_for_run",
                        lambda r: "print('produced outputs')")
    return tid, rid


def _walk(node, pred, acc):
    if pred(node):
        acc.append(node)
    for c in node.get("children") or []:
        _walk(c, pred, acc)
    return acc


def _canned_view(rid: str) -> dict:
    return {"files": [
        # bulk workspace file, nothing keeps it yet — the invisible class
        {"rel": "bulk-data.parquet", "bytes": 900_000_000, "kind": "file",
         "state": "at-risk", "badge": "", "url": None, "site": None,
         "large": True},
        # nested + retained → served via the run-file route
        {"rel": "figs/scatter.png", "bytes": 20_000, "kind": "figure",
         "state": "retained", "badge": "kept ✓",
         "url": f"/api/runs/{rid}/file?rel=figs/scatter.png", "site": "local",
         "large": False},
        # small surfaced copy in aba's store
        {"rel": "summary.png", "bytes": 10_000, "kind": "figure",
         "state": "in-store", "badge": "temporary · viewing copy",
         "url": "/artifacts/p/summary.png", "site": None, "large": False},
        # swept — must NOT appear in the tab (the Run card owns that story)
        {"rel": "gone.dat", "bytes": 5, "kind": "file", "state": "cleared",
         "badge": "discarded", "url": None, "site": None, "large": False},
    ], "summary": {"total": 4}}


def _files_under_output(tree) -> dict[str, dict]:
    out: list = []
    _walk(tree, lambda n: n.get("kind") == "file" and "/output/" in
          (n.get("path") or ""), out)
    return {n["rel"] if n.get("rel") else n["name"]: n for n in out}


# ── door 1: the files tree ───────────────────────────────────────────────────

def test_kernel_run_outputs_visible_from_ledger(monkeypatch):
    _tid, rid = _mk_run(monkeypatch)
    # the kernel-substrate shape: artifact_path holds ONLY dotfiles
    ap = get_entity(rid)["artifact_path"]
    (Path(ap) / ".exec").mkdir(parents=True, exist_ok=True)
    (Path(ap) / ".exec" / "rec.json").write_text("{}")
    monkeypatch.setattr(artmod, "artifacts_for_run",
                        lambda r: [{"original_name": "bulk-data.parquet"}])
    monkeypatch.setattr(runsmod, "run_durable_view",
                        lambda r: _canned_view(rid))

    by = _files_under_output(treemod.build_files_tree())
    # the exact class that went invisible: a bulk file living in the kernel
    # workspace, listed from the ledger with its danger stated
    assert "bulk-data.parquet" in by, "ledger-produced file missing from the Files tab"
    h5 = by["bulk-data.parquet"]
    assert h5["ephemeral"] is True and h5["state"] == "at-risk"
    assert h5["run_id"] == rid and h5["rel"] == "bulk-data.parquet"
    # nested rel → nested folders, served URL carried
    assert by["figs/scatter.png"]["path"].endswith("/output/figs/scatter.png")
    assert by["figs/scatter.png"]["artifact_path"].startswith(f"/api/runs/{rid}/file")
    assert "ephemeral" not in by["figs/scatter.png"]
    assert by["summary.png"]["artifact_path"] == "/artifacts/p/summary.png"
    # cleared files do not exist — listing them here would advertise dead paths
    assert "gone.dat" not in by


def test_legacy_disk_files_still_listed_and_deduped(monkeypatch):
    _tid, rid = _mk_run(monkeypatch)
    ap = Path(get_entity(rid)["artifact_path"])
    (ap / "figs").mkdir(parents=True, exist_ok=True)
    (ap / "figs" / "scatter.png").write_bytes(b"png")     # ALSO in the ledger
    (ap / "legacy.bin").write_bytes(b"bin-bytes")      # disk-only (old model)
    monkeypatch.setattr(artmod, "artifacts_for_run",
                        lambda r: [{"original_name": "x"}])
    monkeypatch.setattr(runsmod, "run_durable_view",
                        lambda r: _canned_view(rid))

    by = _files_under_output(treemod.build_files_tree())
    assert "legacy.bin" in by, "legacy jobdir file lost by the source swap"
    assert str(ap) in (by["legacy.bin"]["artifact_path"] or "")
    dupes = [n for n in by.values() if n["name"] == "scatter.png"]
    assert len(dupes) == 1, "ledger + disk double-listed the same rel"


def test_ledger_failure_degrades_to_disk_never_crashes(monkeypatch):
    _tid, rid = _mk_run(monkeypatch)
    ap = Path(get_entity(rid)["artifact_path"])
    ap.mkdir(parents=True, exist_ok=True)
    (ap / "seen.csv").write_text("a,b")
    monkeypatch.setattr(artmod, "artifacts_for_run",
                        lambda r: [{"original_name": "x"}])

    def _boom(r):
        raise RuntimeError("substrate unreachable")
    monkeypatch.setattr(runsmod, "run_durable_view", _boom)
    by = _files_under_output(treemod.build_files_tree())
    assert "seen.csv" in by                     # disk door still answers


def test_truncation_is_declared_not_silent(monkeypatch):
    _tid, rid = _mk_run(monkeypatch)
    run = get_entity(rid)
    many = {"files": [
        {"rel": f"f{i:03}.csv", "bytes": 1, "kind": "file",
         "state": "in-sandbox", "badge": "", "url": None, "site": None,
         "large": False} for i in range(8)], "summary": {"total": 8}}
    monkeypatch.setattr(artmod, "artifacts_for_run",
                        lambda r: [{"original_name": "x"}])
    monkeypatch.setattr(runsmod, "run_durable_view", lambda r: many)
    parent = {"kind": "folder", "name": "output", "path": "p/output",
              "children": []}
    n = treemod._graft_run_outputs(parent, run, cap=5)
    assert n == 5 and len(parent["children"]) == 5          # ceiling holds
    assert parent["truncated"] is True
    assert "5" in parent["note"] and "8" in parent["note"]  # the cut is NAMED


def test_disk_topup_skips_runner_scaffolding(monkeypatch):
    """The disk top-up lists PRODUCTS, not the job runner's process
    bookkeeping (pid/log/launcher scripts at the sandbox root, the blocks/
    transcript dir). Other side: a real product beside them still grafts."""
    _tid, rid = _mk_run(monkeypatch)
    ap = Path(get_entity(rid)["artifact_path"])
    (ap / "blocks").mkdir(parents=True, exist_ok=True)
    (ap / "blocks" / "0001.out").write_text("x")
    for n in ("pid", "log", "runner.sh", "activate.sh", "rusage"):
        (ap / n).write_text("scaffolding")
    (ap / "result.bin").write_bytes(b"product")
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda r: [])

    by = _files_under_output(treemod.build_files_tree())
    assert "result.bin" in by                      # the product is listed
    for n in ("pid", "log", "runner.sh", "activate.sh", "rusage", "0001.out"):
        assert n not in by, f"runner scaffolding leaked into output/: {n}"


# ── doors 2+3: serve routes (content, download zip) ─────────────────────────

def _routes_app(monkeypatch, tree: dict):
    from fastapi import FastAPI
    import content.bio.web.routes.files as fr
    monkeypatch.setattr(treemod, "build_files_tree",
                        lambda **kw: tree)
    app = FastAPI()
    app.include_router(fr.router)
    return app, fr


def _tab_tree(rid: str, *, site=None) -> dict:
    return {"kind": "root", "name": "", "path": "", "children": [
        {"kind": "file", "name": "bulk-data.parquet", "path": "runs/r1/output/bulk-data.parquet",
         "artifact_path": None, "size": 9, "state": "in-sandbox", "badge": "",
         "run_id": rid, "rel": "bulk-data.parquet", "ephemeral": True,
         **({"site": site} if site else {})},
    ]}


def test_files_content_serves_ledger_node_via_run_resolver(monkeypatch):
    from fastapi.testclient import TestClient
    src = Path(_tmp) / "kernel-ws" / "bulk-data.parquet"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_bytes(b"blob-bytes")
    monkeypatch.setattr(runsmod, "resolve_run_file",
                        lambda rid, rel: str(src) if rel == "bulk-data.parquet" else None)
    app, _fr = _routes_app(monkeypatch, _tab_tree("r1"))
    r = TestClient(app).get("/api/files/content",
                            params={"path": "runs/r1/output/bulk-data.parquet"})
    assert r.status_code == 200 and r.content == b"blob-bytes"


def test_files_content_names_the_site_when_bytes_are_remote(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(runsmod, "resolve_run_file", lambda rid, rel: None)
    app, _fr = _routes_app(monkeypatch, _tab_tree("r1", site="siteA"))
    r = TestClient(app).get("/api/files/content",
                            params={"path": "runs/r1/output/bulk-data.parquet"})
    assert r.status_code == 404
    assert "siteA" in r.json()["detail"], \
        "a remote file must 404 naming its site, not blame the disk"


def test_download_zip_includes_resolvable_and_names_the_rest(monkeypatch):
    import io
    import zipfile
    from fastapi.testclient import TestClient
    src = Path(_tmp) / "kernel-ws2" / "ok.csv"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("a,b\n1,2\n")
    tree = {"kind": "root", "name": "", "path": "", "children": [
        {"kind": "folder", "name": "output", "path": "output", "children": [
            {"kind": "file", "name": "ok.csv", "path": "output/ok.csv",
             "artifact_path": None, "size": 8, "run_id": "r1", "rel": "ok.csv"},
            {"kind": "file", "name": "far.parquet", "path": "output/far.parquet",
             "artifact_path": None, "size": 9, "run_id": "r1",
             "rel": "far.parquet", "site": "siteB"},
        ]}]}
    monkeypatch.setattr(runsmod, "resolve_run_file",
                        lambda rid, rel: str(src) if rel == "ok.csv" else None)
    app, _fr = _routes_app(monkeypatch, tree)
    r = TestClient(app).get("/api/files/download", params={"path": "output"})
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(zf.namelist())
    assert "ok.csv" in names
    assert "SKIPPED-FILES.txt" in names, \
        "an unserveable listed file must be NAMED, not silently dropped"
    manifest = zf.read("SKIPPED-FILES.txt").decode()
    assert "far.parquet" in manifest and "siteB" in manifest


# ── door 4: materialize ──────────────────────────────────────────────────────

def test_materialize_uses_the_callers_resolver(tmp_path):
    from core.files.materialize import materialize_tree
    src = tmp_path / "ws" / "bulk-data.parquet"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"bytes")
    tree = _tab_tree("r1")
    out = tmp_path / "files"
    # without a resolver the ledger node is missing — WITH a warning
    s1 = materialize_tree(tree, out, clean=True)
    assert s1["missing"] == 1 and s1["warnings"]
    # the caller's resolver supplies the bytes
    s2 = materialize_tree(tree, out, clean=True,
                          resolve=lambda n: src if n.get("rel") == "bulk-data.parquet"
                          else None)
    assert s2["missing"] == 0 and (s2["linked"] + s2["copied"]) == 1
    assert (out / "runs/r1/output/bulk-data.parquet").exists()


def test_materialize_route_wires_the_run_resolver():
    """The bio materialize route must pass the run-backed resolver — a bare
    materialize_tree(tree, out) silently regresses every kernel-run output
    to 'missing'."""
    import inspect
    import content.bio.web.routes.platform_bio as pb
    src = inspect.getsource(pb.project_materialize)
    assert "resolve=_run_backed_path" in src


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "pytest", __file__, "-v"]))
