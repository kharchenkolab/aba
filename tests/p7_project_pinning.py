"""Phase B (misc/modularity_audit.md): project-context pinning contract.

Two invariants:

1. The middleware atomically pins ?project_id= or X-Project-Id on every
   request. Two tabs on different projects can no longer race on the
   process-global.

2. require_project (Depends) enforces 412 when no pid is supplied AND the
   global is None. Applied to entity reads/writes specifically called out
   in the audit (entities_get, entities_patch, entities_delete).

Deterministic. Runs against an isolated temp DB. No live project.

Run:
    .venv/bin/python tests/p7_project_pinning.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="aba_pin_")
from core.graph import _schema  # noqa: E402
_schema.set_db_path(os.path.join(_tmp, "test.db"))
_schema.init_db()

from fastapi.testclient import TestClient  # noqa: E402
from core import projects as _projects  # noqa: E402
import main as _main  # noqa: E402

client = TestClient(_main.app)


def test_query_param_pin_is_per_request_no_global_leak():
    """#17: ?project_id=X binds X for the request's duration via a contextvar —
    it must NOT mutate the process-global (that shared global is what raced
    concurrent requests / corrupted streaming turns). After the request, the
    global is unchanged."""
    _projects.set_current("alpha")
    r = client.get("/api/entities/workspace?project_id=beta")
    assert r.status_code in (200, 404), r.text          # accepted (not 412)
    assert _projects.current() == "alpha", \
        f"pin leaked to the process-global: current()={_projects.current()!r}"


def test_header_pin_is_per_request_no_global_leak():
    """X-Project-Id: X also pins per-request without leaking to the global."""
    _projects.set_current("alpha")
    r = client.get("/api/entities/workspace", headers={"X-Project-Id": "gamma"})
    assert r.status_code in (200, 404), r.text
    assert _projects.current() == "alpha", \
        f"header pin leaked to the process-global: current()={_projects.current()!r}"


def test_depends_412_on_missing_when_no_global():
    """Entity endpoints with Depends(require_project) must 412 when both
    pid is absent AND the process-global is None."""
    # Force the global to None (the post-bounce / park-on-scratch state).
    _projects.set_current(None)  # type: ignore[arg-type]
    r = client.get("/api/entities/workspace")
    assert r.status_code == 412, f"expected 412, got {r.status_code}: {r.text}"
    r = client.patch("/api/entities/workspace", json={"title": "x"})
    assert r.status_code == 412, f"expected 412, got {r.status_code}: {r.text}"
    r = client.delete("/api/entities/some_eid")
    assert r.status_code == 412, f"expected 412, got {r.status_code}: {r.text}"


def test_depends_passes_when_pid_supplied():
    """The dep returns the supplied pid and allows the handler to proceed."""
    _projects.set_current(None)  # type: ignore[arg-type]
    r = client.get("/api/entities/workspace?project_id=delta")
    # workspace entity doesn't exist in the temp DB -> 404, but NOT 412
    assert r.status_code != 412, f"dep should accept supplied pid: {r.text}"


def test_depends_passes_when_global_set():
    """Backward-compatibility: if pid is missing but the global IS set
    (e.g., via /open), the dep returns the global and the handler runs."""
    _projects.set_current("epsilon")
    r = client.get("/api/entities/workspace")
    assert r.status_code != 412, f"dep should accept global pid: {r.text}"


def main() -> int:
    tests = [
        test_query_param_pin_is_per_request_no_global_leak,
        test_header_pin_is_per_request_no_global_leak,
        test_depends_412_on_missing_when_no_global,
        test_depends_passes_when_pid_supplied,
        test_depends_passes_when_global_set,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{len(failed)} / {len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
