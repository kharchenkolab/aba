"""Helper API: /api/auth/model — read + persist the agent's model name.

ABA_MODEL is the backend's switch between haiku (cheap, fast), sonnet
(balanced), and opus (highest quality). Today the only way to change it is
to hand-edit ~/.aba/config.env + restart the backend. These tests cover
the new endpoint pair that lets the Control page do it from the browser.
"""
from __future__ import annotations
import os

import pytest


# Reuse the helper test pattern: pure-function tests don't need the
# FastAPI test client, but the model endpoint is small enough to drive
# through the auth router's persistence helpers directly.


def _seed_config(tmp_path, **entries):
    """Write a config.env at $ABA_HOME/config.env using auth's emitter so
    the round-trip semantics match production."""
    from aba_installer import auth
    from aba_installer.paths import config_env
    p = config_env()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(auth._emit_config_env(entries))


def test_get_model_returns_current_value_from_config_env(tmp_path):
    from aba_installer import auth
    _seed_config(tmp_path, ABA_MODEL="claude-sonnet-4-6")
    out = auth.get_model_tool()
    assert out["model"] == "claude-sonnet-4-6"
    # Always include the choices list so the UI can render a dropdown
    # without hard-coding model IDs that might drift from the backend.
    assert "available" in out and isinstance(out["available"], list)
    assert any(m["id"] == "claude-haiku-4-5" for m in out["available"])
    assert any(m["id"] == "claude-sonnet-4-6" for m in out["available"])
    assert any(m["id"] == "claude-opus-4-7" for m in out["available"])


def test_get_model_falls_back_to_default_when_unset(tmp_path):
    from aba_installer import auth
    _seed_config(tmp_path)   # no ABA_MODEL key
    out = auth.get_model_tool()
    # Whatever the install picks as default — must be a STRING, not empty,
    # and must be in the available list (no orphaned default).
    assert isinstance(out["model"], str) and out["model"]
    ids = {m["id"] for m in out["available"]}
    assert out["model"] in ids


def test_set_model_persists_to_config_env(tmp_path):
    from aba_installer import auth
    from aba_installer.paths import config_env
    _seed_config(tmp_path, ABA_MODEL="claude-haiku-4-5",
                 CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat-x",
                 ABA_LLM_CREDENTIAL="oauth_cc")

    res = auth.set_model_tool({"model": "claude-opus-4-7"})
    assert res.get("ok") is True
    # File on disk got the new value
    text = config_env().read_text()
    assert "ABA_MODEL=claude-opus-4-7" in text or "ABA_MODEL='claude-opus-4-7'" in text
    # Existing credential lines preserved (don't blow them away on model change)
    assert "CLAUDE_CODE_OAUTH_TOKEN" in text
    assert "ABA_LLM_CREDENTIAL" in text


def test_set_model_rejects_unknown_id():
    from aba_installer import auth
    with pytest.raises(Exception) as ei:
        auth.set_model_tool({"model": "claude-spaghetti-9-0"})
    assert "unknown" in str(ei.value).lower() or "invalid" in str(ei.value).lower()


def test_set_model_signals_restart_required(tmp_path):
    """The backend reads ABA_MODEL at module-import time (config.py:56),
    so a model change only takes effect after a backend restart. The
    response must include a hint the UI can show — better than silent
    no-op."""
    from aba_installer import auth
    _seed_config(tmp_path, ABA_MODEL="claude-haiku-4-5")
    res = auth.set_model_tool({"model": "claude-sonnet-4-6"})
    assert res.get("restart_required") is True


def test_set_model_idempotent_no_op_when_already_set(tmp_path):
    from aba_installer import auth
    _seed_config(tmp_path, ABA_MODEL="claude-haiku-4-5")
    res = auth.set_model_tool({"model": "claude-haiku-4-5"})
    assert res.get("ok") is True
    # No restart hint when nothing changed.
    assert not res.get("restart_required")
