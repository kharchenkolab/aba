"""Lazy-env-init Phase A: credential-less start is a first-class, surfaced state.

`any_configured()` drives the app's first-run / skip-agent gate; `friendly_error`
points a no-credential chat turn at Settings → Agent instead of a raw 401.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.credentials as creds          # noqa: E402
import core.oauth as oauth                 # noqa: E402
import core.runtime.llm_errors as le       # noqa: E402
from core.web.routers import settings as settings_route  # noqa: E402


def test_any_configured_none(monkeypatch):
    monkeypatch.setattr(creds, "status", lambda prov="anthropic": {"provider": prov, "valid": False})
    assert creds.any_configured() == {"configured": False, "provider": None}


def test_any_configured_picks_first_valid(monkeypatch):
    monkeypatch.setattr(creds, "status",
                        lambda prov="anthropic": {"provider": prov, "valid": prov == "openai"})
    assert creds.any_configured() == {"configured": True, "provider": "openai"}


def test_friendly_error_no_credential_points_at_settings(monkeypatch):
    monkeypatch.setattr(creds, "any_configured", lambda: {"configured": False, "provider": None})
    msg = le.friendly_error(Exception("boom 401 authentication_error"))
    assert "Settings" in msg and "provider" in msg.lower()


def test_credential_status_surfaces_oauth_enabled(monkeypatch):
    """The UI hides the Subscription tab when the deployment doesn't offer OAuth, so
    the credential status must carry oauth_enabled tracking ABA_SUBSCRIPTION_OAUTH."""
    monkeypatch.setattr(creds, "status", lambda prov="anthropic": {"provider": prov, "valid": False})
    monkeypatch.delenv("ABA_SUBSCRIPTION_OAUTH", raising=False)
    assert settings_route.settings_credential_get("anthropic")["oauth_enabled"] is False
    monkeypatch.setenv("ABA_SUBSCRIPTION_OAUTH", "1")
    assert settings_route.settings_credential_get("anthropic")["oauth_enabled"] is True
    assert oauth.enabled() is True


def test_friendly_error_with_credential_falls_through(monkeypatch):
    monkeypatch.setattr(creds, "any_configured", lambda: {"configured": True, "provider": "anthropic"})
    class E(Exception):
        status_code = 401
    msg = le.friendly_error(E("authentication_error"))
    assert "Settings → Agent" not in msg   # a real 401 (cred present) isn't the connect prompt
