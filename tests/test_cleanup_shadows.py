"""Phase 7 tests: cleanup script for legacy shadow entities.

Builds a fake state with:
  - 2 unpinned figures (both shadow candidates)
  - 1 pinned figure (must NOT be touched)
  - 1 unpinned figure with a wasRevisionOf edge (preserved as chain context)
  - 1 unpinned table referenced by a Result via includes edge (preserved)
  - 1 archived unpinned figure (not touched — archived rows are untouched)

Then runs the cleanup script in --apply mode and verifies only the 2
pure-shadow entries got deleted.

Run: .venv/bin/python tests/test_cleanup_shadows.py
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_shadow_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "shadow.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn          # noqa: E402
from core.graph import entities, edges                  # noqa: E402
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_cleanup_keeps_pinned_revisions_results():
    print("\n[1] cleanup leaves pinned / revisioned / Result-referenced entities alone")
    init_db()
    # 2 pure shadows — should be deleted
    sh1 = entities.create_entity(entity_type="figure", title="shadow1",
                                  artifact_path="/sh1.png")
    sh2 = entities.create_entity(entity_type="table", title="shadow2",
                                  artifact_path="/sh2.csv")
    # 1 pinned — keep
    pinned_fig = entities.create_entity(entity_type="figure", title="kept-pinned",
                                         artifact_path="/k.png")
    entities.update_entity(pinned_fig, pinned=True)
    # 1 unpinned with wasRevisionOf edge — keep
    rev_a = entities.create_entity(entity_type="figure", title="rev-A",
                                    artifact_path="/a.png")
    rev_b = entities.create_entity(entity_type="figure", title="rev-B",
                                    artifact_path="/b.png")
    edges.add_edge(rev_b, rev_a, "wasRevisionOf")
    # 1 unpinned table referenced by a Result via 'includes' — keep
    result = entities.create_entity(entity_type="result", title="R")
    inc_tab = entities.create_entity(entity_type="table", title="inc-tab",
                                      artifact_path="/i.csv")
    edges.add_edge(result, inc_tab, "includes")
    # 1 archived (not touched regardless)
    arch_fig = entities.create_entity(entity_type="figure", title="arch",
                                       artifact_path="/x.png")
    entities.archive_entity(arch_fig)

    # Run the script in --apply mode
    script = ROOT / "tools" / "cleanup_shadow_figures.py"
    env = {**os.environ}
    r = subprocess.run(
        [sys.executable, str(script), "--apply"],
        env=env, capture_output=True, text=True,
    )
    print(r.stdout[-1000:])
    if r.returncode != 0:
        print(r.stderr)
        check("script exited 0", False)
        return
    check("script exited 0", True)

    # Verify only sh1 + sh2 got deleted
    check("sh1 deleted", entities.get_entity(sh1) is None)
    check("sh2 deleted", entities.get_entity(sh2) is None)
    check("pinned kept", entities.get_entity(pinned_fig) is not None)
    check("rev-A kept (has incoming wasRevisionOf)",
          entities.get_entity(rev_a) is not None)
    check("rev-B kept (has outgoing wasRevisionOf)",
          entities.get_entity(rev_b) is not None)
    check("inc-tab kept (referenced by Result.includes)",
          entities.get_entity(inc_tab) is not None)
    # archived stays archived
    arch_after = entities.get_entity(arch_fig)
    check("archived figure untouched",
          arch_after is not None and arch_after.get("status") == "archived")


def test_dry_run_changes_nothing():
    print("\n[2] dry-run leaves everything intact")
    # Add another shadow
    sh3 = entities.create_entity(entity_type="figure", title="shadow3",
                                  artifact_path="/sh3.png")
    script = ROOT / "tools" / "cleanup_shadow_figures.py"
    env = {**os.environ}
    r = subprocess.run(
        [sys.executable, str(script)],   # no --apply
        env=env, capture_output=True, text=True,
    )
    check("dry-run exited 0", r.returncode == 0)
    check("entity still exists after dry-run",
          entities.get_entity(sh3) is not None)
    check("output mentions DRY RUN",
          "DRY RUN" in r.stdout)


def main() -> int:
    test_cleanup_keeps_pinned_revisions_results()
    test_dry_run_changes_nothing()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL CLEANUP-SHADOWS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
