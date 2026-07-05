"""Stage 6 / Phase B tests: harvest_table() kernel helper (Python).

Spins up the run_python kernel, calls `harvest_table(df, name='...')`, and
verifies the resulting CSV is picked up by the harvester and surfaces as
a table artifact + table entity (via register_artifacts_from_tool_result).

R is covered by a thinner test that just checks the helper is defined in
the R namespace (we don't spin a full Bioconductor kernel for a simple
write.csv check — that would gate the test on bioc env state).

Run: .venv/bin/python tests/test_harvest_helpers.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_harvest_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "harvest.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                          # noqa: E402
from core.graph import entities                                  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_python_helper_pandas():
    print("\n[1] harvest_table(pandas.DataFrame) creates a CSV picked up by the harvester")
    init_db()
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result

    code = (
        "import pandas as pd\n"
        "df = pd.DataFrame({'gene': ['G1','G2','G3'], 'lfc': [1.2, -0.4, 2.1]})\n"
        "p = harvest_table(df, name='de_results')\n"
        "print('saved to', p)\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": "thr_harv_a",
                                          "tool_use_id": "tu_harv_a"})
    check("run_python ok", res.get("returncode") == 0,
          f"stderr={res.get('stderr')!r}")
    if res.get("returncode") != 0:
        return
    check("stdout shows the harvest line",
          "[harvest_table] wrote de_results.csv" in (res.get("stdout") or ""),
          f"stdout: {(res.get('stdout') or '')[:200]!r}")
    tables = res.get("tables") or []
    check("tables has at least one entry", len(tables) >= 1,
          f"tables: {tables}")
    if tables:
        names = [t.get("original_name", "") for t in tables]
        check("'de_results.csv' appears in harvested tables",
              any("de_results.csv" in n for n in names),
              f"got names: {names}")

    # Post Option-B-Phase-5: registry no longer mints entities on harvest;
    # the table is reachable as an artifact via the exec record. Pin it
    # explicitly to materialize the table entity.
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id="thr_harv_a",
    )
    from content.bio.lifecycle.artifacts import pin_artifact
    # `harvest_table(df, name='de_results')` writes de_results.csv, which
    # appears in result.tables at idx 0 (the only table this cell produced).
    pinned = pin_artifact(res["exec_id"], "table", 0,
                          wrap_in_result=False, thread_id="thr_harv_a")
    t = entities.get_entity(pinned["entity_id"])
    check("pin_artifact materialized a table entity", t is not None)
    if t:
        check("table entity has exec_id", bool(t.get("exec_id")))
        check("table.artifact_kind = table", t.get("artifact_kind") == "table")


def test_python_helper_auto_name():
    print("\n[2] harvest_table(df) with default name still works (auto-unique)")
    from content.bio.tools.run_exec import run_python
    code = (
        "import pandas as pd\n"
        "df = pd.DataFrame({'a': [1,2], 'b': [3,4]})\n"
        "harvest_table(df)\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": "thr_harv_b",
                                          "tool_use_id": "tu_harv_b"})
    check("run_python ok", res.get("returncode") == 0)
    tables = res.get("tables") or []
    check("auto-named table harvested", len(tables) >= 1)
    if tables:
        check("auto-name starts with 'table_'",
              any("table_" in (t.get("original_name") or "") for t in tables))


def test_python_helper_dict_fallback():
    print("\n[3] harvest_table(dict) falls back to csv module")
    from content.bio.tools.run_exec import run_python
    code = (
        "harvest_table({'k1': 'v1', 'k2': 'v2'}, name='kvs')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": "thr_harv_c",
                                          "tool_use_id": "tu_harv_c"})
    check("run_python ok", res.get("returncode") == 0,
          f"stderr={res.get('stderr')!r}")
    tables = res.get("tables") or []
    check("kvs.csv harvested",
          any("kvs.csv" in (t.get("original_name") or "") for t in tables))


def test_python_helper_extension_normalization():
    print("\n[4] harvest_table normalizes filename extension")
    from content.bio.tools.run_exec import run_python
    code = (
        "import pandas as pd\n"
        "df = pd.DataFrame({'x':[1]})\n"
        "harvest_table(df, name='no_ext_here')\n"     # no .csv suffix
    )
    res = run_python({"code": code}, ctx={"thread_id": "thr_harv_d",
                                          "tool_use_id": "tu_harv_d"})
    tables = res.get("tables") or []
    check("filename normalized to .csv",
          any("no_ext_here.csv" in (t.get("original_name") or "") for t in tables),
          f"got names: {[t.get('original_name') for t in tables]}")


def main() -> int:
    test_python_helper_pandas()
    test_python_helper_auto_name()
    test_python_helper_dict_fallback()
    test_python_helper_extension_normalization()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL HARVEST-HELPER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
