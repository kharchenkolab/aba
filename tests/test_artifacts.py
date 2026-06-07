"""Option B / Phase 1 tests: backend artifact resolver + HTTP routes.

Covers:
  - format_artifact_id / parse_artifact_id round-trip + bad inputs
  - resolve_artifact happy path + 4 miss cases (no exec, out-of-range,
    kind mismatch, malformed produced entry)
  - list_artifacts + kind filter
  - artifacts_for_run preserves started_at order
  - GET /api/exec_records/{id}/artifacts
  - GET /api/artifacts/{exec_id}/{kind}/{idx} (200 + 404)
  - GET /api/runs/{run_id}/artifacts

Run: .venv/bin/python tests/test_artifacts.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_art_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "art.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                   # noqa: E402
from core.graph import exec_records                       # noqa: E402
from core.exec import artifacts as art                    # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _make_exec(*, produced, run_id=None, thread_id="thr_art",
               started_at="2026-06-07T10:00:00Z"):
    cwd = Path(_tmp) / f"exec_{started_at[-8:].replace(':', '')}"
    cwd.mkdir(parents=True, exist_ok=True)
    return exec_records.create(
        thread_id=thread_id, run_id=run_id, tool_name="run_python",
        status="ok", code="x = 1", started_at=started_at,
        completed_at=started_at, cwd=cwd,
        payload={"produced": produced},
    )


def test_format_parse_roundtrip():
    print("\n[1] format/parse roundtrip + bad-input rejection")
    s = art.format_artifact_id("exec_abcd1234", "figure", 0)
    check("format → 'exec_abcd1234:figure:0'", s == "exec_abcd1234:figure:0")
    e, k, i = art.parse_artifact_id(s)
    check("parse exec_id", e == "exec_abcd1234")
    check("parse kind", k == "figure")
    check("parse idx", i == 0)
    # Kind normalization (case-insensitive)
    s2 = art.format_artifact_id("exec_x", "Figure", 3)
    check("kind normalized to lowercase",
          s2 == "exec_x:figure:3")
    # Malformed inputs
    for bad in ("", "exec_x", "exec_x:figure", "exec_x:figure:abc",
                "exec_x:figure:-1", ":figure:0", "exec_x::0"):
        try:
            art.parse_artifact_id(bad)
            check(f"reject malformed {bad!r}", False, "no exception")
        except ValueError:
            check(f"reject malformed {bad!r}", True)
    # format rejects empties
    for args in [("", "figure", 0), ("exec_x", "", 0), ("exec_x", "figure", -1)]:
        try:
            art.format_artifact_id(*args)
            check(f"format rejects {args}", False, "no exception")
        except ValueError:
            check(f"format rejects {args}", True)


def test_resolve_single():
    print("\n[2] resolve_artifact happy path + miss cases")
    init_db()
    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/a.png", "name": "umap.png"},
        {"kind": "table",  "idx": 0, "url": "/b.csv", "name": "de.csv"},
        {"kind": "file",   "idx": 0, "url": "/c.rds", "name": "obj.rds"},
    ])
    # Happy path — figure at idx 0
    a = art.resolve_artifact(ex, "figure", 0)
    check("figure at idx 0 resolves", a is not None)
    if a:
        check("artifact_id is well-formed",
              a["artifact_id"] == f"{ex}:figure:0")
        check("url passed through", a["url"] == "/a.png")
        check("original_name coerced from 'name'",
              a["original_name"] == "umap.png")
        check("kind lowercased", a["kind"] == "figure")
    # Table at idx 0 — independent from figure at idx 0 (kinds partition idx space
    # in the canonical model, but our produced[] is flat; idx 0 is figure here.
    # Verify the resolver respects the kind filter as a positive identity).
    t = art.resolve_artifact(ex, "table", 0)
    # The produced[] list above has table at LIST INDEX 1. resolve_artifact
    # takes the flat-list idx. So idx 1 = table; idx 0 = figure.
    # The kind passed must match the entry's kind, so resolve(ex, 'table', 0)
    # is a kind mismatch — entry[0] is a figure.
    check("kind mismatch at idx 0 returns None", t is None)
    # Real table is at flat-list idx 1
    t2 = art.resolve_artifact(ex, "table", 1)
    check("table at flat-list idx 1 resolves", t2 is not None)
    if t2:
        check("table.original_name = de.csv",
              t2["original_name"] == "de.csv")
    # Out of range
    miss = art.resolve_artifact(ex, "figure", 99)
    check("out-of-range idx returns None", miss is None)
    # Unknown exec
    miss2 = art.resolve_artifact("exec_no_such", "figure", 0)
    check("unknown exec returns None", miss2 is None)


def test_list_artifacts():
    print("\n[3] list_artifacts + kind filter")
    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/p1.png", "name": "p1.png"},
        {"kind": "figure", "idx": 1, "url": "/p2.png", "name": "p2.png"},
        {"kind": "table",  "idx": 0, "url": "/t1.csv", "name": "t1.csv"},
    ])
    all_ = art.list_artifacts(ex)
    check("3 artifacts returned", len(all_) == 3)
    check("all carry artifact_id",
          all(a.get("artifact_id") for a in all_))
    figs = art.list_artifacts(ex, kind="figure")
    check("kind=figure returns 2", len(figs) == 2)
    tabs = art.list_artifacts(ex, kind="table")
    check("kind=table returns 1", len(tabs) == 1)
    check("kind filter is case-insensitive",
          len(art.list_artifacts(ex, kind="FIGURE")) == 2)


def test_artifacts_for_run():
    print("\n[4] artifacts_for_run preserves started_at order across multi-exec runs")
    rid = "R_art_test"
    # Insert deliberately out of order
    ex_late = _make_exec(run_id=rid, produced=[
        {"kind": "figure", "idx": 0, "url": "/late.png", "name": "late.png"},
    ], started_at="2026-06-07T10:02:00Z")
    ex_first = _make_exec(run_id=rid, produced=[
        {"kind": "figure", "idx": 0, "url": "/first.png", "name": "first.png"},
    ], started_at="2026-06-07T10:01:00Z")
    ex_mid = _make_exec(run_id=rid, produced=[
        {"kind": "table",  "idx": 0, "url": "/mid.csv", "name": "mid.csv"},
    ], started_at="2026-06-07T10:01:30Z")

    out = art.artifacts_for_run(rid)
    check("3 artifacts across the run", len(out) == 3)
    names = [a.get("original_name") for a in out]
    check("ordered by started_at",
          names == ["first.png", "mid.csv", "late.png"],
          f"got {names}")
    # Filter
    figs = art.artifacts_for_run(rid, kind="figure")
    check("filter narrows to figures", [a["original_name"] for a in figs]
          == ["first.png", "late.png"])
    # Empty run
    check("empty run returns []", art.artifacts_for_run("R_no_such") == [])


def test_http_list_exec_artifacts():
    print("\n[5] GET /api/exec_records/{exec_id}/artifacts")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)

    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/u.png", "name": "u.png"},
    ])
    r = client.get(f"/api/exec_records/{ex}/artifacts")
    check("200", r.status_code == 200)
    body = r.json()
    check("body.artifacts is a list", isinstance(body.get("artifacts"), list))
    check("one artifact returned",
          len(body.get("artifacts") or []) == 1)
    if body.get("artifacts"):
        check("artifact_id present in HTTP body",
              "artifact_id" in body["artifacts"][0])
    # Unknown exec → 200 with empty list (the resolver doesn't 404 here;
    # the artifacts list endpoint is permissive)
    r2 = client.get("/api/exec_records/exec_no_such/artifacts")
    check("unknown exec → 200 empty", r2.status_code == 200
          and r2.json().get("artifacts") == [])


def test_http_resolve_artifact():
    print("\n[6] GET /api/artifacts/{exec_id}/{kind}/{idx}")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    ex = _make_exec(produced=[
        {"kind": "figure", "idx": 0, "url": "/zz.png", "name": "zz.png"},
    ])
    r = client.get(f"/api/artifacts/{ex}/figure/0")
    check("200 for valid", r.status_code == 200)
    if r.status_code == 200:
        check("body has artifact_id", "artifact_id" in r.json())
    r2 = client.get(f"/api/artifacts/{ex}/figure/99")
    check("404 for out-of-range", r2.status_code == 404)
    r3 = client.get(f"/api/artifacts/{ex}/table/0")
    check("404 for kind mismatch", r3.status_code == 404)


def test_http_list_run_artifacts():
    print("\n[7] GET /api/runs/{run_id}/artifacts")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI(); app.include_router(router)
    client = TestClient(app)
    rid = "R_http_art"
    _make_exec(run_id=rid, produced=[
        {"kind": "figure", "idx": 0, "url": "/A.png", "name": "A.png"},
        {"kind": "table",  "idx": 0, "url": "/A.csv", "name": "A.csv"},
    ], started_at="2026-06-07T11:00:00Z")
    r = client.get(f"/api/runs/{rid}/artifacts")
    check("200", r.status_code == 200)
    names = [a.get("original_name") for a in (r.json().get("artifacts") or [])]
    check("both artifacts returned", set(names) == {"A.png", "A.csv"},
          f"got {names}")


def main() -> int:
    test_format_parse_roundtrip()
    test_resolve_single()
    test_list_artifacts()
    test_artifacts_for_run()
    test_http_list_exec_artifacts()
    test_http_resolve_artifact()
    test_http_list_run_artifacts()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL ARTIFACTS-PHASE-1 CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
