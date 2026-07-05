"""Option B / Phase 5 cutover acceptance test.

Verifies the central promise of the lazy-materialization cutover:
running cells that produce many artifacts creates ZERO new figure/table
entity rows. Artifacts are reachable via exec records; entities only
exist for things the user pinned.

The full integration scenario from the design doc:
  1. Run a cell producing 3 figures + 2 tables
  2. ZERO new entity rows (no shadow figures, no shadow tables)
  3. All 5 artifacts reachable via artifacts_for_run(run_id)
  4. Pin one figure → 1 entity row appears
  5. Make-revision of the pinned figure → 1 more entity (also pinned via the wasRevisionOf chain)
  6. Unpin → entity flag flips, edges intact
  7. Re-pin → same entity_id (idempotent)

Run: .venv/bin/python tests/test_option_b_phase5.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_optB_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "ob.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn          # noqa: E402
from core.graph import entities, exec_records          # noqa: E402
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _entity_count_by_type(t: str) -> int:
    with _conn() as c:
        r = c.execute("SELECT COUNT(*) AS n FROM entities WHERE type=?", (t,)).fetchone()
    return r["n"]


def test_full_scenario():
    print("\n[scenario] cell with 3 figures + 2 tables → ZERO entities → pin one → 1 entity")
    init_db()
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    from content.bio.lifecycle.revisions import make_revision
    from core.exec.artifacts import artifacts_for_run

    figures_before = _entity_count_by_type("figure")
    tables_before  = _entity_count_by_type("table")

    code = """
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

for i in range(3):
    plt.figure(); plt.plot([1,2,3],[i,i+1,i+2])
    plt.savefig(f'fig_{i}.png'); plt.close('all')

pd.DataFrame({'a':[1,2],'b':[3,4]}).to_csv('table_a.csv', index=False)
pd.DataFrame({'x':[5,6]}).to_csv('table_b.csv', index=False)
print('done')
"""
    res = run_python({"code": code}, ctx={"thread_id": "thr_ob",
                                          "tool_use_id": "tu_ob"})
    check("run_python ok", res.get("returncode") == 0,
          f"stderr={res.get('stderr')!r}")
    check("exec_id present", isinstance(res.get("exec_id"), str))
    check("result has 3 plots", len(res.get("plots") or []) == 3)
    check("result has 2 tables", len(res.get("tables") or []) == 2)

    # Trigger the registry hook (manifest refresh; no entity minting now)
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id="thr_ob",
    )

    # The whole point of the cutover: no new figure/table entities.
    figures_after = _entity_count_by_type("figure")
    tables_after  = _entity_count_by_type("table")
    check("ZERO new figure entities created",
          figures_after == figures_before,
          f"before={figures_before} after={figures_after}")
    check("ZERO new table entities created",
          tables_after == tables_before,
          f"before={tables_before} after={tables_after}")

    # Artifacts are still reachable via the exec record + artifacts_for_run.
    # The run was attributed to an ambient analysis (since no explicit Run is open).
    # Find that ambient analysis and resolve its artifacts.
    from content.bio.lifecycle.runs import active_run_id
    ambient = active_run_id("thr_ob")
    check("ambient analysis present", isinstance(ambient, str))
    if ambient:
        run_arts = artifacts_for_run(ambient)
        # 3 figures + 2 tables, plus any other files (matplotlib might add nothing else)
        fig_arts = [a for a in run_arts if a["kind"] == "figure"]
        tab_arts = [a for a in run_arts if a["kind"] == "table"]
        check("3 figure artifacts reachable",
              len(fig_arts) == 3, f"got {len(fig_arts)}")
        check("2 table artifacts reachable",
              len(tab_arts) == 2, f"got {len(tab_arts)}")

    # Pin one figure via the artifact path
    pinned = pin_artifact(res["exec_id"], "figure", 0,
                          wrap_in_result=False, thread_id="thr_ob")
    figures_after_pin = _entity_count_by_type("figure")
    check("after pin: exactly 1 new figure entity",
          figures_after_pin == figures_before + 1,
          f"got {figures_after_pin}, expected {figures_before + 1}")
    fig_id = pinned["entity_id"]
    rec = entities.get_entity(fig_id)
    check("pinned figure has the right exec_id",
          rec and rec.get("exec_id") == res["exec_id"])

    # Re-pin same artifact → no new entity
    pinned2 = pin_artifact(res["exec_id"], "figure", 0,
                           wrap_in_result=False, thread_id="thr_ob")
    figures_after_repin = _entity_count_by_type("figure")
    check("re-pin idempotent: no new entity",
          figures_after_repin == figures_after_pin,
          f"got {figures_after_repin}")
    check("re-pin returns same entity_id",
          pinned2["entity_id"] == fig_id)
    check("re-pin was_new = False", pinned2.get("was_new") is False)

    # Make-revision: should add 1 more figure entity
    rev_code = code.replace("plt.plot([1,2,3],[i,i+1,i+2])",
                            "plt.plot([1,2,3],[10*i, 10*(i+1), 10*(i+2)])")
    rev = make_revision(fig_id, rev_code, thread_id="thr_ob")
    figures_after_rev = _entity_count_by_type("figure")
    check("after revision: 1 more figure entity",
          figures_after_rev == figures_after_pin + 1,
          f"got {figures_after_rev}, expected {figures_after_pin + 1}")
    check("revision is not the same as parent",
          rev["new_entity_id"] != fig_id)
    # The new revision wasRevisionOf the parent
    from core.graph.edges import edges_from
    out_edges = edges_from(rev["new_entity_id"])
    check("wasRevisionOf edge present",
          any(e["rel_type"] == "wasRevisionOf"
              and e["target_id"] == fig_id
              for e in out_edges))


def test_no_used_edge_when_no_focus():
    print("\n[edge] no `analysis --used--> focused` edge when run is unfocused")
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.runs import active_run_id
    from core.graph.edges import edges_from

    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[4,5,6]); plt.savefig('e.png'); plt.close('all')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": "thr_ob2", "tool_use_id": "tu_ob2"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,  # no focus
        analysis_ctx={}, thread_id="thr_ob2",
    )
    rid = active_run_id("thr_ob2")
    if rid:
        out = edges_from(rid)
        used = [e for e in out if e["rel_type"] == "used"]
        check("no `used` edge when run was unfocused",
              len(used) == 0, f"got {used}")


def main() -> int:
    test_full_scenario()
    test_no_used_edge_when_no_focus()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL OPTION-B-PHASE-5 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
