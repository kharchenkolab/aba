"""I3 — Compatibility report.

Verify that after a recover_project walk, a recovery_report.json appears in
the project dir capturing:
- source vs host aba_commit/version
- referenced entity types / recipes / capabilities / tools
- missing-list entries for things the host doesn't have

We synthesize an imported project with known references and check the
report matches.

Run: .venv/bin/python tests/test_scribe_compat_report.py
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_compat_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
for k in ("ABA_DB_PATH",):
    os.environ.pop(k, None)

sys.path.insert(0, str(ROOT / "backend"))

from core.recovery.scribe import Scribe, set_scribe_override   # noqa: E402

_scribe = Scribe(tick_interval=10_000.0)
set_scribe_override(_scribe)

from core import projects                                       # noqa: E402
from core.recovery.walker import recover_project                # noqa: E402

projects.init()

PROOT = Path(_tmp) / "projects"


def _build_import_with_refs(pid: str) -> Path:
    """Synthesize a recovery archive that references known + unknown deps."""
    pdir = PROOT / pid
    (pdir / "entities").mkdir(parents=True, exist_ok=True)

    pdir.joinpath("project.json").write_text(json.dumps({
        "_v": 1, "_ts": "2026-06-08T00:00:00Z",
        "pid": pid, "aba_commit": "source-commit-abc", "aba_version": "0.41.x",
        "source_project_dir": str(pdir),
        "registry": {"id": pid, "name": "Compat test",
                     "created_at": "2026-06-08T00:00:00Z",
                     "last_touched": "2026-06-08T00:00:00Z"},
    }))

    # Entity with a known type (analysis) but referencing an unknown recipe.
    pdir.joinpath("entities", "ana_a1.json").write_text(json.dumps({
        "_v": 1, "_ts": "2026-06-08T00:00:00Z",
        "id": "ana_a1", "type": "analysis", "title": "analysis with recipe ref",
        "status": "active",
        "metadata": {"skill": "made-up-recipe-name-x9z"},
        "created_at": "2026-06-08T00:00:00Z",
        "updated_at": "2026-06-08T00:00:00Z",
    }))
    # Plan-shaped entity with nested steps referencing recipes + capabilities.
    pdir.joinpath("entities", "pln_a1.json").write_text(json.dumps({
        "_v": 1, "_ts": "2026-06-08T00:00:00Z",
        "id": "pln_a1", "type": "plan", "title": "plan",
        "status": "active",
        "metadata": {
            "steps": [
                {"skill": "made-up-recipe-name-x9z", "capabilities": ["nonexistent-cap-q1"]},
                {"recipe": "another-unknown-recipe"},
            ],
        },
        "created_at": "2026-06-08T00:00:00Z",
        "updated_at": "2026-06-08T00:00:00Z",
    }))
    # Entity with a definitely-unknown type to exercise that branch.
    pdir.joinpath("entities", "fake_t.json").write_text(json.dumps({
        "_v": 1, "_ts": "2026-06-08T00:00:00Z",
        "id": "fake_t", "type": "made_up_type_never_seen", "title": "bogus",
        "status": "active",
        "created_at": "2026-06-08T00:00:00Z",
        "updated_at": "2026-06-08T00:00:00Z",
    }))

    # Exec sidecar with both a known tool (run_python) and unknown
    exec_dir = pdir / "work" / "thr_x" / ".exec"
    exec_dir.mkdir(parents=True, exist_ok=True)
    exec_dir.joinpath("exec_known.json").write_text(json.dumps({
        "exec_id": "exec_known", "thread_id": "thr_x", "tool_name": "run_python",
        "status": "ok", "started_at": "2026-06-08T00:00:00Z",
        "completed_at": "2026-06-08T00:00:01Z",
    }))
    exec_dir.joinpath("exec_unknown.json").write_text(json.dumps({
        "exec_id": "exec_unknown", "thread_id": "thr_x", "tool_name": "definitely_nonexistent_tool_v1",
        "status": "ok", "started_at": "2026-06-08T00:00:00Z",
        "completed_at": "2026-06-08T00:00:01Z",
    }))

    return pdir


# ─── tests ──────────────────────────────────────────────────────────────────
def test_recovery_report_written_with_references_and_missing():
    pdir = _build_import_with_refs("prj_compatA")
    rep = recover_project(pdir)
    # File written
    report_path = pdir / "recovery_report.json"
    assert report_path.exists(), f"recovery_report.json missing; warnings: {rep.warnings}"
    j = json.loads(report_path.read_text())
    # Source fingerprint
    assert j["source"]["aba_commit"] == "source-commit-abc"
    assert j["source"]["aba_version"] == "0.41.x"
    # Host fingerprint present
    assert j["host"]["aba_commit"]
    # Referenced collections include what we seeded
    refs = j["referenced"]
    assert "analysis" in refs["entity_types"]
    assert "plan" in refs["entity_types"]
    assert "made_up_type_never_seen" in refs["entity_types"]
    assert "made-up-recipe-name-x9z" in refs["recipes"]
    assert "another-unknown-recipe" in refs["recipes"]
    assert "nonexistent-cap-q1" in refs["capabilities"]
    assert "run_python" in refs["tools"]
    assert "definitely_nonexistent_tool_v1" in refs["tools"]
    # Missing-list entries (these registries are present in dev tree)
    missing = j["missing"]
    assert "made_up_type_never_seen" in missing["entity_types"]
    assert "made-up-recipe-name-x9z" in missing["recipes"]
    assert "another-unknown-recipe" in missing["recipes"]
    assert "nonexistent-cap-q1" in missing["capabilities"]
    assert "definitely_nonexistent_tool_v1" in missing["tools"]
    # run_python should NOT appear in missing tools
    assert "run_python" not in missing["tools"]
    # analysis + plan should NOT appear in missing entity types (they're declared)
    assert "analysis" not in missing["entity_types"]
    assert "plan" not in missing["entity_types"]


def test_recovery_report_dry_run_does_not_write():
    pdir = _build_import_with_refs("prj_compatB")
    rep = recover_project(pdir, dry_run=True)
    assert not (pdir / "recovery_report.json").exists(), \
        "dry-run must not write recovery_report.json"
    # Cleanup the temp DB
    Path(rep.target_db).unlink(missing_ok=True)


# ─── runner ─────────────────────────────────────────────────────────────────
TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]

if __name__ == "__main__":
    fails = 0
    for fn in TESTS:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except Exception as e:
            fails += 1
            import traceback; traceback.print_exc()
            print(f"  FAIL {fn.__name__}: {e!r}")
    if fails:
        print(f"\n{fails}/{len(TESTS)} FAILED")
        sys.exit(1)
    print(f"\nall {len(TESTS)} tests passed")
