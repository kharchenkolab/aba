"""Subscription OAuth PKCE flow (core.oauth) — the framework + state machine.
The provider CONSTANTS are reverse-engineered and need live validation; these tests
cover the mechanics (PKCE, authorize-URL shape, flow store, code exchange + persist)
with the network mocked."""
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def test_disabled_by_default():
    os.environ.pop("ABA_SUBSCRIPTION_OAUTH", None)
    from core import oauth
    assert oauth.enabled() is False
    try:
        oauth.start("anthropic")
        assert False, "should refuse when disabled"
    except ValueError as e:
        assert "enabled" in str(e).lower()


def test_start_builds_pkce_authorize_url():
    os.environ["ABA_SUBSCRIPTION_OAUTH"] = "1"
    from core import oauth
    out = oauth.start("anthropic")
    assert out["flow_id"] and out["authorize_url"].startswith("https://claude.ai/oauth/authorize?")
    q = parse_qs(urlparse(out["authorize_url"]).query)
    assert q["response_type"] == ["code"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["code_challenge"] and q["state"]
    # the flow is stored with the verifier for later exchange
    assert out["flow_id"] in oauth._FLOWS_LIVE
    assert oauth._FLOWS_LIVE[out["flow_id"]]["verifier"]


def test_submit_exchanges_and_persists(monkeypatch=None):
    os.environ["ABA_SUBSCRIPTION_OAUTH"] = "1"
    os.environ["ABA_HOME"] = tempfile.mkdtemp(prefix="aba_oauth_")
    from core import oauth, credentials
    # mock the token exchange (no network)
    oauth._exchange = lambda flow, code, verifier: {"access_token": "acc-tok-123",
                                                    "refresh_token": "ref", "expires_at": 9999999999}
    out = oauth.start("openai")
    st = oauth.submit(out["flow_id"], "the-code#state")   # trailing #state stripped
    assert st["provider"] == "openai" and st["has_oauth"] and st["valid"]
    assert credentials.read().get("OPENAI_OAUTH_TOKEN") == "acc-tok-123"
    # flow consumed
    assert out["flow_id"] not in oauth._FLOWS_LIVE


def test_submit_bad_flow_id():
    os.environ["ABA_SUBSCRIPTION_OAUTH"] = "1"
    from core import oauth
    try:
        oauth.submit("nope", "code")
        assert False
    except ValueError as e:
        assert "expired" in str(e).lower()


def test_anthropic_submit_stores_oauth_cc():
    os.environ["ABA_SUBSCRIPTION_OAUTH"] = "1"
    os.environ["ABA_HOME"] = tempfile.mkdtemp(prefix="aba_oauth2_")
    from core import oauth, credentials
    oauth._exchange = lambda flow, code, verifier: {"access_token": "sk-oauth-anth"}
    out = oauth.start("anthropic")
    oauth.submit(out["flow_id"], "code")
    cfg = credentials.read()
    assert cfg.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-oauth-anth"
    assert cfg.get("ABA_LLM_CREDENTIAL") == "oauth_cc"


if __name__ == "__main__":
    test_disabled_by_default(); print("ok  disabled by default")
    test_start_builds_pkce_authorize_url(); print("ok  start builds PKCE authorize url")
    test_submit_exchanges_and_persists(); print("ok  submit exchanges + persists (openai)")
    test_submit_bad_flow_id(); print("ok  bad flow id rejected")
    test_anthropic_submit_stores_oauth_cc(); print("ok  anthropic submit → oauth_cc")
    print("all oauth-flow tests passed")
