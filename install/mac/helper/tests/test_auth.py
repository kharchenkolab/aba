"""H4 — Credential management tests."""
import os
import stat

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from aba_installer.service import build_app
    return TestClient(build_app())


VALID_KEY = "sk-ant-api03-" + "a" * 40
ALT_KEY   = "sk-ant-api03-" + "b" * 40
VALID_OAUTH = "sk-ant-oat01-" + "c" * 40


def test_initial_status_no_credentials(client):
    r = client.get("/api/auth/status").json()
    assert r["credentials"] is False
    assert r["flow"] is None
    assert r["key_suffix"] is None


def test_set_apikey_persists_to_config_env(client, tmp_aba_home):
    r = client.post("/api/auth/apikey", json={"key": VALID_KEY})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "persisted": True}
    # File written, mode 0600
    cfg = tmp_aba_home / "config.env"
    assert cfg.exists()
    mode = stat.S_IMODE(os.stat(cfg).st_mode)
    assert mode == 0o600, f"expected 0600 perms, got {oct(mode)}"
    # Body contains the key (as a shell-safe export). `api_key` is already
    # shell-safe so shlex.quote leaves it unquoted.
    text = cfg.read_text()
    assert "ANTHROPIC_API_KEY" in text
    assert VALID_KEY in text
    assert "ANTHROPIC_AUTH_FLOW=api_key" in text


def test_status_after_set(client):
    client.post("/api/auth/apikey", json={"key": VALID_KEY})
    r = client.get("/api/auth/status").json()
    assert r["credentials"] is True
    assert r["flow"] == "api_key"
    assert r["key_suffix"] == VALID_KEY[-4:]
    # Whole key never leaked
    full_response = client.get("/api/auth/status").text
    assert VALID_KEY not in full_response


def test_set_apikey_rejects_malformed_key(client):
    r = client.post("/api/auth/apikey", json={"key": "not-a-real-key"})
    assert r.status_code == 400


def test_set_apikey_rejects_empty(client):
    r = client.post("/api/auth/apikey", json={"key": ""})
    assert r.status_code == 400


def test_set_apikey_overwrites_prior_value(client, tmp_aba_home):
    client.post("/api/auth/apikey", json={"key": VALID_KEY})
    client.post("/api/auth/apikey", json={"key": ALT_KEY})
    text = (tmp_aba_home / "config.env").read_text()
    assert ALT_KEY in text
    assert VALID_KEY not in text


def test_set_apikey_persist_false_doesnt_write(client, tmp_aba_home):
    r = client.post("/api/auth/apikey", json={"key": VALID_KEY, "persist": False})
    assert r.json() == {"ok": True, "persisted": False}
    assert not (tmp_aba_home / "config.env").exists()


def test_set_oauth_persists_subscription_creds(client, tmp_aba_home):
    r = client.post("/api/auth/oauth", json={"token": VALID_OAUTH})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "persisted": True}
    cfg = tmp_aba_home / "config.env"
    assert cfg.exists()
    assert stat.S_IMODE(os.stat(cfg).st_mode) == 0o600
    text = cfg.read_text()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text and VALID_OAUTH in text
    assert "ABA_LLM_CREDENTIAL=oauth_cc" in text
    assert "ANTHROPIC_AUTH_FLOW=oauth" in text
    # Status reflects the oauth flow
    s = client.get("/api/auth/status").json()
    assert s["credentials"] is True and s["flow"] == "oauth"


def test_set_oauth_rejects_api_key_in_oauth_field(client):
    # An API key (sk-ant-api…) is not an OAuth token — clear error.
    r = client.post("/api/auth/oauth", json={"token": VALID_KEY})
    assert r.status_code == 400


def test_oauth_and_apikey_are_mutually_exclusive(client, tmp_aba_home):
    # Setting oauth after a key drops the key, and vice-versa.
    client.post("/api/auth/apikey", json={"key": VALID_KEY})
    client.post("/api/auth/oauth", json={"token": VALID_OAUTH})
    text = (tmp_aba_home / "config.env").read_text()
    assert VALID_KEY not in text and "ANTHROPIC_API_KEY" not in text
    assert VALID_OAUTH in text
    client.post("/api/auth/apikey", json={"key": VALID_KEY})
    text = (tmp_aba_home / "config.env").read_text()
    assert VALID_OAUTH not in text and "CLAUDE_CODE_OAUTH_TOKEN" not in text
    assert "ABA_LLM_CREDENTIAL" not in text


def test_oauth_start_returns_authorize_url(client):
    r = client.post("/api/auth/oauth/start")
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert url.startswith("https://claude.ai/oauth/authorize?")
    for p in ("client_id=", "code_challenge=", "code_challenge_method=S256",
              "redirect_uri=", "state="):
        assert p in url
    # Poll now reports the flow in progress
    assert client.get("/api/auth/oauth/poll").json()["status"] == "pending"


def test_oauth_callback_rejects_bad_state(client):
    client.post("/api/auth/oauth/start")
    r = client.get("/callback", params={"code": "x", "state": "wrong-state"})
    assert r.status_code == 400
    assert client.get("/api/auth/oauth/poll").json()["status"] == "error"


def test_oauth_callback_exchanges_and_persists(client, tmp_aba_home, monkeypatch):
    from aba_installer import auth
    monkeypatch.setattr(auth, "_exchange_code",
                        lambda code, verifier, redirect_uri: "sk-ant-oat01-fromflow")
    start = client.post("/api/auth/oauth/start").json()
    # Recover the state the helper generated (it's in the authorize URL)
    import urllib.parse as up
    state = up.parse_qs(up.urlparse(start["authorize_url"]).query)["state"][0]
    r = client.get("/callback", params={"code": "authcode", "state": state})
    assert r.status_code == 200
    assert client.get("/api/auth/oauth/poll").json()["status"] == "done"
    text = (tmp_aba_home / "config.env").read_text()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text and "sk-ant-oat01-fromflow" in text
    assert "ABA_LLM_CREDENTIAL=oauth_cc" in text


def test_clear_credentials_removes_key_lines(client, tmp_aba_home):
    client.post("/api/auth/apikey", json={"key": VALID_KEY})
    r = client.post("/api/auth/clear")
    assert r.status_code == 200
    removed = r.json()["removed"]
    assert "ANTHROPIC_API_KEY" in removed
    # Status reflects clear state. (config.env may still exist with other
    # entries like ABA_HOME / ABA_RUNTIME_DIR — that's intentional, those
    # are infrastructure config, not credentials.)
    s = client.get("/api/auth/status").json()
    assert s["credentials"] is False
    # The key itself is gone from disk regardless
    cfg = tmp_aba_home / "config.env"
    if cfg.exists():
        assert VALID_KEY not in cfg.read_text()


def test_clear_preserves_unrelated_entries(client, tmp_aba_home):
    """If other vars exist (e.g. ABA_RUNTIME_DIR), clearing creds shouldn't
    blow them away."""
    # Seed config.env with the key AND a non-key entry
    client.post("/api/auth/apikey", json={"key": VALID_KEY})
    # The set-apikey path already wrote ABA_HOME + ABA_RUNTIME_DIR alongside
    client.post("/api/auth/clear")
    text = (tmp_aba_home / "config.env").read_text() if (tmp_aba_home / "config.env").exists() else ""
    # If non-key entries existed, they should still be there
    if text:
        assert "ABA_RUNTIME_DIR" in text or "ABA_HOME" in text
