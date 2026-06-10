"""Hot model resolution — current_model_for_primary().

The backend currently captures ABA_MODEL once at module import
(backend/core/config.py:56) and bakes it into the primary AgentSpec.
Switching the model from the tray / helper UI writes config.env but the
live backend never reads it again — so the user has to restart.

current_model_for_primary() reads the live state on demand: in-process
env vars first (test override + back-compat with launcher-sourced env),
then a fresh parse of ~/.aba/config.env's ABA_MODEL line (set by the
helper's POST /api/auth/model), then a caller-supplied default (the
spec's YAML-declared model). Read every call — file-read is microseconds
and the only callsite (guide.py per-turn) is amortised across the LLM
roundtrip.

Run: .venv/bin/python -m pytest tests/test_current_model_resolution.py -q
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import config   # noqa: E402


_DEFAULT = "claude-haiku-4-5-20251001"


@pytest.fixture
def isolated_aba_home(tmp_path, monkeypatch):
    """Point ABA_HOME at a clean tempdir; clear model-related env vars so
    each test sees a fresh resolution chain."""
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    for k in ("ABA_MODEL", "ABA_PRIMARY_MODEL"):
        monkeypatch.delenv(k, raising=False)
    yield tmp_path


def _write_config_env(home: Path, **entries) -> None:
    """Write a config.env in the helper's canonical format."""
    cfg = home / "config.env"
    lines = ["# test config.env"]
    for k, v in entries.items():
        lines.append(f"export {k}={v}")
    cfg.write_text("\n".join(lines) + "\n")


# ─── precedence: env > config.env > default ────────────────────────────
def test_env_var_overrides_everything(isolated_aba_home, monkeypatch):
    monkeypatch.setenv("ABA_MODEL", "claude-sonnet-4-6")
    _write_config_env(isolated_aba_home, ABA_MODEL="claude-opus-4-7")
    assert config.current_model_for_primary(default=_DEFAULT) == "claude-sonnet-4-6"


def test_primary_env_var_takes_precedence_over_aba_model(isolated_aba_home, monkeypatch):
    """ABA_PRIMARY_MODEL is the targeted override (only affects the chat
    agent, not advisors). Mirror load_agent_spec's precedence so a process
    that already set both does the same thing here."""
    monkeypatch.setenv("ABA_PRIMARY_MODEL", "claude-opus-4-7")
    monkeypatch.setenv("ABA_MODEL", "claude-sonnet-4-6")
    assert config.current_model_for_primary(default=_DEFAULT) == "claude-opus-4-7"


def test_config_env_used_when_no_env_override(isolated_aba_home):
    _write_config_env(isolated_aba_home, ABA_MODEL="claude-opus-4-7")
    assert config.current_model_for_primary(default=_DEFAULT) == "claude-opus-4-7"


def test_default_used_when_neither_env_nor_config_env(isolated_aba_home):
    assert config.current_model_for_primary(default=_DEFAULT) == _DEFAULT


# ─── live re-read: the load-bearing property ───────────────────────────
def test_subsequent_call_picks_up_config_env_change_without_process_restart(
    isolated_aba_home,
):
    """The whole point: write a new value to config.env, the very next
    call sees it. No caching that survives across calls."""
    _write_config_env(isolated_aba_home, ABA_MODEL="claude-haiku-4-5")
    assert config.current_model_for_primary(default=_DEFAULT) == "claude-haiku-4-5"
    # User picks a different model in the tray — helper writes config.env.
    _write_config_env(isolated_aba_home, ABA_MODEL="claude-opus-4-7")
    # Next turn (= next call): sees the new value.
    assert config.current_model_for_primary(default=_DEFAULT) == "claude-opus-4-7"


def test_subsequent_call_picks_up_env_var_change(isolated_aba_home, monkeypatch):
    monkeypatch.setenv("ABA_MODEL", "claude-haiku-4-5")
    assert config.current_model_for_primary(default=_DEFAULT) == "claude-haiku-4-5"
    monkeypatch.setenv("ABA_MODEL", "claude-sonnet-4-6")
    assert config.current_model_for_primary(default=_DEFAULT) == "claude-sonnet-4-6"


# ─── robustness: malformed config.env doesn't crash the turn ───────────
def test_malformed_config_env_falls_back_to_default(isolated_aba_home):
    cfg = isolated_aba_home / "config.env"
    cfg.write_text("this is not in export K=V form at all\n??? ??? ???\n")
    # Should not raise; should fall through to default.
    assert config.current_model_for_primary(default=_DEFAULT) == _DEFAULT


def test_config_env_without_aba_model_key_falls_through(isolated_aba_home):
    """config.env exists but only has credentials, no ABA_MODEL line.
    Common state right after sign-in."""
    _write_config_env(isolated_aba_home,
                      CLAUDE_CODE_OAUTH_TOKEN="sk-ant-oat-xxx",
                      ABA_LLM_CREDENTIAL="oauth_cc")
    assert config.current_model_for_primary(default=_DEFAULT) == _DEFAULT


def test_missing_config_env_silently_falls_through(isolated_aba_home):
    """No config.env file at all. The function must not raise."""
    # isolated_aba_home fixture left it empty
    assert not (isolated_aba_home / "config.env").exists()
    assert config.current_model_for_primary(default=_DEFAULT) == _DEFAULT


# ─── empty / whitespace values are ignored ─────────────────────────────
def test_empty_env_var_value_does_not_override(isolated_aba_home):
    """ABA_MODEL='' should be treated as 'not set'; falling through to
    config.env / default keeps the user out of a footgun ('I set it to
    nothing therefore I get nothing')."""
    os.environ["ABA_MODEL"] = ""        # explicit empty
    try:
        _write_config_env(isolated_aba_home, ABA_MODEL="claude-opus-4-7")
        assert config.current_model_for_primary(default=_DEFAULT) == "claude-opus-4-7"
    finally:
        del os.environ["ABA_MODEL"]
