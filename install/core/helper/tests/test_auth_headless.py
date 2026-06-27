"""Headless OAuth helpers + CLI-friendly credential persisters (auth.py / cli.py).
The live token roundtrip needs a real Claude login (tested by hand); here we
cover URL building, paste parsing, validation, and persistence."""
import urllib.parse
import pytest


def test_build_headless_authorize_url():
    from aba_installer import auth
    info = auth.build_headless_authorize_url()
    assert info["authorize_url"].startswith(auth._OAUTH_AUTHORIZE_URL)
    q = urllib.parse.parse_qs(urllib.parse.urlparse(info["authorize_url"]).query)
    assert q["client_id"][0] == auth._OAUTH_CLIENT_ID
    assert q["code_challenge_method"][0] == "S256"
    assert q["state"][0] == info["state"]
    assert q["redirect_uri"][0] == info["redirect_uri"]


def test_parse_pasted_code_formats():
    from aba_installer.auth import _parse_pasted_code
    assert _parse_pasted_code("ABC", "S0") == ("ABC", "S0")              # bare code
    assert _parse_pasted_code("ABC#XYZ", "S0") == ("ABC", "XYZ")        # code#state
    url = "https://console.anthropic.com/oauth/code/callback?code=ABC&state=XYZ"
    assert _parse_pasted_code(url, "S0") == ("ABC", "XYZ")             # full URL


def test_persist_api_key_validates_and_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    from aba_installer import auth
    with pytest.raises(RuntimeError):
        auth.persist_api_key("not-a-key")
    auth.persist_api_key("sk-ant-api03-" + "x" * 20)
    cfg = (tmp_path / "config.env").read_text()
    assert "ANTHROPIC_API_KEY" in cfg and "api_key" in cfg


def test_persist_setup_token_validates_and_writes(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    from aba_installer import auth
    with pytest.raises(RuntimeError):
        auth.persist_setup_token("sk-ant-api03-xxxxxxxxxxxxxxxx")   # api key, not oat
    auth.persist_setup_token("sk-ant-oat01-" + "y" * 20)
    cfg = (tmp_path / "config.env").read_text()
    assert "CLAUDE_CODE_OAUTH_TOKEN" in cfg and "oauth_cc" in cfg


def test_cli_auth_api_key_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from aba_installer import cli
    assert cli.main(["auth", "--api-key", "sk-ant-api03-" + "z" * 20]) == 0
    assert cli.main(["auth", "--api-key", "bogus"]) == 1
