"""Option B / Phase 6 tests: close_run auto-pins declared finals.

When a Run was opened with a plan_entity_id whose steps declare
expected_outputs, close_run materializes + pins any artifact whose
original_name matches a declared filename.

Run: .venv/bin/python tests/test_option_b_phase6.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p6_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p6.db")
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


def _make_plan(steps: list[dict]) -> str:
    """Stub a plan entity with the given steps."""
    return entities.create_entity(
        entity_type="plan", title="Test plan",
        metadata={"steps": steps},
    )


def _make_exec_with_produced(run_id, *, produced, started_at="2026-06-07T17:00:00Z"):
    cwd = Path(_tmp) / f"e_{started_at[-8:].replace(':', '')}"
    cwd.mkdir(parents=True, exist_ok=True)
    return exec_records.create(
        thread_id="thr_p6", run_id=run_id, tool_name="run_python",
        status="ok", code="x = 1", started_at=started_at,
        completed_at=started_at, cwd=cwd,
        payload={"produced": produced, "stdout_tail": "", "stderr_tail": ""},
    )


def test_no_plan_no_autopin():
    print("\n[1] Run without plan_entity_id → no auto-pin on close")
    init_db()
    from content.bio.lifecycle.runs import close_run, open_run

    rid = open_run("thr_p6a", title="Plain Run")
    # Add an exec with a produced figure so the Run isn't empty
    _make_exec_with_produced(rid, produced=[
        {"kind": "figure", "idx": 0, "url": "/u.png", "name": "umap.png"},
    ])
    figures_before = _count_figures()
    out = close_run("thr_p6a")
    check("close_run returned the rid", out == rid)
    figures_after = _count_figures()
    check("no figures auto-pinned (no plan)",
          figures_after == figures_before)


def test_plan_with_expected_outputs_autopins():
    print("\n[2] Run with plan declaring expected_outputs auto-pins matches")
    from content.bio.lifecycle.runs import close_run, open_run

    plan_id = _make_plan([
        {"title": "Step 1", "expected_outputs": ["umap.png", "qc_violin.png"]},
        {"title": "Step 2", "expected_outputs": ["de_results.csv"]},
    ])
    rid = open_run("thr_p6b", title="Planned Run", plan_entity_id=plan_id)
    # Produced artifacts: 2 declared + 1 undeclared
    _make_exec_with_produced(rid, produced=[
        {"kind": "figure", "idx": 0, "url": "/u.png", "name": "umap.png"},
        {"kind": "figure", "idx": 1, "url": "/v.png", "name": "qc_violin.png"},
        {"kind": "figure", "idx": 2, "url": "/x.png", "name": "scratchpad.png"},
    ])
    _make_exec_with_produced(rid, produced=[
        {"kind": "table", "idx": 0, "url": "/d.csv", "name": "de_results.csv"},
        {"kind": "table", "idx": 1, "url": "/o.csv", "name": "other.csv"},
    ], started_at="2026-06-07T17:01:00Z")

    figures_before = _count_figures()
    tables_before = _count_tables()
    close_run("thr_p6b")
    figures_after = _count_figures()
    tables_after = _count_tables()
    check("2 declared figures auto-pinned",
          figures_after - figures_before == 2,
          f"got delta={figures_after - figures_before}")
    check("1 declared table auto-pinned",
          tables_after - tables_before == 1,
          f"got delta={tables_after - tables_before}")
    # The undeclared figures/tables stay unpinned (no entities)
    from core.graph._schema import _conn
    with _conn() as c:
        # Confirm no figure entity for the undeclared scratchpad.png
        r = c.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE type='figure' "
            "AND title='scratchpad.png'"
        ).fetchone()
    check("undeclared 'scratchpad.png' NOT pinned", r["n"] == 0)


def test_plan_with_non_filename_descriptions_skipped():
    print("\n[3] expected_outputs entries WITHOUT extensions are skipped")
    from content.bio.lifecycle.runs import close_run, open_run

    plan_id = _make_plan([
        {"title": "Step 1", "expected_outputs": [
            "DE results",          # no dot — skipped
            "clustering analysis", # no dot — skipped
            "umap.png",            # picked up
        ]},
    ])
    rid = open_run("thr_p6c", title="Run", plan_entity_id=plan_id)
    _make_exec_with_produced(rid, produced=[
        {"kind": "figure", "idx": 0, "url": "/u.png", "name": "umap.png"},
        {"kind": "figure", "idx": 1, "url": "/v.png", "name": "clustering_analysis.png"},
    ])
    figures_before = _count_figures()
    close_run("thr_p6c")
    figures_after = _count_figures()
    # Only "umap.png" gets auto-pinned; "clustering analysis" doesn't match
    # because it lacks a '.', and "clustering_analysis.png" doesn't match
    # any declared name either.
    check("only umap.png auto-pinned",
          figures_after - figures_before == 1,
          f"got delta={figures_after - figures_before}")


def test_autopin_idempotent_on_double_close():
    print("\n[4] closing a (re-opened) Run twice doesn't duplicate pins")
    from content.bio.lifecycle.runs import close_run, open_run

    plan_id = _make_plan([
        {"title": "S", "expected_outputs": ["important.png"]},
    ])
    rid = open_run("thr_p6d", title="R", plan_entity_id=plan_id)
    _make_exec_with_produced(rid, produced=[
        {"kind": "figure", "idx": 0, "url": "/i.png", "name": "important.png"},
    ])
    figures_before = _count_figures()
    close_run("thr_p6d")
    delta1 = _count_figures() - figures_before
    check("first close pins 1", delta1 == 1)
    # Reopen + close again (simulating user reopening then closing)
    # In practice the same Run can be reopened by setting run_state=open.
    md = dict((entities.get_entity(rid) or {}).get("metadata") or {})
    md["run_state"] = "open"
    entities.update_entity(rid, metadata=md)
    close_run("thr_p6d")
    delta2 = _count_figures() - figures_before
    check("second close doesn't add another entity (idempotent)",
          delta2 == 1, f"got delta={delta2}")


def _count_figures() -> int:
    from core.graph._schema import _conn
    with _conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM entities WHERE type='figure'").fetchone()["n"]


def _count_tables() -> int:
    from core.graph._schema import _conn
    with _conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM entities WHERE type='table'").fetchone()["n"]


def main() -> int:
    test_no_plan_no_autopin()
    test_plan_with_expected_outputs_autopins()
    test_plan_with_non_filename_descriptions_skipped()
    test_autopin_idempotent_on_double_close()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL OPTION-B-PHASE-6 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
