"""resolve_reference: stage a reference for a run + pin the run-lock
(refs.md §9). The pin is a schema-legal `run --used--> reference` edge plus the
content-sha version lock. Synthetic (no kernel).

Run:  .venv/bin/python tests/test_refs_resolve.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_refresolve_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "rr.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ABA_REFS_DIR"] = str(Path(_tmp) / "refs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                       # noqa: E402
from core.graph.edges import edges_from                      # noqa: E402
import content.bio  # noqa: E402,F401
from content.bio.tools import register_reference_tool, resolve_reference_tool  # noqa: E402
from content.bio.lifecycle.runs import open_run, active_run_id  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def _used_targets(run_id: str) -> set:
    return {e["target_id"] for e in edges_from(run_id) if e["rel_type"] == "used"}


def main() -> int:
    init_db()
    fa = Path(_tmp) / "ref.fa"
    fa.write_text(">c\nACGTACGT\n")
    reg = register_reference_tool({"path": str(fa), "organism": "fly", "role": "genome",
                                   "assembly": "BDGP6"})
    ref_id, sha = reg["reference_id"], reg["sha"]

    print("resolve with an open run → pins run-lock")
    tid = "thr_resolve"
    open_run(tid, "resolve test")
    run_id = active_run_id(tid)
    check("a run is open", bool(run_id), str(run_id))
    res = resolve_reference_tool({"reference_id": ref_id}, ctx={"thread_id": tid})
    check("resolve → ok + local_path + version lock = sha",
          res.get("status") == "ok" and res.get("local_path") and res.get("version_lock") == sha,
          str(res)[:160])
    check("run_id reported", res.get("run_id") == run_id)
    check("run-lock pinned: run --used--> reference", ref_id in _used_targets(run_id),
          str(_used_targets(run_id)))

    print("resolve by facets (no id) → same reference")
    res2 = resolve_reference_tool({"organism": "fly", "role": "genome"}, ctx={"thread_id": tid})
    check("facet resolve finds the reference", res2.get("reference_id") == ref_id, str(res2)[:120])

    print("resolve with NO open run → path returned, not pinned")
    res3 = resolve_reference_tool({"reference_id": ref_id}, ctx={"thread_id": "thr_norun"})
    check("no-run resolve still returns the path", res3.get("status") == "ok" and res3.get("local_path"))
    check("no-run resolve has run_id=None", res3.get("run_id") is None, str(res3.get("run_id")))

    print("resolve a non-existent reference → clean error")
    res4 = resolve_reference_tool({"reference_id": "ref_does_not_exist"})
    check("unknown reference → error (no crash)", "error" in res4, str(res4))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL REFS-RESOLVE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
