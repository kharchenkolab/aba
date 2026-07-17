"""Subscription (Claude.ai) sign-in must persist a REFRESHABLE oauth store, so the
short-lived access token auto-refreshes instead of lapsing into a 401 the next turn
(the "ping green but chat fails" bug). Regression 2026-07-12.
"""
import sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.credentials as creds          # noqa: E402
import core.llm as llm                    # noqa: E402


def test_anthropic_oauth_persists_refreshable_store(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    # Avoid touching config.env formatting specifics / network status calls.
    monkeypatch.setattr(creds, "read", lambda: {})
    monkeypatch.setattr(creds, "write", lambda e: None)
    monkeypatch.setattr(creds, "status", lambda p="anthropic": {"provider": p, "valid": True})
    monkeypatch.setattr(creds, "_clear_llm_client_cache", lambda: None)

    creds.store_oauth_token("anthropic", {
        "access_token": "sk-ant-oat-NEW",
        "refresh_token": "rt-123",
        "expires_at": 9999999999,
    })

    store = json.loads((tmp_path / "oauth.json").read_text())
    assert store["access_token"] == "sk-ant-oat-NEW"
    assert store["refresh_token"] == "rt-123"        # <-- the previously-dropped field
    assert store["expires_at"] == 9999999999

    # And the bearer resolver now reads it (priority 1, refreshable).
    assert llm._oauth_bearer() == "sk-ant-oat-NEW"


def test_bearer_none_when_expired_no_refresh(monkeypatch, tmp_path):
    """A store with an expiry but no refresh token → bearer returns None on expiry, so
    status/ping report NOT valid (→ re-auth) instead of a misleading green."""
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    (tmp_path / "oauth.json").write_text(json.dumps(
        {"access_token": "dead", "expires_at": 1}))    # long past
    assert llm._oauth_bearer() is None


# ── 2026-07-18: a dead store must not poison the chain ───────────────────────

def test_failed_refresh_falls_through_to_cli_credential(monkeypatch, tmp_path):
    """An expired store whose refresh FAILS must fall through to the CLI
    credential (file or macOS keychain) instead of returning None — found
    live: a 400ing refresh blocked a perfectly valid CLI token."""
    import json as _json
    import time as _time
    from core import llm

    store = tmp_path / "oauth.json"
    store.write_text(_json.dumps({"access_token": "stale",
                                  "refresh_token": "dead",
                                  "expires_at": _time.time() - 3600}))
    monkeypatch.setattr(llm, "_oauth_store_path", lambda: str(store))
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)  # leaked by store_oauth_token in earlier tests
    monkeypatch.setattr(llm, "_refresh_oauth", lambda s: None)   # 400s
    monkeypatch.setattr(llm.os.path, "expanduser",
                        lambda p: str(tmp_path / "nofile") if ".claude" in p else p)
    llm._CLI_CRED_CACHE.update(tok=None, until=0.0)

    calls = {}

    class FakeProc:
        returncode = 0
        stdout = _json.dumps({"claudeAiOauth": {
            "accessToken": "cli-tok-123",
            "expiresAt": int((_time.time() + 3600) * 1000)}})
        stderr = ""

    def fake_run(argv, **kw):
        calls["argv"] = argv
        return FakeProc()
    import subprocess as _sp
    monkeypatch.setattr(_sp, "run", fake_run)
    monkeypatch.setattr(llm.sys, "platform", "darwin")
    monkeypatch.setattr(llm, "_CLI_KEYCHAIN_ENABLED", True)  # conftest disables

    assert llm._oauth_bearer() == "cli-tok-123"
    assert "security" in calls["argv"][0]

    # expired CLI token reads as missing (never a confusing 401 downstream)
    llm._CLI_CRED_CACHE.update(tok=None, until=0.0)
    FakeProc.stdout = _json.dumps({"claudeAiOauth": {
        "accessToken": "cli-tok-old",
        "expiresAt": int((_time.time() - 10) * 1000)}})
    assert llm._oauth_bearer() is None
