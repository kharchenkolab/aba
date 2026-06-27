"""H1 — service endpoints via FastAPI TestClient."""
import pytest


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from aba_installer.service import build_app
    return TestClient(build_app())


def test_ready_endpoint(client):
    r = client.get("/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "version" in body


def test_status_endpoint_initial(client):
    """Status lives under /api/status once the control router lands (H3)."""
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    assert "aba_home" in body
    assert body["installed"] is False  # no env_dir or repo_dir on a fresh ABA_HOME
    assert body["backend_running"] is False
    assert body["credentials"] is False


def test_status_reports_installed_when_runnable(client, tmp_aba_home):
    # Requires the artifacts a full install produces (not just dirs that appear
    # mid-prewarm): env/bin/uvicorn, built frontend, installed launcher.
    (tmp_aba_home / "env" / "bin").mkdir(parents=True)
    (tmp_aba_home / "env" / "bin" / "uvicorn").write_text("#!/bin/sh\n")
    (tmp_aba_home / "repo" / "aba" / "frontend" / "dist").mkdir(parents=True)
    (tmp_aba_home / "repo" / "aba" / "frontend" / "dist" / "index.html").write_text("<html>")
    (tmp_aba_home / "bin").mkdir()
    (tmp_aba_home / "bin" / "aba").write_text("#!/bin/sh\n")
    r = client.get("/api/status")
    assert r.json()["installed"] is True
