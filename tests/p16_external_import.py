"""External import of Runs & Datasets (misc/external_import.md).

Covers the by-reference import path end to end WITHOUT the async worker:
  1. import_run_code SCRAPES an external results dir into the standard result_obj (copies small
     viewables, references the bulk, parses + publishes MultiQC) — so it flows through the same
     finalize→present chain as a native pipeline.
  2. harvest_artifacts honors the max_files cap (an outside tree can hold 100s of QC files).
  3. open_imported_run creates a by-reference Run with a drift baseline in its metadata.
  4. Drift is FLAGGED when the external payload changes or vanishes.
  5. _import_done_text presents the imported Run (not a "no-op"/failed job).
  6. DB-CRASH RECOVERY: the imported Run's by-reference metadata lives in the entity SIDECAR
     (Location 2), so recovery reconstructs it fully even with the external dir (Location 1) GONE.

Run: .venv/bin/python tests/p16_external_import.py
"""
from __future__ import annotations
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_extimport_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
for _k in ("DATA_DIR", "ARTIFACTS_DIR", "ABA_WORK_DIR"):
    os.environ.pop(_k, None)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()

from core.exec.import_run import import_run_code, IMPORT_HARVEST_CAP  # noqa: E402
from core.exec.run import harvest_artifacts  # noqa: E402
from core.data.external_ref import check_drift, fingerprint  # noqa: E402


def _make_external_run(root: Path) -> Path:
    """A plausible external nf-core-style results tree."""
    src = root / "vbcf_results"
    (src / "multiqc").mkdir(parents=True)
    (src / "multiqc" / "multiqc_report.html").write_text("<html><body>MultiQC</body></html>")
    (src / "fastqc").mkdir()
    (src / "fastqc" / "s1_fastqc.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 200)
    (src / "fastqc" / "s2_fastqc.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 200)
    (src / "star_salmon").mkdir()
    (src / "star_salmon" / "counts.csv").write_text("gene,s1,s2\nA,5,7\nB,1,0\n")
    (src / "star_salmon" / "aln.bam").write_bytes(b"BAM" + b"\x00" * 4096)   # bulk, unrecognized → referenced
    return src


def test_import_run_code_scrapes_external_dir():
    root = Path(tempfile.mkdtemp(dir=_TMP))
    src = _make_external_run(root)
    r = import_run_code(str(src), project_id="prj_scrape", run_id="imp_1")
    assert r["returncode"] == 0, r
    assert r["execution_mode"] == "import"
    # small viewables copied (as pinnable children); bulk referenced only
    names = [a.get("original_name", "") for a in r["plots"] + r["tables"] + r["files"]]
    assert any(n.endswith("s1_fastqc.png") for n in names), names
    assert any(n.endswith("counts.csv") for n in names), names
    assert not any(n.endswith("aln.bam") for n in names), "bulk .bam must NOT be copied"
    # the whole tree (incl. the bulk) is referenced for the manifest
    assert any(o.endswith("aln.bam") for o in r["outputs"]), r["outputs"]
    # MultiQC report published to a servable URL
    assert r["multiqc"].get("report_url", "").startswith("/artifacts/prj_scrape/"), r["multiqc"]
    # provenance block routes to the workflow exec record; cwd is LOCAL (never the external dir)
    assert r["workflow"]["imported"] is True and r["workflow"]["source_dir"] == str(src)
    assert Path(r["cwd"]) != src and "aba" in r["cwd"].lower()
    print("OK  test_import_run_code_scrapes_external_dir")


def test_import_run_code_missing_dir():
    r = import_run_code(str(Path(_TMP) / "does_not_exist"), project_id="p", run_id="imp_x")
    assert r["returncode"] == 1 and "not found" in r["error"].lower(), r
    print("OK  test_import_run_code_missing_dir")


def test_harvest_cap_bounds_copies_and_warns():
    root = Path(tempfile.mkdtemp(dir=_TMP))
    d = root / "many"; d.mkdir()
    for i in range(5):
        (d / f"p{i}.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    plots, tables, files, warns = harvest_artifacts(d, project_id="prj_cap", max_files=2)
    assert len(plots) == 2, f"cap not honored: {len(plots)}"
    assert any("max_files" in w for w in warns), warns
    print("OK  test_harvest_cap_bounds_copies_and_warns")


def test_open_imported_run_is_by_reference_with_baseline():
    from content.bio.lifecycle.runs import open_imported_run
    from core.graph.entities import get_entity
    root = Path(tempfile.mkdtemp(dir=_TMP))
    src = _make_external_run(root)
    rid = open_imported_run("thr_imp", "Imported: nf-core/rnaseq", str(src),
                            pipeline="nf-core/rnaseq", revision="3.21.0", source="vbcf")
    ent = get_entity(rid)
    assert ent and ent["type"] == "analysis"
    md = ent.get("metadata") or {}
    assert md.get("by_reference") is True and md.get("ref_path") == str(src)
    assert md.get("origin") == "external" and md.get("pipeline") == "nf-core/rnaseq"
    assert ent.get("artifact_path") == str(src), "artifact_path must point at the external dir"
    fp = md.get("import_fingerprint") or {}
    assert fp.get("exists") and fp.get("digest"), f"drift baseline missing: {fp}"
    # drift is fresh right after import, changed after a new file, missing after removal
    assert check_drift(md)["stale"] is False
    (src / "new_output.txt").write_text("late arrival")
    assert check_drift(md)["reason"] == "changed"
    print("OK  test_open_imported_run_is_by_reference_with_baseline")


def test_import_done_text_presents_the_run():
    from core.jobs.continuation import _import_done_text, _is_import_run_job, _continuation_message_text
    root = Path(tempfile.mkdtemp(dir=_TMP))
    src = _make_external_run(root)
    job = {"id": "job_imp", "kind": "import_run", "status": "done", "title": "Import",
           "params": {"thread_id": "t", "project_id": "prj_pres", "run_id": "imp_9",
                      "source_dir": str(src), "pipeline": "nf-core/rnaseq"}}
    assert _is_import_run_job(job)
    msg = _import_done_text(job, "prj_pres")
    assert msg.startswith("[continuation: imported run"), msg[:80]
    assert "PRESENT" in msg and str(src) in msg
    assert "repeatab" in msg.lower(), "should note limited repeatability"
    # routing: the dispatcher picks the import branch even though params carry a pipeline
    routed = _continuation_message_text(job, "prj_pres")
    assert routed.startswith("[continuation: imported run"), routed[:80]
    print("OK  test_import_done_text_presents_the_run")


def test_db_crash_recovery_reconstructs_imported_run_without_location1():
    """The user's 'DB crash = a form of import': an imported Run's by-reference metadata lives in
    the entity sidecar (Location 2). Recovery must rebuild it FULLY even when the external payload
    (Location 1) is gone — proving recovery never depended on the external dir."""
    from content.bio.lifecycle.runs import open_imported_run
    from core.graph.entities import get_entity
    from core.recovery.scribe import Scribe, set_scribe_override, EntityUpserted
    from core.recovery.walker import recover_project
    from core import config as _config

    root = Path(tempfile.mkdtemp(dir=_TMP))
    src = _make_external_run(root)
    rid = open_imported_run("thr_rec", "Imported: nf-core/rnaseq", str(src),
                            pipeline="nf-core/rnaseq", revision="3.21.0")
    row = get_entity(rid)
    base_fp = (row.get("metadata") or {}).get("import_fingerprint") or {}
    assert base_fp.get("digest"), "no baseline captured"

    # Write the entity sidecar the way the scribe does (Location 2), under a project dir.
    pid = "prj_recover"
    s = Scribe(tick_interval=10_000.0)
    set_scribe_override(s)
    s.enqueue(EntityUpserted(pid=pid, entity_id=rid, row=row))
    s.flush()
    proj_dir = _config.PROJECTS_DIR / pid
    sidecar = proj_dir / "entities" / f"{rid}.json"
    assert sidecar.is_file(), f"sidecar not written: {sidecar}"
    (proj_dir / "project.json").write_text(json.dumps({"pid": pid}))

    # Location 1 is DELETED before recovery — the external results are gone.
    shutil.rmtree(src)
    assert not src.exists()

    # Recover into a fresh DB and read the reconstructed entity back.
    rec_db = Path(tempfile.mkdtemp(dir=_TMP)) / "recovered.db"
    report = recover_project(proj_dir, target_db=rec_db, target_pid=pid)
    assert report.entities >= 1, f"recovery found no entities: {report}"

    import sqlite3
    c = sqlite3.connect(rec_db); c.row_factory = sqlite3.Row
    got = c.execute("SELECT type, artifact_path, metadata FROM entities WHERE id=?", (rid,)).fetchone()
    c.close()
    assert got is not None, "imported Run was NOT recovered from the sidecar"
    assert got["type"] == "analysis"
    assert got["artifact_path"] == str(src), "artifact_path (ref to Location 1) not recovered"
    md = json.loads(got["metadata"] or "{}")
    assert md.get("by_reference") is True and md.get("ref_path") == str(src)
    assert (md.get("import_fingerprint") or {}).get("digest") == base_fp.get("digest"), \
        "drift baseline did not survive recovery"
    # And with Location 1 gone, drift now correctly reports it missing (recovery-safe flag).
    assert check_drift(md)["reason"] == "missing"
    print("OK  test_db_crash_recovery_reconstructs_imported_run_without_location1")


def test_register_dataset_external_is_by_reference_with_baseline():
    """Datasets (the piece that mostly existed): registering an EXTERNAL path references it in
    place with a drift baseline — no copy — and drift flags a later change."""
    from content.bio.tools.curation import register_dataset_tool
    from core.graph.entities import get_entity
    root = Path(tempfile.mkdtemp(dir=_TMP))
    ext = root / "external_10x"; ext.mkdir()
    (ext / "matrix.mtx").write_text("%%MatrixMarket\n3 3 3\n1 1 5\n")
    (ext / "barcodes.tsv").write_text("AAA\nCCC\n")
    out = register_dataset_tool({"path": str(ext), "title": "External 10x", "source": "core"},
                                {"thread_id": "thr_ds"})
    assert out.get("dataset_id"), out
    md = (get_entity(out["dataset_id"]) or {}).get("metadata") or {}
    assert md.get("by_reference") is True and md.get("ref_path") == str(ext)
    assert (md.get("import_fingerprint") or {}).get("digest"), f"dataset baseline missing: {md}"
    assert check_drift(md)["stale"] is False
    (ext / "features.tsv").write_text("g1\ng2\n")                # external data grows
    assert check_drift(md)["reason"] == "changed"
    print("OK  test_register_dataset_external_is_by_reference_with_baseline")


def test_metadata_edits_land_in_location2_not_location1():
    """Modifications to an imported entity (rename, notes, tags) go to the ABA-owned sidecar
    (Location 2); the external payload (Location 1) is never touched, and the ref stays intact."""
    from content.bio.lifecycle.runs import open_imported_run
    from core.graph.entities import get_entity, update_entity
    root = Path(tempfile.mkdtemp(dir=_TMP))
    src = _make_external_run(root)
    before = fingerprint(str(src))["digest"]
    rid = open_imported_run("thr_mod", "Imported", str(src), pipeline="nf-core/rnaseq")
    md = dict((get_entity(rid) or {}).get("metadata") or {})
    md["user_tag"] = "keep"
    update_entity(rid, title="Renamed import", metadata=md, notes="reviewed by PI")
    ent2 = get_entity(rid)
    assert ent2["title"] == "Renamed import"
    assert (ent2.get("metadata") or {}).get("ref_path") == str(src)      # ref survives the edit
    assert (ent2.get("metadata") or {}).get("user_tag") == "keep"
    assert fingerprint(str(src))["digest"] == before, "the edit must NOT touch Location 1"
    print("OK  test_metadata_edits_land_in_location2_not_location1")


def test_scrape_populates_imported_run_manifest():
    """The load-bearing wiring: the on_job_complete path must recognize kind='import_run' and
    refresh the Run's output MANIFEST from its by-reference artifact_path. (Post-cutover, harvested
    outputs are NOT minted as child entities — they surface via the manifest + exec record and pin
    on demand.) Regression guard for the bug where 'import_run' wasn't in _ARTIFACT_TOOLS, so an
    imported Run's outputs never appeared."""
    from content.bio.lifecycle.runs import open_imported_run
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from core.graph.entities import get_entity
    root = Path(tempfile.mkdtemp(dir=_TMP))
    src = _make_external_run(root)
    rid = open_imported_run("thr_att", "Imported", str(src), pipeline="nf-core/rnaseq")
    r = import_run_code(str(src), project_id="prj_att", run_id="imp_att")
    register_artifacts_from_tool_result(
        tool_name="import_run", tool_input={}, result_obj=r,
        focused_entity_id=None, analysis_ctx={"analysis_id": rid}, thread_id="thr_att")
    man = ((get_entity(rid) or {}).get("metadata") or {}).get("run") or {}
    labels = [o.get("label", "") for o in (man.get("outputs") or [])]
    assert labels, "imported Run manifest was not populated (import_run not recognized?)"
    assert any("multiqc_report.html" in l for l in labels), labels
    assert any(l.endswith("aln.bam") for l in labels), \
        f"the bulk .bam must be listed (browsable, not copied): {labels}"
    print("OK  test_scrape_populates_imported_run_manifest")


def main() -> int:
    tests = [test_import_run_code_scrapes_external_dir,
             test_scrape_populates_imported_run_manifest,
             test_import_run_code_missing_dir,
             test_harvest_cap_bounds_copies_and_warns,
             test_open_imported_run_is_by_reference_with_baseline,
             test_import_done_text_presents_the_run,
             test_register_dataset_external_is_by_reference_with_baseline,
             test_metadata_edits_land_in_location2_not_location1,
             test_db_crash_recovery_reconstructs_imported_run_without_location1]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as e:  # noqa: BLE001
            failed.append(t.__name__)
            print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"\n{'all ' + str(len(tests)) + ' passed' if not failed else str(len(failed)) + ' failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
