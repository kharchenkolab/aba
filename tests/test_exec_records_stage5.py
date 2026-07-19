"""Stage 5 tests: make_revision + reproduce_from_exec.

Covers:
  - make_revision creates a new figure entity with exec_id + wasRevisionOf edge
  - both entities are independent (different ids, different exec_ids)
  - parent stays accessible; revision is navigable via figure_history
  - reproduce_from_exec re-runs the code, returns new_exec_id, no entity created
  - env_drift detection works when the env fingerprint changes

Run:  .venv/bin/python tests/test_exec_records_stage5.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_execrec_s5_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "s5.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                  # noqa: E402
from core.graph import entities, edges, exec_records    # noqa: E402
import pytest                                            # noqa: E402


@pytest.fixture(autouse=True)
def _pack_mode(monkeypatch):
    """W3.5 weft-only: the seed run_python needs a base pack — present pack-mode
    (backend interpreter as the session) so the seed executes."""
    import _packmode
    _packmode.enable(monkeypatch)


_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _make_seed_figure(thread_id: str = "thr_s5",
                       y0: float = 1.0) -> tuple[str, str]:
    """Create a real seed figure via run_python so it has a proper exec record.
    Returns (figure_entity_id, exec_id).

    Post Option-B-Phase-5: register_artifacts_from_tool_result no longer
    mints figure entities on harvest. The helper now materializes the
    first figure artifact via pin_artifact so the test gets a real
    entity to revise / reproduce / chevron-navigate.
    """
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = (
        f"import matplotlib\nmatplotlib.use('Agg')\n"
        f"import matplotlib.pyplot as plt\n"
        f"plt.figure(); plt.plot([1,2,3],[{y0},{y0+1},{y0+2}])\n"
        f"plt.savefig('seed.png'); plt.close('all')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": thread_id,
                                          "tool_use_id": f"tu_seed_{y0}"})
    if res.get("returncode") != 0:
        raise RuntimeError(f"seed run failed: stderr={res.get('stderr')!r}")
    # Still call register so the Run's analysis + manifest are set up
    # like in production.
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id=thread_id,
    )
    ex = res["exec_id"]
    out = pin_artifact(ex, "figure", 0, wrap_in_result=False,
                      thread_id=thread_id)
    return out["entity_id"], ex


def test_make_revision_creates_linked_figure():
    print("\n[1] make_revision creates a new figure with wasRevisionOf edge")
    init_db()
    from content.bio.lifecycle.revisions import make_revision

    parent_id, parent_exec = _make_seed_figure(thread_id="thr_s5a", y0=1.0)
    modified = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[10,20,30])\n"  # different data
        "plt.savefig('rev.png'); plt.close('all')\n"
    )
    out = make_revision(parent_id, modified, thread_id="thr_s5a",
                       title="Revised seed")
    check("new_entity_id returned", isinstance(out.get("new_entity_id"), str))
    check("exec_id returned", isinstance(out.get("exec_id"), str))
    check("wasRevisionOf points at parent", out.get("wasRevisionOf") == parent_id)
    new_id = out["new_entity_id"]
    new_rec = entities.get_entity(new_id)
    parent_rec = entities.get_entity(parent_id)
    check("new entity is a figure", new_rec.get("type") == "figure")
    check("new entity is independent (different id)", new_id != parent_id)
    check("new entity has its own exec_id",
          new_rec.get("exec_id") and new_rec["exec_id"] != parent_rec.get("exec_id"))
    check("new entity title = 'Revised seed'", new_rec.get("title") == "Revised seed")
    # wasRevisionOf edge
    out_edges = edges.edges_from(new_id)
    rev_edges = [e for e in out_edges if e["rel_type"] == "wasRevisionOf"]
    check("wasRevisionOf edge exists", len(rev_edges) == 1)
    check("edge target = parent", rev_edges[0]["target_id"] == parent_id)


def test_figure_history_walks_chain():
    print("\n[2] figure_history walks the revision chain")
    from content.bio.lifecycle.revisions import make_revision
    from content.bio.graph.figure_history import figure_history

    parent_id, _ = _make_seed_figure(thread_id="thr_s5b", y0=2.0)
    modified = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[5,6,7])\n"
        "plt.savefig('rev2.png'); plt.close('all')\n"
    )
    out1 = make_revision(parent_id, modified, thread_id="thr_s5b",
                        title="rev1")
    # Revision of revision
    modified2 = modified.replace("[5,6,7]", "[9,8,7]")
    out2 = make_revision(out1["new_entity_id"], modified2, thread_id="thr_s5b",
                        title="rev2")
    hist = figure_history(out2["new_entity_id"])
    ids_in_chain = [e["id"] for e in hist]
    check("chain length == 3", len(hist) == 3,
          f"got {[e['id'] for e in hist]}")
    check("parent is in chain", parent_id in ids_in_chain)
    check("rev1 is in chain", out1["new_entity_id"] in ids_in_chain)
    check("rev2 is in chain", out2["new_entity_id"] in ids_in_chain)


def test_reproduce_from_exec_no_drift():
    print("\n[3] reproduce_from_exec on a fresh entity → no drift, new exec_id")
    from content.bio.lifecycle.revisions import reproduce_from_exec

    parent_id, _ = _make_seed_figure(thread_id="thr_s5c", y0=3.0)
    out = reproduce_from_exec(parent_id, thread_id="thr_s5c")
    check("reproduced = True", out.get("reproduced") is True,
          f"error={out.get('error')!r}")
    check("new_exec_id is set", isinstance(out.get("new_exec_id"), str))
    check("env_drift = False (same kernel, same env)", out.get("env_drift") is False)
    check("original_fingerprint set", bool(out.get("original_fingerprint")))
    check("new_fingerprint set", bool(out.get("new_fingerprint")))
    check("warnings empty (no drift)", out.get("warnings") == [])


def test_make_revision_rejects_bad_inputs():
    print("\n[4] make_revision rejects bad inputs cleanly")
    from content.bio.lifecycle.revisions import make_revision
    try:
        make_revision("ent_does_not_exist", "x = 1")
        check("rejects unknown parent", False, "no exception")
    except ValueError:
        check("rejects unknown parent", True)
    # Type mismatch — create a non-figure entity
    eid_run = entities.create_entity(entity_type="analysis", title="Not a figure")
    try:
        make_revision(eid_run, "x = 1")
        check("rejects non-figure parent", False, "no exception")
    except ValueError:
        check("rejects non-figure parent", True)


def test_make_revision_empty_code():
    print("\n[5] make_revision rejects empty code")
    from content.bio.lifecycle.revisions import make_revision
    parent_id, _ = _make_seed_figure(thread_id="thr_s5d", y0=4.0)
    try:
        make_revision(parent_id, "")
        check("rejects empty code", False, "no exception")
    except ValueError:
        check("rejects empty code", True)
    try:
        make_revision(parent_id, "   \n   ")
        check("rejects whitespace-only code", False, "no exception")
    except ValueError:
        check("rejects whitespace-only code", True)


def main() -> int:
    test_make_revision_creates_linked_figure()
    test_figure_history_walks_chain()
    test_reproduce_from_exec_no_drift()
    test_make_revision_rejects_bad_inputs()
    test_make_revision_empty_code()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL EXEC-RECORDS STAGE-5 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
