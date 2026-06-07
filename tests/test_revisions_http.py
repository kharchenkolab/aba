"""A1 tests: HTTP endpoints for revisions / make_revision / reproduce.

Mounts the bio router on a minimal FastAPI app and exercises the three
new routes via TestClient. The underlying Python functions are already
tested (Stage 5); this just confirms the HTTP wiring.

Run: .venv/bin/python tests/test_revisions_http.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_rev_http_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "rh.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"] = "/workspace/aba-runtime/envs"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                  # noqa: E402
from core.graph import entities, exec_records, edges    # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _mount_app():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from content.bio.web.routes import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _make_seed_figure(thread_id: str = "thr_http"):
    """Create a real seed figure via the dispatch path so it has an exec
    record + entity row + everything wired up.

    Post Option-B-Phase-5: registry no longer mints entities on harvest;
    we explicitly materialize via pin_artifact for the test fixture.
    """
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[4,5,6])\n"
        "plt.savefig('http_seed.png'); plt.close('all')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": thread_id,
                                          "tool_use_id": "tu_http_seed"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id=thread_id,
    )
    ex = res.get("exec_id")
    if not ex:
        return None
    out = pin_artifact(ex, "figure", 0, wrap_in_result=False,
                      thread_id=thread_id)
    return out["entity_id"]


def test_revisions_unknown_id_404():
    print("\n[1] GET /revisions on unknown id → 404")
    init_db()
    client = _mount_app()
    r = client.get("/api/entities/ent_does_not_exist/revisions")
    check("404 for unknown entity", r.status_code == 404)


def test_revisions_single_entity():
    print("\n[2] GET /revisions on a fresh figure → chain of 1")
    client = _mount_app()
    fig_id = _make_seed_figure(thread_id="thr_http_a")
    check("seed figure created", bool(fig_id))
    if not fig_id:
        return
    r = client.get(f"/api/entities/{fig_id}/revisions")
    check("200", r.status_code == 200)
    body = r.json()
    check("chain has 1 entry", len(body.get("chain", [])) == 1)
    check("position = 0", body.get("position") == 0)
    check("prev is None", body.get("prev") is None)
    check("next is None", body.get("next") is None)


def test_make_revision_then_revisions():
    print("\n[3] POST /make_revision → GET /revisions returns chain of 2")
    client = _mount_app()
    fig_id = _make_seed_figure(thread_id="thr_http_b")
    modified = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[10,20,30])\n"
        "plt.savefig('http_rev.png'); plt.close('all')\n"
    )
    r = client.post(f"/api/entities/{fig_id}/make_revision",
                    json={"modified_code": modified, "title": "HTTP revision"})
    check("make_revision 200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        return
    body = r.json()
    new_ent = body.get("entity")
    check("entity returned", isinstance(new_ent, dict) and bool(new_ent.get("id")))
    check("entity is a figure", (new_ent or {}).get("type") == "figure")
    check("wasRevisionOf points at parent",
          body.get("wasRevisionOf") == fig_id)
    new_id = new_ent.get("id") if new_ent else None
    if not new_id:
        return
    # Now /revisions on the new id should return chain of 2
    r2 = client.get(f"/api/entities/{new_id}/revisions")
    check("/revisions 200", r2.status_code == 200)
    body2 = r2.json()
    check("chain length = 2", len(body2.get("chain", [])) == 2)
    check("new id is at position 0 (newest)",
          body2.get("position") == 0)
    check("prev = original", body2.get("prev") == fig_id)


def test_make_revision_400_on_bad_inputs():
    print("\n[4] /make_revision returns 400 on empty code")
    client = _mount_app()
    fig_id = _make_seed_figure(thread_id="thr_http_c")
    r = client.post(f"/api/entities/{fig_id}/make_revision",
                    json={"modified_code": ""})
    check("400 on empty code", r.status_code == 400, f"got {r.status_code}")
    # Unknown entity
    r2 = client.post("/api/entities/ent_no_such/make_revision",
                     json={"modified_code": "x = 1"})
    check("404 on unknown entity", r2.status_code == 404)
    # Wrong type — analysis can't be revised
    ana = entities.create_entity(entity_type="analysis", title="Not a figure")
    r3 = client.post(f"/api/entities/{ana}/make_revision",
                     json={"modified_code": "x = 1"})
    check("400 on non-figure parent", r3.status_code == 400)


def test_reproduce():
    print("\n[5] POST /reproduce returns reproduction summary")
    client = _mount_app()
    fig_id = _make_seed_figure(thread_id="thr_http_d")
    r = client.post(f"/api/entities/{fig_id}/reproduce")
    check("200", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
    if r.status_code != 200:
        return
    body = r.json()
    check("reproduced = True", body.get("reproduced") is True)
    check("new_exec_id present", isinstance(body.get("new_exec_id"), str))
    check("env_drift is False (same kernel)", body.get("env_drift") is False)
    check("warnings is empty list", body.get("warnings") == [])
    # 404 on unknown
    r2 = client.post("/api/entities/ent_no_such/reproduce")
    check("404 on unknown entity", r2.status_code == 404)


def main() -> int:
    test_revisions_unknown_id_404()
    test_revisions_single_entity()
    test_make_revision_then_revisions()
    test_make_revision_400_on_bad_inputs()
    test_reproduce()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL REVISIONS-HTTP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
