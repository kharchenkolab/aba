"""Option B / Phase 4 backend test: refresh_output_manifest augments each
output entry with the canonical `artifact_id` when the file matches an
artifact recorded in the Run's exec records.

Strategy:
  - Create a Run + a real Run output dir
  - Create an exec record with run_id pointing at the Run, producing a
    "umap.png" artifact (URL doesn't have to be valid for this test —
    we just need the produced[] entry)
  - Write a file with that name on disk under the Run dir
  - Call refresh_output_manifest
  - Assert the resulting manifest entry carries artifact_id

Run: .venv/bin/python tests/test_run_manifest_artifacts.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_runman_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "rm.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                   # noqa: E402
from core.graph import entities, exec_records            # noqa: E402
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_manifest_carries_artifact_id():
    print("\n[1] refresh_output_manifest augments outputs with artifact_id")
    init_db()
    from content.bio.lifecycle.runs import refresh_output_manifest

    # Set up: Run with artifact_path = a real directory
    run_dir = Path(_tmp) / "run_dir"
    run_dir.mkdir(parents=True, exist_ok=True)
    rid = entities.create_entity(
        entity_type="analysis", title="Run A",
        artifact_path=str(run_dir),
        metadata={"thread_id": "thr_rm", "run_state": "open", "origin": "internal"},
    )

    # Two files on disk
    (run_dir / "umap.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    (run_dir / "de.csv").write_text("gene,lfc\nG1,1.2\n")
    (run_dir / "mystery.txt").write_text("just a file")

    # An exec record attributed to the Run, producing umap.png (figure)
    # and de.csv (table). The name has to match the file basename so the
    # manifest's lookup keys on it.
    cwd = Path(_tmp) / "exec_cwd"; cwd.mkdir(exist_ok=True)
    ex = exec_records.create(
        thread_id="thr_rm", run_id=rid, tool_name="run_python",
        status="ok", code="...", started_at="2026-06-07T16:00:00Z",
        completed_at="2026-06-07T16:00:01Z", cwd=cwd,
        payload={"produced": [
            {"kind": "figure", "idx": 0, "url": "/foo.png", "name": "umap.png"},
            {"kind": "table",  "idx": 0, "url": "/foo.csv", "name": "de.csv"},
        ]},
    )

    refresh_output_manifest(rid)

    # Read back
    rec = entities.get_entity(rid)
    outputs = ((rec.get("metadata") or {}).get("run") or {}).get("outputs", [])
    check("3 outputs in manifest", len(outputs) == 3,
          f"got {len(outputs)}: labels={[o.get('label') for o in outputs]}")

    by_label = {o["label"]: o for o in outputs}

    # umap.png → figure with artifact_id pointing at ex:figure:0
    fig = by_label.get("umap.png")
    check("umap.png present", fig is not None)
    if fig:
        check("figure has artifact_id",
              fig.get("artifact_id") == f"{ex}:figure:0",
              f"got {fig.get('artifact_id')!r}")

    # de.csv → table with artifact_id pointing at ex:table:1
    tbl = by_label.get("de.csv")
    check("de.csv present", tbl is not None)
    if tbl:
        check("table has artifact_id",
              tbl.get("artifact_id") == f"{ex}:table:1",
              f"got {tbl.get('artifact_id')!r}")

    # mystery.txt → file, NO artifact_id (no matching produced[] entry)
    myst = by_label.get("mystery.txt")
    check("mystery.txt present", myst is not None)
    if myst:
        check("file without matching artifact has no artifact_id",
              "artifact_id" not in myst,
              f"got keys: {list(myst.keys())}")


def test_manifest_no_execs_still_works():
    print("\n[2] manifest refresh on a Run with no exec records still works")
    from content.bio.lifecycle.runs import refresh_output_manifest

    run_dir = Path(_tmp) / "run_dir_no_exec"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "alone.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
    rid = entities.create_entity(
        entity_type="analysis", title="Lone Run",
        artifact_path=str(run_dir),
        metadata={"thread_id": "thr_rm2", "run_state": "open", "origin": "internal"},
    )
    refresh_output_manifest(rid)
    rec = entities.get_entity(rid)
    outputs = ((rec.get("metadata") or {}).get("run") or {}).get("outputs", [])
    check("manifest has alone.png", len(outputs) == 1)
    if outputs:
        check("no artifact_id when no execs ran",
              "artifact_id" not in outputs[0])


def main() -> int:
    test_manifest_carries_artifact_id()
    test_manifest_no_execs_still_works()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL RUN-MANIFEST-ARTIFACTS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
