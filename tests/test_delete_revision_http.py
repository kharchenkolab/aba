"""HTTP integration test for POST /api/entities/{id}/delete-revision.

Mounts the bio router on a minimal FastAPI app and exercises the new
route via TestClient. The underlying delete_revision() helper is
already covered by tests/test_delete_revision.py — this just confirms
the HTTP wiring + error paths.

Run: .venv/bin/python tests/test_delete_revision_http.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_del_rev_http_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "drh.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()
from core.graph.entities import create_entity, get_entity  # noqa: E402
from core.graph.edges import add_edge  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def _mount_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _figure(title, art) -> str:
    p = os.path.join(_tmp, art); open(p, "w").write("x")
    return create_entity(entity_type="figure", title=title,
                         artifact_path=p,
                         metadata={"thread_id": "default"})


def main() -> int:
    client = _mount_app()

    # Two-entry chain v1 ← v2; member.ref = v1.
    v1 = _figure("v1", "v1.png")
    v2 = _figure("v2", "v2.png")
    add_edge(source_id=v2, target_id=v1, rel_type="wasRevisionOf")
    rid = create_entity(entity_type="result", title="R",
                        metadata={"thread_id": "default",
                                  "members": [{"id": "m1", "kind": "figure", "ref": v1}]})
    add_edge(source_id=rid, target_id=v1, rel_type="includes")

    # Delete head (v2): 200 ok, chain shrinks
    r = client.post(f"/api/entities/{v2}/delete-revision")
    check("delete head 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    body = r.json() if r.status_code == 200 else {}
    check("body.deleted matches", body.get("deleted") == v2, str(body))
    check("v2 actually gone", get_entity(v2) is None)

    # Now chain is just v1. Deleting v1 should 400 (only active version).
    r = client.post(f"/api/entities/{v1}/delete-revision")
    check("only-active refused with 400",
          r.status_code == 400, f"{r.status_code} {r.text[:200]}")
    check("error mentions 'Remove from Result'",
          "Remove from Result" in r.text, r.text[:200])

    # Unknown entity → 404
    r = client.post("/api/entities/fig_nope/delete-revision")
    check("unknown id → 404", r.status_code == 404, f"{r.status_code}")

    # Wrong type (Result) → 400
    r = client.post(f"/api/entities/{rid}/delete-revision")
    check("non-figure type → 400",
          r.status_code == 400 and "figure/table" in r.text, r.text[:200])

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL DELETE-REVISION-HTTP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
