"""H7 — Static UI bundle.

The UI is plain HTML/CSS/JS shipped with the helper package. The
service mounts ui/ at /ui and serves index.html at /.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


UI_DIR = Path(__file__).resolve().parents[1] / "src/aba_installer/ui"


@pytest.fixture
def client():
    from aba_installer.service import build_app
    return TestClient(build_app())


def test_ui_files_exist():
    assert (UI_DIR / "index.html").exists()
    assert (UI_DIR / "app.js").exists()
    assert (UI_DIR / "app.css").exists()


def test_root_serves_index_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "<title>ABA</title>" in r.text
    assert 'id="app"' in r.text


def test_ui_static_files_reachable(client):
    r = client.get("/ui/app.js")
    assert r.status_code == 200
    assert "fetchJSON" in r.text     # known function in app.js
    r = client.get("/ui/app.css")
    assert r.status_code == 200
    assert ":root" in r.text


def test_index_references_static_files(client):
    body = client.get("/").text
    assert "/ui/app.css" in body
    assert "/ui/app.js" in body


def test_index_has_three_pages():
    """Welcome, Install, Control — the three UI states."""
    body = (UI_DIR / "index.html").read_text()
    assert 'id="page-welcome"' in body
    assert 'id="page-install"' in body
    assert 'id="page-control"' in body


def test_index_has_update_button():
    """The update affordance (user-requested) lives on the Control page."""
    body = (UI_DIR / "index.html").read_text()
    assert 'id="ctl-update"' in body


def test_app_js_posts_to_install_and_update_endpoints():
    js = (UI_DIR / "app.js").read_text()
    assert "/api/install" in js
    assert "/api/update" in js
    assert "/api/start" in js
    assert "/api/stop" in js
    assert "/api/uninstall" in js


def test_app_js_handles_sse_events():
    """The streamPlaybook helper parses SSE frames (event: <name>, data: <json>)."""
    js = (UI_DIR / "app.js").read_text()
    assert "event: " in js or "event:" in js
    assert "data: " in js or "data:" in js
    # Handles the four event names emitted by the executor
    for ev in ("step_start", "step_end", "complete"):
        assert ev in js, f"app.js should handle '{ev}' SSE event"
