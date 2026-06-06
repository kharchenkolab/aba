"""Stage 6 / Phase A tests: cell entity + create_cell_from_exec + HTTP pin endpoint.

Covers:
  - cell entity_type loads from YAML (registry recognizes it)
  - create_cell_from_exec rejects unknown exec, empty-output exec
  - create_cell_from_exec builds a cell entity pointing at the exec
  - title derivation: explicit override > first stdout line > tool fallback
  - preview_text capped at 500 chars
  - HTTP POST /api/exec_records/{exec_id}/pin_cell — happy path + 400/404
  - pin_cell_from_exec wraps the cell in a Result (pin_evidence path)

Run: .venv/bin/python tests/test_cell_entity.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_cell_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "cell.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                  # noqa: E402
from core.graph import entities, exec_records           # noqa: E402
# Importing content.bio runs its __init__.py which calls
# load_types(entity_types/) — without this the cell.yaml we just added
# isn't visible to get_type / check_create_fields. The HTTP and
# lifecycle tests below already touch content.bio.*, so this explicit
# import only matters for the schema-loaded check below.
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _make_exec(*, run_id=None, thread_id="thr_cell", code="x = 1",
               stdout="hello\nworld", produced=None, tool_name="run_python"):
    cwd = Path(_tmp) / f"cell_{tool_name}_{stdout[:8] if stdout else 'na'}"
    cwd.mkdir(parents=True, exist_ok=True)
    return exec_records.create(
        thread_id=thread_id, run_id=run_id, tool_name=tool_name,
        status="ok", code=code, started_at="2026-06-06T13:00:00Z",
        completed_at="2026-06-06T13:00:02Z", cwd=cwd,
        payload={
            "executor": "kernel:python", "language": "python",
            "stdout_tail": stdout, "stderr_tail": "",
            "exit_code": 0, "wall_time_s": 2.0,
            "produced": produced or [],
        },
    )


def test_cell_yaml_loads():
    print("\n[1] cell.yaml is discoverable + schema is honored")
    init_db()
    from core.entity_types import check_create_fields, get_type
    t = get_type("cell")
    check("entity_types.get_type('cell') returns a type", t is not None)
    # required field is `title`
    warns = check_create_fields("cell", {"title": "ok"})
    check("create_cell with title only → no warnings",
          warns == [], f"got {warns}")
    warns_bad = check_create_fields("cell", {"title": ""})
    check("create_cell with empty title → warns",
          len(warns_bad) >= 1)


def test_create_cell_unknown_exec():
    print("\n[2] create_cell_from_exec rejects unknown exec_id")
    from content.bio.lifecycle.cells import create_cell_from_exec
    try:
        create_cell_from_exec("exec_does_not_exist")
        check("ValueError raised", False, "no exception")
    except ValueError:
        check("ValueError raised on unknown exec", True)


def test_create_cell_empty_output():
    print("\n[3] create_cell_from_exec refuses to pin a blank exec")
    ex = _make_exec(stdout="")
    from content.bio.lifecycle.cells import create_cell_from_exec
    try:
        create_cell_from_exec(ex)
        check("ValueError on blank exec", False, "no exception")
    except ValueError as e:
        check("ValueError on blank exec", True, f"got {str(e)[:60]!r}")


def test_create_cell_happy_path():
    print("\n[4] create_cell_from_exec creates a cell entity")
    ex = _make_exec(stdout="answer is 42\nmore output here")
    from content.bio.lifecycle.cells import create_cell_from_exec
    cell_id = create_cell_from_exec(ex, thread_id="thr_cell")
    check("returned an id", isinstance(cell_id, str))
    rec = entities.get_entity(cell_id)
    check("entity exists", rec is not None)
    if not rec:
        return
    check("type = cell", rec.get("type") == "cell")
    check("exec_id matches", rec.get("exec_id") == ex)
    check("artifact_kind = cell", rec.get("artifact_kind") == "cell")
    check("artifact_idx = 0", rec.get("artifact_idx") == 0)
    md = rec.get("metadata") or {}
    check("preview_text starts with first stdout line",
          (md.get("preview_text") or "").startswith("answer is 42"),
          f"got {(md.get('preview_text') or '')[:50]!r}")
    check("title derived from first stdout line",
          rec.get("title") == "answer is 42",
          f"got {rec.get('title')!r}")


def test_title_derivation():
    print("\n[5] title derivation: override > first-line > fallback")
    # Override wins
    ex1 = _make_exec(stdout="ignored line")
    from content.bio.lifecycle.cells import create_cell_from_exec
    c1 = create_cell_from_exec(ex1, title="Custom title")
    check("explicit title wins",
          entities.get_entity(c1).get("title") == "Custom title")
    # No stdout → fallback
    ex2 = exec_records.create(
        thread_id="thr_fb", run_id=None, tool_name="run_python",
        status="ok", code="", started_at="2026-06-06T13:01:00Z",
        completed_at="2026-06-06T13:01:00Z",
        cwd=Path(_tmp) / "fb",
        payload={"stdout_tail": "", "stderr_tail": "",
                 "produced": [{"kind": "figure", "idx": 0, "url": "/foo.png"}]},
    )
    Path(_tmp, "fb").mkdir(exist_ok=True)
    c2 = create_cell_from_exec(ex2)
    check("no-stdout cell falls back to 'Output of <tool_name>'",
          entities.get_entity(c2).get("title") == "Output of run_python")


def test_preview_text_capped():
    print("\n[6] preview_text capped at 500 chars")
    long_out = "x" * 2000
    ex = _make_exec(stdout=long_out)
    from content.bio.lifecycle.cells import create_cell_from_exec
    cid = create_cell_from_exec(ex)
    md = entities.get_entity(cid).get("metadata") or {}
    preview = md.get("preview_text") or ""
    check("preview length = 500", len(preview) == 500,
          f"got {len(preview)}")


def test_wasGeneratedBy_edge_for_run_attributed_exec():
    print("\n[7] wasGeneratedBy edge written when exec has a run_id")
    # Need a real analysis (run) entity for the edge target
    ana = entities.create_entity(
        entity_type="analysis", title="Test run",
        metadata={"thread_id": "thr_e", "run_state": "open", "origin": "internal"},
    )
    ex = _make_exec(run_id=ana, thread_id="thr_e",
                    stdout="produced by ana")
    from content.bio.lifecycle.cells import create_cell_from_exec
    from core.graph.edges import edges_from
    cid = create_cell_from_exec(ex, thread_id="thr_e")
    out = edges_from(cid)
    has_was_gen = any(e["target_id"] == ana
                       and e["rel_type"] == "wasGeneratedBy"
                       for e in out)
    check("cell --wasGeneratedBy--> analysis edge present", has_was_gen,
          f"edges: {[(e['target_id'], e['rel_type']) for e in out]}")


def test_http_pin_cell_happy():
    print("\n[8] POST /api/exec_records/{exec_id}/pin_cell")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)

    ex = _make_exec(stdout="HTTP pin output line 1")
    # Default: wrap_in_result=True → cell + result created
    r = client.post(f"/api/exec_records/{ex}/pin_cell", json={"title": "HTTP cell"})
    check("200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        return
    body = r.json()
    check("body.cell is a dict", isinstance(body.get("cell"), dict))
    check("cell has exec_id", (body.get("cell") or {}).get("exec_id") == ex)
    check("cell.title = HTTP cell", (body.get("cell") or {}).get("title") == "HTTP cell")
    check("result_id is set (wrap_in_result default)",
          isinstance(body.get("result_id"), str))

    # wrap_in_result=False
    ex2 = _make_exec(stdout="bare cell only")
    r2 = client.post(f"/api/exec_records/{ex2}/pin_cell",
                     json={"wrap_in_result": False})
    check("bare 200", r2.status_code == 200)
    if r2.status_code == 200:
        check("result_id is None", r2.json().get("result_id") is None)


def test_http_pin_cell_errors():
    print("\n[9] /pin_cell error paths")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)

    # Unknown exec
    r = client.post("/api/exec_records/exec_no_such/pin_cell", json={})
    check("400 on unknown exec", r.status_code == 400,
          f"got {r.status_code}")

    # Blank-output exec
    ex_blank = _make_exec(stdout="")
    r2 = client.post(f"/api/exec_records/{ex_blank}/pin_cell", json={})
    check("400 on blank exec", r2.status_code == 400)


def main() -> int:
    test_cell_yaml_loads()
    test_create_cell_unknown_exec()
    test_create_cell_empty_output()
    test_create_cell_happy_path()
    test_title_derivation()
    test_preview_text_capped()
    test_wasGeneratedBy_edge_for_run_attributed_exec()
    test_http_pin_cell_happy()
    test_http_pin_cell_errors()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL CELL-ENTITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
