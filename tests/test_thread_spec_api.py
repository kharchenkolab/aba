"""HTTP layer for per-thread spec + the primary-spec catalog endpoint.

Wraps C2 + D1's backend pieces. The frontend can drive these endpoints
to build a "Backend" dropdown without any extra plumbing on this
side.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_thread_api_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "ta.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))


pytestmark = pytest.mark.bio


@pytest.fixture
def client():
    """FastAPI TestClient over the live app."""
    from fastapi.testclient import TestClient
    from main import app
    from core.graph._schema import init_db
    from core import projects
    init_db()
    projects.set_current("prj_thread_api")
    return TestClient(app)


def test_specs_primary_endpoint_lists_both_guides(client):
    r = client.get("/api/specs/primary")
    assert r.status_code == 200, r.text
    body = r.json()
    names = [s["name"] for s in body["specs"]]
    assert "guide"      in names
    assert "lean_guide" in names
    # `default` is the resolved choice (env override or "guide").
    assert body["default"] in names


def test_specs_primary_marks_default(client):
    r = client.get("/api/specs/primary")
    body = r.json()
    flagged = [s for s in body["specs"] if s["is_default"]]
    assert len(flagged) == 1
    assert flagged[0]["name"] == body["default"]


def test_specs_primary_metadata_round_trip(client):
    """The dropdown needs at minimum the model + a way to tell lean
    from full at a glance — that's prompt_mode + tool_count."""
    r = client.get("/api/specs/primary")
    by_name = {s["name"]: s for s in r.json()["specs"]}
    g = by_name["guide"]
    l = by_name["lean_guide"]
    assert g["prompt_mode"] == "full"
    assert l["prompt_mode"] == "lean"
    # Both specs now have full reach ('*' allowlist) → tool_count is None.
    # The lean optimization shrinks the catalog PRESENTATION, not the
    # set of reachable tools. See lean_guide.yaml header (2026-06-20
    # redesign).
    assert g["tool_count"] is None
    assert l["tool_count"] is None
    # Lean has a smaller summary budget than guide's (None → default).
    assert l["summary_budget"] is not None
    assert g["summary_budget"] is None


def test_thread_create_with_spec_pins(client):
    r = client.post("/api/threads", json={
        "title": "lean run", "question": "q", "spec": "lean_guide"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    # And we can read it back via the threads/{tid} path → check metadata.
    from core.graph.threads import get_thread_spec
    assert get_thread_spec(tid) == "lean_guide"


def test_thread_patch_sets_then_clears_spec(client):
    # Create without spec
    r = client.post("/api/threads", json={"title": "x", "question": ""})
    tid = r.json()["id"]
    # PATCH to pin
    r2 = client.patch(f"/api/threads/{tid}",
                      json={"spec": "lean_guide"})
    assert r2.status_code == 200, r2.text
    from core.graph.threads import get_thread_spec
    assert get_thread_spec(tid) == "lean_guide"
    # PATCH with empty string clears
    r3 = client.patch(f"/api/threads/{tid}", json={"spec": ""})
    assert r3.status_code == 200, r3.text
    assert get_thread_spec(tid) is None


def test_thread_patch_spec_not_clobber_other_fields(client):
    r = client.post("/api/threads", json={"title": "z", "question": "qq"})
    tid = r.json()["id"]
    # Patch spec — title and question must survive.
    r2 = client.patch(f"/api/threads/{tid}", json={"spec": "lean_guide"})
    body = r2.json()
    assert body["title"] == "z"
    md = body.get("metadata") or {}
    assert md.get("question") == "qq"
    assert md.get("spec")     == "lean_guide"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
