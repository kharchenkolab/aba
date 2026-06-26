"""Stage 2 tests: entity exec_id pointer + code-lookup helper.

Covers:
  - schema: entities.exec_id / artifact_kind / artifact_idx columns
  - create_entity accepts and stores the new fields
  - get_entity surfaces them in the returned dict
  - lookup_code_for_entity:
      * uses exec record when entity.exec_id is set
      * falls back to entity.producing_code on legacy entities
      * returns '' when neither is available
  - Integration: run_python → result has exec_id → registry creates figure
    entity carrying that exec_id → helper resolves the code

Run:  .venv/bin/python tests/test_exec_records_stage2.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_execrec_s2_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "s2.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"] = "/workspace/aba-runtime/envs"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn   # noqa: E402
from core.graph import entities, exec_records   # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_schema_has_new_columns():
    print("\n[1] entities table has exec_id / artifact_kind / artifact_idx")
    init_db()
    with _conn() as c:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(entities)").fetchall()}
    for col in ("exec_id", "artifact_kind", "artifact_idx"):
        check(f"column {col} present", col in cols)
    with _conn() as c:
        idx = {r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='entities'"
        ).fetchall()}
    check("idx_entities_exec present", "idx_entities_exec" in idx)


def test_create_entity_with_new_fields():
    print("\n[2] create_entity stores exec_id / artifact_kind / artifact_idx")
    eid = entities.create_entity(
        entity_type="figure",
        title="Test figure",
        exec_id="exec_test_001",
        artifact_kind="figure",
        artifact_idx=2,
        artifact_path="/tmp/test-fig.png",
    )
    rec = entities.get_entity(eid)
    check("entity created", rec is not None)
    if not rec:
        return
    check("rec.exec_id matches", rec.get("exec_id") == "exec_test_001")
    check("rec.artifact_kind = figure", rec.get("artifact_kind") == "figure")
    check("rec.artifact_idx = 2", rec.get("artifact_idx") == 2)
    # Legacy fields absent
    check("rec.producing_code is None (not set)", rec.get("producing_code") is None)


def test_legacy_entity_unchanged():
    print("\n[3] entity without exec_id has no code reachable")
    # Post Cutover 4: producing_code is gone. An entity with no exec_id
    # is a degraded entity — lookup returns "". (Real legacy entities are
    # auto-backfilled with synthetic exec records at init_db; only
    # entities that never had code in the first place stay code-less.)
    eid = entities.create_entity(
        entity_type="figure", title="No-code figure",
        artifact_path="/tmp/no-code.png",
    )
    rec = entities.get_entity(eid)
    check("entity created", rec is not None)
    if not rec:
        return
    check("rec.exec_id is None", rec.get("exec_id") is None)
    check("rec.artifact_kind is None", rec.get("artifact_kind") is None)
    check("no producing_code key in entity dict",
          "producing_code" not in rec)
    check("lookup_code_for_entity returns ''",
          exec_records.lookup_code_for_entity(rec) == "")


def test_lookup_code_for_entity_via_exec():
    print("\n[4] lookup_code_for_entity uses exec record when exec_id is set")
    # Stand up a real exec record so the helper can resolve it
    cwd = Path(_tmp) / "lookup_a"; cwd.mkdir(exist_ok=True)
    code = "print('from exec record')\n"
    ex_id = exec_records.create(
        thread_id="thr_lookup", run_id=None, tool_name="run_python",
        status="ok", code=code, started_at="2026-06-06T11:00:00Z",
        completed_at="2026-06-06T11:00:01Z", cwd=cwd,
    )
    eid = entities.create_entity(
        entity_type="figure",
        title="Helper test",
        exec_id=ex_id,
        artifact_kind="figure",
        artifact_idx=0,
        artifact_path="/tmp/helper.png",
        # NOTE: deliberately NOT setting producing_code — the helper should
        # find the code via the exec record.
    )
    rec = entities.get_entity(eid)
    looked = exec_records.lookup_code_for_entity(rec)
    check("helper returns code from exec record", looked == code,
          f"got {looked!r}")


def test_lookup_code_for_entity_no_exec():
    print("\n[5] lookup_code_for_entity returns '' when no exec_id (post Cutover 4)")
    # Post-cutover: legacy fallback to producing_code is gone. An entity
    # without exec_id resolves to "". (Backfilled entities will have
    # exec_id set by the backfill on init_db.)
    eid = entities.create_entity(
        entity_type="figure", title="No exec",
        artifact_path="/tmp/none.png",
    )
    rec = entities.get_entity(eid)
    looked = exec_records.lookup_code_for_entity(rec)
    check("no exec_id → ''", looked == "", f"got {looked!r}")


def test_lookup_code_for_entity_dangling_exec():
    print("\n[6] helper returns '' when exec_id points nowhere")
    eid = entities.create_entity(
        entity_type="figure", title="Broken pointer",
        artifact_path="/tmp/broken.png",
        exec_id="exec_does_not_exist",
        artifact_kind="figure", artifact_idx=0,
    )
    rec = entities.get_entity(eid)
    looked = exec_records.lookup_code_for_entity(rec)
    check("dangling exec_id → ''", looked == "", f"got {looked!r}")


def test_lookup_code_for_entity_none_entity():
    print("\n[7] helper returns '' on None / empty inputs")
    check("None → ''", exec_records.lookup_code_for_entity(None) == "")
    check("empty dict → ''", exec_records.lookup_code_for_entity({}) == "")


def test_integration_run_python_to_figure_entity():
    """Full path: run_python writes exec record → user pins via pin_artifact
    → figure entity materializes carrying the exec_id → helper resolves
    the code via the exec record.

    Post Option-B-Phase-5: registry no longer mints figure entities on
    harvest. Materialization is explicit (user pin or auto-pin), so this
    integration test exercises pin_artifact as the entity-creation step.
    """
    print("\n[8] integration: run_python → pin_artifact → figure entity carries exec_id")
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact

    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure()\nplt.plot([1, 2, 3], [4, 5, 6])\n"
        "plt.savefig('s2.png')\nplt.close('all')\n"
        "print('produced s2.png')\n"
    )
    ctx = {"thread_id": "thr_s2_int", "tool_use_id": "toolu_s2_int"}
    res = run_python({"code": code}, ctx=ctx)
    check("run_python ok", res.get("returncode") == 0,
          f"stderr={res.get('stderr')!r}")
    check("result.exec_id present", bool(res.get("exec_id")))
    check("result.plots has at least one figure",
          isinstance(res.get("plots"), list) and len(res["plots"]) >= 1)
    if not res.get("exec_id") or not res.get("plots"):
        return
    # Registry still ensures the Run + manifest are set up. It no longer
    # mints figure/table entities — assert that.
    new_recs = register_artifacts_from_tool_result(
        tool_name="run_python",
        tool_input={"code": code},
        result_obj=res,
        focused_entity_id=None,
        analysis_ctx={},
        thread_id="thr_s2_int",
    )
    figure_recs = [r for r in new_recs if r["type"] == "figure"]
    check("registry NO LONGER auto-mints figure entities",
          len(figure_recs) == 0,
          f"got {len(figure_recs)} unexpected entities")
    # Explicit materialization via pin_artifact
    pinned = pin_artifact(res["exec_id"], "figure", 0,
                          wrap_in_result=False, thread_id="thr_s2_int")
    fig = entities.get_entity(pinned["entity_id"])
    check("pin_artifact created the figure entity", fig is not None)
    if not fig:
        return
    check("figure.exec_id matches run result", fig.get("exec_id") == res["exec_id"])
    check("figure.artifact_kind = figure", fig.get("artifact_kind") == "figure")
    check("figure.artifact_idx = 0", fig.get("artifact_idx") == 0)
    # Helper round-trips
    refetched = entities.get_entity(fig["id"])
    looked = exec_records.lookup_code_for_entity(refetched)
    check("helper resolves code via exec record",
          looked == code, f"got {looked[:40]!r}…")


def main() -> int:
    test_schema_has_new_columns()
    test_create_entity_with_new_fields()
    test_legacy_entity_unchanged()
    test_lookup_code_for_entity_via_exec()
    test_lookup_code_for_entity_no_exec()
    test_lookup_code_for_entity_dangling_exec()
    test_lookup_code_for_entity_none_entity()
    test_integration_run_python_to_figure_entity()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL EXEC-RECORDS STAGE-2 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
