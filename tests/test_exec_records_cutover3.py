"""Cutover 3 tests: stop writing producing_code + backfill legacy entities.

Covers:
  - New figure created via registry.register_artifacts has exec_id but
    producing_code is NULL (no denormalized write)
  - backfill_legacy_producing_code creates a synthetic exec record for
    every entity with producing_code AND no exec_id
  - Backfilled entity's exec_id points at the synthetic record
  - Synthetic record has source=backfill and the right code
  - Backfill is idempotent: a second call backfills 0 (everything has
    exec_id now)
  - lookup_code_for_entity resolves backfilled entities via the new path
  - Language is sniffed from the code (R signals → run_r, else run_python)

Run: .venv/bin/python tests/test_exec_records_cutover3.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_co3_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "co3.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"] = "/workspace/aba-runtime/envs"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn          # noqa: E402
from core.graph import entities, exec_records          # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_registry_no_longer_writes_producing_code():
    print("\n[1] registry-created figures no longer carry producing_code")
    init_db()
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result

    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[4,5,6]); plt.savefig('c3.png'); plt.close('all')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": "thr_c3a", "tool_use_id": "tu_c3a"})
    check("run_python ok", res.get("returncode") == 0)
    recs = register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id="thr_c3a",
    )
    figs = [r for r in recs if r["type"] == "figure"]
    check("figure created", len(figs) >= 1)
    if figs:
        f = figs[0]
        check("figure has exec_id set", bool(f.get("exec_id")))
        check("figure has NO producing_code (post-cutover)",
              f.get("producing_code") is None,
              f"got {f.get('producing_code')!r}")
        # The new helper still resolves code via the exec record
        looked = exec_records.lookup_code_for_entity(f)
        check("lookup_code_for_entity still works for new entities",
              looked == code)


def test_backfill_synthesizes_records():
    print("\n[2] backfill_legacy_producing_code synthesizes exec records")
    # Create a legacy-style entity (producing_code set, no exec_id)
    eid_py = entities.create_entity(
        entity_type="figure", title="Legacy py",
        artifact_path="/tmp/legacy_py.png",
        producing_code="import scanpy as sc\nprint('legacy python')\n",
    )
    eid_r = entities.create_entity(
        entity_type="figure", title="Legacy r",
        artifact_path="/tmp/legacy_r.png",
        producing_code="library(Seurat)\nobj <- CreateSeuratObject(counts=mat)\n",
    )
    # Entity that already has exec_id — should NOT be touched
    cwd = Path(_tmp) / "skip"; cwd.mkdir(exist_ok=True)
    real_ex = exec_records.create(
        thread_id="thr_skip", run_id=None, tool_name="run_python",
        status="ok", code="x = 1", started_at="2026-06-06T11:00:00Z", cwd=cwd,
    )
    eid_skip = entities.create_entity(
        entity_type="figure", title="Already has exec",
        artifact_path="/tmp/skip.png",
        producing_code="x = 1",  # duplicated cache, but has exec_id
        exec_id=real_ex, artifact_kind="figure", artifact_idx=0,
    )

    out = exec_records.backfill_legacy_producing_code()
    check("backfilled count > 0",
          out["backfilled"] >= 2, f"got {out!r}")
    check("scanned count is 2 (only the two legacies)",
          out["scanned"] == 2, f"got {out!r}")
    check("errors = 0", out["errors"] == 0)

    # Verify each legacy entity now has exec_id
    rec_py = entities.get_entity(eid_py)
    rec_r = entities.get_entity(eid_r)
    check("legacy python entity now has exec_id", bool(rec_py.get("exec_id")))
    check("legacy r entity now has exec_id", bool(rec_r.get("exec_id")))
    # The skip entity should be unchanged
    rec_skip = entities.get_entity(eid_skip)
    check("entity with prior exec_id is untouched", rec_skip.get("exec_id") == real_ex)


def test_backfill_synthetic_record_shape():
    print("\n[3] synthetic exec records have right fields")
    # Create another legacy entity, backfill, inspect
    eid = entities.create_entity(
        entity_type="figure", title="Inspect me",
        artifact_path="/tmp/i.png",
        producing_code="library(Seurat)\nplot(1:10)\n",
    )
    exec_records.backfill_legacy_producing_code()
    rec = entities.get_entity(eid)
    ex_id = rec.get("exec_id")
    check("entity has exec_id", bool(ex_id))
    if not ex_id:
        return
    body = exec_records.get(ex_id)
    check("get() returns the synthetic record", body is not None)
    if not body:
        return
    check("source = backfill", body.get("source") == "backfill")
    check("language = r (sniffed from library(Seurat))",
          body.get("language") == "r", f"got {body.get('language')}")
    check("tool_name = run_r", body.get("tool_name") == "run_r")
    check("code preserved",
          body.get("code") == "library(Seurat)\nplot(1:10)\n")
    check("code_hash starts with sha256:",
          (body.get("code_hash") or "").startswith("sha256:"))
    check("produced is empty list (degraded)", body.get("produced") == [])
    # Sidecar JSON lives at <runtime>/exec-backfill/<exec_id>.json
    rp = body.get("record_path")
    check("record_path is set", bool(rp))
    if rp:
        check("sidecar in exec-backfill dir", "/exec-backfill/" in rp)


def test_backfill_idempotent():
    print("\n[4] running backfill twice → second call finds nothing")
    # Init was called at top → first backfill already ran.
    # Run again, expect 0 candidates.
    out = exec_records.backfill_legacy_producing_code()
    check("second backfill scans 0 candidates",
          out["scanned"] == 0, f"got {out!r}")
    check("second backfill backfilled 0", out["backfilled"] == 0)


def test_lookup_resolves_backfilled():
    print("\n[5] lookup_code_for_entity resolves backfilled entities")
    eid = entities.create_entity(
        entity_type="figure", title="Lookup",
        artifact_path="/tmp/l.png",
        producing_code="print('resolve via backfill')\n",
    )
    exec_records.backfill_legacy_producing_code()
    rec = entities.get_entity(eid)
    # exec_id is now set
    check("entity has exec_id post-backfill", bool(rec.get("exec_id")))
    looked = exec_records.lookup_code_for_entity(rec)
    check("lookup returns the code via the synthetic exec",
          looked == "print('resolve via backfill')\n",
          f"got {looked!r}")


def test_backfill_dry_run():
    print("\n[6] dry_run=True scans without writing")
    eid = entities.create_entity(
        entity_type="figure", title="Dry",
        artifact_path="/tmp/dry.png",
        producing_code="print('dry')\n",
    )
    out = exec_records.backfill_legacy_producing_code(dry_run=True)
    check("dry_run backfilled count > 0", out["backfilled"] >= 1)
    rec = entities.get_entity(eid)
    check("exec_id NOT set after dry_run (no write)", rec.get("exec_id") is None)
    # Now do for real
    exec_records.backfill_legacy_producing_code()
    rec2 = entities.get_entity(eid)
    check("exec_id IS set after real run", bool(rec2.get("exec_id")))


def main() -> int:
    test_registry_no_longer_writes_producing_code()
    test_backfill_synthesizes_records()
    test_backfill_synthetic_record_shape()
    test_backfill_idempotent()
    test_lookup_resolves_backfilled()
    test_backfill_dry_run()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL EXEC-RECORDS CUTOVER-3 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
