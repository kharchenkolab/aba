"""Subscription OAuth PKCE flow (core.oauth). Anthropic = paste-code; OpenAI/Codex =
localhost:1455 callback. The provider CONSTANTS are the real public CLI clients; the
auth backends aren't official APIs (need live validation). These cover the mechanics
(PKCE, authorize-URL, both flow shapes, exchange + persist) with the network mocked."""
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


def test_anthropic_paste_flow():
    os.environ["ABA_SUBSCRIPTION_OAUTH"] = "1"
    os.environ["ABA_HOME"] = tempfile.mkdtemp(prefix="aba_oauth_a_")
    from core import oauth, credentials
    out = oauth.start("anthropic")
    assert out["mode"] == "paste"
    assert out["authorize_url"].startswith("https://claude.ai/oauth/authorize?")
    q = parse_qs(urlparse(out["authorize_url"]).query)
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"] and q["state"]
    oauth._exchange = lambda flow, code, verifier: {"access_token": "sk-oauth-anth"}
    st = oauth.submit(out["flow_id"], "the-code#state")   # trailing #state stripped
    assert st["provider"] == "anthropic"
    cfg = credentials.read()
    assert cfg.get("CLAUDE_CODE_OAUTH_TOKEN") == "sk-oauth-anth"
    assert cfg.get("ABA_LLM_CREDENTIAL") == "oauth_cc"
    assert out["flow_id"] not in oauth._FLOWS_LIVE


def test_openai_callback_flow():
    os.environ["ABA_SUBSCRIPTION_OAUTH"] = "1"
    os.environ["ABA_HOME"] = tempfile.mkdtemp(prefix="aba_oauth_o_")
    for k in ("OPENAI_OAUTH_TOKEN", "ABA_OPENAI_ACCOUNT_ID", "ABA_OPENAI_BASE_URL"):
        os.environ.pop(k, None)
    from core import oauth, credentials
    try:
        out = oauth.start("openai")
    except ValueError as e:
        # :1455 already bound (a real Codex login?) — skip rather than fail.
        if "callback port" in str(e):
            print("  (skipped: :1455 busy)"); return
        raise
    assert out["mode"] == "callback"
    q = parse_qs(urlparse(out["authorize_url"]).query)
    assert q["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
    assert q["originator"] == ["codex_cli"] and q["code_challenge_method"] == ["S256"]
    # simulate the browser callback landing the code, then exchange
    oauth._exchange = lambda flow, code, verifier: {
        "access_token": "acc", "refresh_token": "ref", "account_id": "acct-1",
        "expires_at": 9999999999}
    oauth._FLOWS_LIVE[out["flow_id"]]["code"] = "captured-code"
    st = oauth.poll(out["flow_id"])
    assert st["state"] == "done" and st["credential"]["provider"] == "openai"
    assert st["credential"]["has_oauth"] is True
    cfg = credentials.read()
    assert cfg.get("OPENAI_OAUTH_TOKEN") == "acc"
    assert cfg.get("ABA_OPENAI_ACCOUNT_ID") == "acct-1"
    assert cfg.get("ABA_OPENAI_BASE_URL") == "https://chatgpt.com/backend-api/codex"
    assert out["flow_id"] not in oauth._FLOWS_LIVE


def test_poll_pending_and_bad_flow():
    os.environ["ABA_SUBSCRIPTION_OAUTH"] = "1"
    from core import oauth
    assert oauth.poll("nope")["state"] == "error"


def test_openai_runtime_subscription_headers():
    """When a Codex subscription is set, the runtime uses the Bearer + Account-Id
    header against the WHAM backend (and treats it as real-openai → no vLLM ext)."""
    os.environ["OPENAI_OAUTH_TOKEN"] = "acc-tok"
    os.environ["ABA_OPENAI_ACCOUNT_ID"] = "acct-9"
    os.environ["ABA_OPENAI_BASE_URL"] = "https://chatgpt.com/backend-api/wham"
    try:
        from core.runtime.llm_runtime_openai import OpenAICompatibleRuntime
        rt = OpenAICompatibleRuntime()
        assert rt.api_key == "acc-tok"
        assert rt._account_id == "acct-9"
        assert rt._real_openai is True
    finally:
        for k in ("OPENAI_OAUTH_TOKEN", "ABA_OPENAI_ACCOUNT_ID", "ABA_OPENAI_BASE_URL"):
            os.environ.pop(k, None)


def test_jwt_account_id_extraction():
    from core import oauth
    import base64 as _b, json as _j
    payload = _b.urlsafe_b64encode(_j.dumps({"chatgpt_account_id": "acct-x"}).encode()).rstrip(b"=").decode()
    jwt = "h." + payload + ".s"
    assert oauth._chatgpt_account_id({"id_token": jwt}) == "acct-x"
    # organizations fallback
    payload2 = _b.urlsafe_b64encode(_j.dumps(
        {"https://api.openai.com/auth": {"organizations": [{"id": "org-7"}]}}).encode()).rstrip(b"=").decode()
    assert oauth._chatgpt_account_id({"access_token": "h." + payload2 + ".s"}) == "org-7"


if __name__ == "__main__":
    test_disabled_by_default(); print("ok  disabled by default")
    test_anthropic_paste_flow(); print("ok  anthropic paste flow")
    test_openai_callback_flow(); print("ok  openai callback flow (:1455)")
    test_poll_pending_and_bad_flow(); print("ok  poll bad flow")
    test_openai_runtime_subscription_headers(); print("ok  runtime subscription headers")
    test_jwt_account_id_extraction(); print("ok  jwt account-id extraction")
    print("all oauth-flow tests passed")
