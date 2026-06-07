"""Option B / Phase 2 tests: materialize + pin-from-artifact.

Covers:
  - materialize_entity_from_artifact happy path (figure, table)
  - Cell kind delegates to lifecycle/cells.create_cell_from_exec
  - 'file' kind rejected (not materializable)
  - Idempotency: re-materializing returns the same entity_id
  - Edges: wasGeneratedBy run when exec has run_id
  - pin_artifact wraps in Result by default; respects wrap_in_result=False
  - was_new flag flips correctly
  - HTTP POST /api/artifacts/{exec_id}/{kind}/{idx}/pin (200, 400)
  - pin_artifact_by_id parses canonical address

Run: .venv/bin/python tests/test_pin_artifact.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_pa_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "pa.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                   # noqa: E402
from core.graph import entities, exec_records, edges     # noqa: E402
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _make_exec(*, produced, run_id=None, thread_id="thr_pa",
               stdout="output line\n", started_at="2026-06-07T13:00:00Z"):
    cwd = Path(_tmp) / f"exec_{started_at[-8:].replace(':', '')}"
    cwd.mkdir(parents=True, exist_ok=True)
    return exec_records.create(
        thread_id=thread_id, run_id=run_id, tool_name="run_python",
        status="ok", code="x = 1", started_at=started_at,
        completed_at=started_at, cwd=cwd,
        payload={"produced": produced, "stdout_tail": stdout, "stderr_tail": ""},
    )


def test_materialize_figure_happy():
    print("\n[1] materialize_entity_from_artifact creates a figure entity")
    init_db()
    from content.bio.lifecycle.artifacts import materialize_entity_from_artifact
    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/u.png",
         "name": "subdir/umap.png"},
    ])
    eid = materialize_entity_from_artifact(ex, "figure", 0,
                                            thread_id="thr_pa")
    rec = entities.get_entity(eid)
    check("entity created", rec is not None)
    if not rec:
        return
    check("type = figure", rec["type"] == "figure")
    check("exec_id matches", rec["exec_id"] == ex)
    check("artifact_kind = figure", rec["artifact_kind"] == "figure")
    check("artifact_idx = 0", rec["artifact_idx"] == 0)
    check("artifact_path = url", rec["artifact_path"] == "/u.png")
    # Title strips the subdir prefix from original_name
    check("title is the bare leaf", rec["title"] == "umap.png",
          f"got {rec['title']!r}")
    check("metadata.original_name preserves the full path",
          (rec.get("metadata") or {}).get("original_name") == "subdir/umap.png")


def test_materialize_table_happy():
    print("\n[2] materialize_entity_from_artifact handles tables")
    from content.bio.lifecycle.artifacts import materialize_entity_from_artifact
    ex = _make_exec(produced=[
        {"kind": "table", "idx": 0, "url": "/t.csv", "name": "de.csv"},
    ])
    eid = materialize_entity_from_artifact(ex, "table", 0)
    rec = entities.get_entity(eid)
    check("entity created as table", rec and rec["type"] == "table")
    if rec:
        check("artifact_path is the CSV url", rec["artifact_path"] == "/t.csv")


def test_materialize_cell_delegates():
    print("\n[3] kind=cell goes through lifecycle/cells")
    from content.bio.lifecycle.artifacts import materialize_entity_from_artifact
    ex = _make_exec(produced=[], stdout="cell output text here")
    eid = materialize_entity_from_artifact(ex, "cell", 0)
    rec = entities.get_entity(eid)
    check("entity is a cell", rec and rec["type"] == "cell")
    if rec:
        md = rec.get("metadata") or {}
        check("preview_text from cells.create_cell_from_exec",
              (md.get("preview_text") or "").startswith("cell output text here"))


def test_materialize_file_rejected():
    print("\n[4] kind=file is rejected (not in _KIND_TO_TYPE)")
    from content.bio.lifecycle.artifacts import materialize_entity_from_artifact
    ex = _make_exec(produced=[
        {"kind": "file", "idx": 0, "url": "/obj.rds", "name": "obj.rds"},
    ])
    try:
        materialize_entity_from_artifact(ex, "file", 0)
        check("ValueError raised", False, "no exception")
    except ValueError as e:
        check("ValueError raised", True, f"got {str(e)[:60]}")


def test_materialize_idempotent():
    print("\n[5] materialize twice returns the same entity_id")
    from content.bio.lifecycle.artifacts import materialize_entity_from_artifact
    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/idem.png", "name": "idem.png"},
    ])
    e1 = materialize_entity_from_artifact(ex, "figure", 0)
    e2 = materialize_entity_from_artifact(ex, "figure", 0)
    check("idempotent (same entity_id)", e1 == e2, f"{e1!r} vs {e2!r}")
    # Only one entity row with this exec_id + artifact_kind + artifact_idx
    from core.graph._schema import _conn
    with _conn() as c:
        n = c.execute(
            "SELECT COUNT(*) AS n FROM entities WHERE exec_id=? AND artifact_kind=? AND artifact_idx=?",
            (ex, "figure", 0)
        ).fetchone()["n"]
    check("DB has exactly 1 row for that triple", n == 1, f"got {n}")


def test_materialize_wasGeneratedBy_edge():
    print("\n[6] wasGeneratedBy edge written when exec has run_id")
    from content.bio.lifecycle.artifacts import materialize_entity_from_artifact
    ana = entities.create_entity(
        entity_type="analysis", title="Run A",
        metadata={"thread_id": "thr_pa_e", "run_state": "open", "origin": "internal"},
    )
    ex = _make_exec(run_id=ana, thread_id="thr_pa_e", produced=[
        {"kind": "figure", "idx": 0, "url": "/r.png", "name": "r.png"},
    ])
    eid = materialize_entity_from_artifact(ex, "figure", 0)
    out_edges = edges.edges_from(eid)
    has_was_gen = any(e["target_id"] == ana
                       and e["rel_type"] == "wasGeneratedBy"
                       for e in out_edges)
    check("wasGeneratedBy edge present", has_was_gen)


def test_pin_artifact_wraps_in_result():
    print("\n[7] pin_artifact wraps in Result by default; was_new tracks first-time")
    from content.bio.lifecycle.artifacts import pin_artifact
    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/pin.png", "name": "pin.png"},
    ])
    # First pin → was_new=True, result_id materialized
    out1 = pin_artifact(ex, "figure", 0, title="First pin")
    check("entity_id present", isinstance(out1.get("entity_id"), str))
    check("result_id present", isinstance(out1.get("result_id"), str))
    check("was_new = True on first pin", out1.get("was_new") is True)
    # Repin → was_new=False, same entity_id
    out2 = pin_artifact(ex, "figure", 0)
    check("repin entity_id matches",
          out2.get("entity_id") == out1.get("entity_id"))
    check("was_new = False on repin", out2.get("was_new") is False)
    # The repin path will still create ANOTHER Result wrapper (pin_evidence
    # doesn't dedup on entity). That's intentional — multiple Results
    # can wrap the same figure (different threads, different curation).
    check("repin produced a result_id again",
          isinstance(out2.get("result_id"), str))


def test_pin_artifact_no_wrap():
    print("\n[8] pin_artifact wrap_in_result=False skips Result creation")
    from content.bio.lifecycle.artifacts import pin_artifact
    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/nw.png", "name": "nw.png"},
    ])
    out = pin_artifact(ex, "figure", 0, wrap_in_result=False)
    check("entity_id present", isinstance(out.get("entity_id"), str))
    check("result_id None", out.get("result_id") is None)


def test_pin_artifact_by_id():
    print("\n[9] pin_artifact_by_id parses the canonical address")
    from content.bio.lifecycle.artifacts import pin_artifact_by_id
    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/byid.png", "name": "byid.png"},
    ])
    out = pin_artifact_by_id(f"{ex}:figure:0", wrap_in_result=False)
    check("works end-to-end via id", isinstance(out.get("entity_id"), str))


def test_http_pin_artifact():
    print("\n[10] POST /api/artifacts/{exec_id}/{kind}/{idx}/pin")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)

    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/http.png", "name": "http.png"},
    ])
    r = client.post(f"/api/artifacts/{ex}/figure/0/pin",
                    json={"title": "HTTP pin"})
    check("200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        return
    body = r.json()
    check("entity_id present", isinstance(body.get("entity_id"), str))
    check("entity carrier dict", isinstance(body.get("entity"), dict))
    check("entity.title = HTTP pin",
          (body.get("entity") or {}).get("title") == "HTTP pin")
    check("was_new = True", body.get("was_new") is True)
    # Re-pin → was_new flips
    r2 = client.post(f"/api/artifacts/{ex}/figure/0/pin", json={})
    check("re-pin 200", r2.status_code == 200)
    if r2.status_code == 200:
        check("was_new = False on repin",
              r2.json().get("was_new") is False)
    # Unknown artifact
    r3 = client.post(f"/api/artifacts/{ex}/figure/99/pin", json={})
    check("400 for out-of-range idx", r3.status_code == 400)


def main() -> int:
    test_materialize_figure_happy()
    test_materialize_table_happy()
    test_materialize_cell_delegates()
    test_materialize_file_rejected()
    test_materialize_idempotent()
    test_materialize_wasGeneratedBy_edge()
    test_pin_artifact_wraps_in_result()
    test_pin_artifact_no_wrap()
    test_pin_artifact_by_id()
    test_http_pin_artifact()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL PIN-ARTIFACT-PHASE-2 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
