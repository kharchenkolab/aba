"""W2 (weft rewrite §4d): export_bundle's one-file weft path + import.

A weft-run record (exec record carries compute.job_id) exports as ONE tarball
via weft bundle_export, with the entity's record + lineage riding the sealed
metadata envelope; import_bundle_file restores the closure and re-attaches the
envelope. Records without a compute block — and any record when the substrate
is offline — keep the legacy folder bundle.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_bundlew_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ABA_WEFT_WORKSPACE"] = str(Path(_tmp) / "weft-ws")
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ.pop("ABA_DB_PATH", None)
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.bio

import content.bio  # noqa: E402,F401
from core import projects  # noqa: E402
from core.graph import exec_records  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
from content.bio.lifecycle.revisions import export_bundle, import_bundle_file  # noqa: E402

projects.init()

# Static probe only (configuring at import would race other modules — pytest
# imports every file before running any test).
weft_ok = False
try:
    from core.compute import adapter as _ad
    weft_ok = _ad.resolve_pixi() is not None
except Exception:  # noqa: BLE001
    pass


@pytest.fixture(scope="module", autouse=True)
def _substrate():
    """(Re)configure the adapter for THIS module's workspace; torn down +
    offline afterwards so later modules see an unconfigured substrate."""
    if weft_ok:
        _ad.shutdown()
        _ad._adapter = None
        st = _ad.configure()
        assert st["ok"], st["detail"]
    yield
    try:
        _ad.shutdown()
        _ad._status = {"ok": False, "severity": "info", "detail": "torn down by test"}
    except Exception:  # noqa: BLE001
        pass


def _mk_record(pid: str, *, compute: dict | None = None) -> tuple[str, str]:
    """An entity + exec record (optionally weft-run) in project `pid`."""
    cwd = Path(_tmp) / "cwd" / pid
    cwd.mkdir(parents=True, exist_ok=True)
    payload = {"executor": "background:python", "kind": "script",
               "language": "python", "language_version": "3.12",
               "package_versions": {"numpy": "1.26"}, "seed": 0,
               "inputs": [], "produced": [], "exit_code": 0}
    if compute:
        payload["compute"] = compute
    eid = exec_records.create(
        thread_id="t1", run_id="r1", tool_use_id=None, tool_name="run_python",
        status="ok", code="print('repro me')", code_hash="sha256:x",
        started_at="2026-07-14T00:00:00Z", completed_at="2026-07-14T00:00:01Z",
        cwd=str(cwd), payload=payload)
    art = cwd / "fig.png"
    art.write_bytes(b"png")
    ent_id = create_entity(entity_type="figure", title="bundle test fig",
                           artifact_path=str(art), exec_id=eid)
    return ent_id, eid


def test_record_without_compute_block_uses_folder_mode():
    pid = projects.create_project("bexp-folder")["id"]
    projects.set_current(pid)
    ent_id, _ = _mk_record(pid)
    r = export_bundle(ent_id)
    assert r["mode"] == "folder"
    assert "exec_record.json" in r["files"] and "requirements.txt" in r["files"]


def test_offline_substrate_falls_back_to_folder(monkeypatch):
    import core.compute.adapter as ad
    pid = projects.create_project("bexp-off")["id"]
    projects.set_current(pid)
    ent_id, _ = _mk_record(pid, compute={"substrate": "weft", "job_id": "jb_gone"})
    monkeypatch.setattr(ad, "_status", {"ok": False, "severity": "warning",
                                        "detail": "down"})
    monkeypatch.setattr(ad, "_adapter", None)
    r = export_bundle(ent_id)
    assert r["mode"] == "folder"          # loud fallback, never a dead end


@pytest.mark.skipif(not weft_ok, reason="weft substrate unavailable")
def test_weft_export_import_round_trip():
    from core.compute import adapter as ad
    pid = projects.create_project("bexp-weft")["id"]
    projects.set_current(pid)

    # A real finished weft task with a recorded output = the exportable job.
    # NB: weft declared outputs are DIRECTORIES (hash-tree walks a tree).
    w = ad.get_compute()
    sub = w.sync_call("task_submit", {
        "command": "mkdir -p results && echo repro-payload > results/out.txt",
        "site": "local", "outputs": ["results/"], "label": "bundle-src"})
    wid = sub["job_id"]
    for _ in range(120):
        if w.sync_call("task_status", wid)[0]["state"] in ("DONE", "FAILED"):
            break
        time.sleep(0.5)
    assert w.sync_call("task_status", wid)[0]["state"] == "DONE"

    ent_id, eid = _mk_record(pid, compute={"substrate": "weft", "job_id": wid,
                                           "node": "testnode"})
    r = export_bundle(ent_id)
    assert r["mode"] == "weft", r
    bundle = Path(r["bundle_file"])
    assert bundle.exists() and bundle.stat().st_size > 0
    assert r["weft"]["target_job"] == wid
    assert r["weft"]["reproducibility"]

    # Import restores the closure and re-attaches the aba envelope verbatim.
    imp = import_bundle_file(str(bundle))
    assert imp["target_job"] == wid
    assert imp["task"] and "command" in imp["task"]
    assert any(o["path"].startswith("results") for o in (imp["recorded_outputs"] or []))
    aba = imp.get("aba") or {}
    assert aba.get("entity", {}).get("id") == ent_id
    assert aba["exec_record"]["code"] == "print('repro me')"
    assert aba["exec_record"]["compute"]["job_id"] == wid
    assert (aba.get("lineage") or {}) is not None
    assert "re-attach on export" in (imp.get("note") or "")


def test_envelope_is_bounded_and_json():
    """The sealed envelope must stay small structured JSON (weft caps it at
    64 MB; ours is KBs) and decode round-trip clean."""
    pid = projects.create_project("bexp-env")["id"]
    projects.set_current(pid)
    ent_id, eid = _mk_record(pid)
    from core.graph.entities import get_entity
    from content.bio.lifecycle.revisions import _bundle_envelope
    raw = _bundle_envelope(ent_id, get_entity(ent_id), exec_records.get(eid))
    assert len(raw) < 1_000_000
    env = json.loads(raw.decode())
    assert env["format"] == "aba.bundle_meta:v1"
    assert env["entity"]["id"] == ent_id
    assert env["exec_record"]["code"] == "print('repro me')"
