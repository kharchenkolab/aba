"""Backend LLM credential management (Settings -> Account). Isolated ABA_HOME so
the real ~/.aba/config.env is never touched."""
import os
import sys
from pathlib import Path
import pytest
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def test_credentials_status_set_key_and_oauth(tmp_path, monkeypatch):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    for k in ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "ABA_LLM_CREDENTIAL"):
        monkeypatch.delenv(k, raising=False)
    from core import credentials
    # nothing configured -> apikey mode, no key
    st = credentials.status()
    assert st["mode"] == "apikey" and st["has_api_key"] is False and st["key_suffix"] is None
    # validation
    with pytest.raises(ValueError):
        credentials.set_api_key("not-a-key")
    # set a valid-format API key -> persisted to the isolated config.env + live env
    key = "sk-ant-" + "a" * 30
    st = credentials.set_api_key(key)
    assert st["has_api_key"] and st["key_suffix"] == "aaaa" and st["mode"] == "apikey"
    assert os.environ["ANTHROPIC_API_KEY"] == key
    cfg = credentials.read()
    assert cfg["ANTHROPIC_API_KEY"] == key and cfg["ANTHROPIC_AUTH_FLOW"] == "api_key"
    assert (tmp_path / "config.env").exists()
    # 0600 perms on the written file
    assert oct((tmp_path / "config.env").stat().st_mode & 0o777) == "0o600"
    # the live read in llm.py picks up the new key
    from core.llm import _current_api_key
    assert _current_api_key() == key
    # set a pasted OAuth token -> switches mode to oauth_cc, live
    with pytest.raises(ValueError):
        credentials.set_oauth_token("sk-ant-notanoauthtoken00000")
    tok = "sk-ant-oat" + "b" * 30
    st = credentials.set_oauth_token(tok)
    assert st["mode"] == "oauth_cc" and st["has_oauth"] and st["oauth_source"] == "pasted_token"
    assert os.environ["ABA_LLM_CREDENTIAL"] == "oauth_cc"
    assert credentials.read()["CLAUDE_CODE_OAUTH_TOKEN"] == tok
    # switching back to an API key clears the OAuth creds
    st = credentials.set_api_key("sk-ant-" + "c" * 30)
    assert st["mode"] == "apikey" and "CLAUDE_CODE_OAUTH_TOKEN" not in credentials.read()
