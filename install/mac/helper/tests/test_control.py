"""H3 — Control API endpoints."""
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    # Reset operation state between tests
    from aba_installer import control as cm
    cm._op_state.name = None
    cm._op_state.started_at = None
    cm._op_state.progress = []
    from aba_installer.service import build_app
    return TestClient(build_app())


# ─── status ────────────────────────────────────────────────────────────────
def test_status_fresh(client, tmp_aba_home):
    r = client.get("/api/status")
    assert r.status_code == 200
    j = r.json()
    assert j["aba_home"] == str(tmp_aba_home)
    assert j["installed"] is False
    assert j["operation"] is None


def test_status_installed_marker(client, tmp_aba_home):
    (tmp_aba_home / "env").mkdir()
    (tmp_aba_home / "repo" / "aba").mkdir(parents=True)
    j = client.get("/api/status").json()
    assert j["installed"] is True


# ─── install/update SSE ────────────────────────────────────────────────────
def test_install_streams_sse_events(client, tmp_aba_home, monkeypatch):
    """Replace the playbook with a tiny one so the SSE stream finishes
    in a few hundred ms."""
    fake_pb = tmp_aba_home / "fake.yml"
    fake_pb.write_text("""
defaults:
  timeout_seconds: 10
steps:
  - id: hello
    title: Say hello
    commands:
      - echo hi
""")
    # Monkeypatch the playbook path resolver to point at our tiny YAML
    from aba_installer import control as cm
    monkeypatch.setattr(cm, "_playbook_path", lambda name: fake_pb)

    with client.stream("POST", "/api/install") as r:
        assert r.status_code == 200
        # Collect events
        body = b""
        for chunk in r.iter_bytes():
            body += chunk
        text = body.decode()

    # Should have the standard event sequence
    assert "event: step_start" in text
    assert "event: command_start" in text
    assert "event: command_end" in text
    assert "event: step_end" in text
    assert "event: complete" in text
    # Payload includes the step id we set
    assert '"step_id": "hello"' in text


def test_update_uses_update_playbook(client, tmp_aba_home, monkeypatch):
    """Verify /api/update routes to update.yml — by intercepting
    _playbook_path we can confirm the right name was requested."""
    calls = []

    fake_pb = tmp_aba_home / "fake.yml"
    fake_pb.write_text("""
steps:
  - id: noop
    title: noop
    commands: ["true"]
""")

    from aba_installer import control as cm
    real = cm._playbook_path

    def spy(name):
        calls.append(name)
        return fake_pb

    monkeypatch.setattr(cm, "_playbook_path", spy)

    with client.stream("POST", "/api/update") as r:
        assert r.status_code == 200
        list(r.iter_bytes())   # drain

    assert calls == ["update"], f"expected /update to load 'update' playbook, got {calls}"


def test_install_409s_when_one_already_running(client, tmp_aba_home, monkeypatch):
    """While an operation is in flight, a second POST returns 409.

    We assert by manually holding the state lock — the route handler grabs
    it during _start_op, so simulating an in-flight op is more reliable
    than racing with a real subprocess.
    """
    from aba_installer import control as cm
    cm._op_state.name = "install"   # pretend a run is already in flight
    cm._op_state.started_at = 1.0
    try:
        r = client.post("/api/install")
        assert r.status_code == 409
        assert "already running" in r.json()["detail"]
        # /update is gated by the same lock
        r2 = client.post("/api/update")
        assert r2.status_code == 409
    finally:
        cm._op_state.name = None
        cm._op_state.started_at = None


def test_current_operation_replay(client, tmp_aba_home, monkeypatch):
    fake_pb = tmp_aba_home / "fake.yml"
    fake_pb.write_text("""
steps:
  - id: x
    title: x
    commands: ["echo ok"]
""")
    from aba_installer import control as cm
    monkeypatch.setattr(cm, "_playbook_path", lambda name: fake_pb)

    with client.stream("POST", "/api/install") as r:
        list(r.iter_bytes())
    # After completion: operation cleared
    op = client.get("/api/operation").json()
    assert op["name"] is None
    # But events buffer still has records from the run
    assert len(op["events"]) >= 4   # step_start, command_start, command_end, step_end


# ─── start / stop ──────────────────────────────────────────────────────────
def test_start_requires_launcher(client, tmp_aba_home, monkeypatch):
    # No launcher installed → 409
    monkeypatch.setattr("aba_installer.control._aba_launcher", lambda: None)
    monkeypatch.setattr("aba_installer.control._backend_pid", lambda: None)
    r = client.post("/api/start")
    assert r.status_code == 409


def test_start_noop_when_already_running(client, monkeypatch):
    monkeypatch.setattr("aba_installer.control._backend_pid", lambda: 12345)
    r = client.post("/api/start")
    assert r.status_code == 200
    assert r.json()["already_running"] is True


def test_stop_when_not_running(client, monkeypatch):
    monkeypatch.setattr("aba_installer.control._aba_launcher", lambda: None)
    monkeypatch.setattr("aba_installer.control._backend_pid", lambda: None)
    r = client.post("/api/stop")
    assert r.status_code == 200
    assert r.json().get("already_stopped") is True


# ─── logs ──────────────────────────────────────────────────────────────────
def test_logs_empty_when_no_log_file(client, tmp_aba_home):
    j = client.get("/api/logs").json()
    assert j["lines"] == []


def test_logs_tails_file(client, tmp_aba_home):
    log_path = tmp_aba_home / "logs"
    log_path.mkdir(parents=True, exist_ok=True)
    (log_path / "backend.log").write_text("\n".join(f"line-{i}" for i in range(20)))
    j = client.get("/api/logs?tail=5").json()
    assert j["lines"] == [f"line-{i}" for i in range(15, 20)]


# ─── uninstall ─────────────────────────────────────────────────────────────
def test_uninstall_removes_env_and_repo_keeps_runtime(client, tmp_aba_home):
    (tmp_aba_home / "env").mkdir()
    (tmp_aba_home / "repo" / "aba").mkdir(parents=True)
    (tmp_aba_home / "runtime" / "projects").mkdir(parents=True)
    (tmp_aba_home / "config.env").write_text("X=1")

    r = client.post("/api/uninstall")
    assert r.status_code == 200
    j = r.json()
    assert "env" in j["removed"]
    assert "repo" in j["removed"]
    # Runtime + config kept
    assert (tmp_aba_home / "runtime").exists()
    assert (tmp_aba_home / "config.env").exists()


def test_uninstall_full_blast_removes_everything(client, tmp_aba_home):
    (tmp_aba_home / "env").mkdir()
    (tmp_aba_home / "runtime" / "projects").mkdir(parents=True)
    (tmp_aba_home / "config.env").write_text("X=1")
    r = client.post("/api/uninstall?keep_runtime=false")
    j = r.json()
    assert "runtime" in j["removed"]
    assert "config.env" in j["removed"]
    assert not (tmp_aba_home / "runtime").exists()
    assert not (tmp_aba_home / "config.env").exists()


def test_prewarm_noop_when_env_already_built(client, tmp_aba_home):
    # If conda-meta exists the env is already built — prewarm must NOT kick off
    # a (multi-GB) download. This guards the prewarm/install idempotency.
    (tmp_aba_home / "env" / "conda-meta").mkdir(parents=True)
    r = client.post("/api/install/prewarm")
    assert r.status_code == 200
    assert r.json() == {"started": False, "status": "done"}


def test_prewarm_status_endpoint(client):
    r = client.get("/api/install/prewarm")
    assert r.status_code == 200
    assert "status" in r.json() and "events" in r.json()
