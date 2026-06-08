"""Credential management.

Two ways to authenticate, both persisted to ~/.aba/config.env (mode 0600),
which the ABA launcher (~/bin/aba) sources at startup:

  • Anthropic API key  → ANTHROPIC_API_KEY (billed to the user's org).
  • Claude.ai subscription → a Claude Code OAuth token (from
    `claude setup-token`) in CLAUDE_CODE_OAUTH_TOKEN, plus
    ABA_LLM_CREDENTIAL=oauth_cc so the backend uses the subscription bearer
    for non-Haiku models. The backend reads CLAUDE_CODE_OAUTH_TOKEN first
    and only falls back to ~/.claude if it's unset — so providing it here
    keeps the backend off ~/.claude entirely (see core/llm.py:_oauth_bearer).
"""
from __future__ import annotations
import os
import re
import shlex
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from aba_installer.paths import aba_home, config_env, runtime_dir


router = APIRouter(prefix="/api/auth", tags=["auth"])


# ─── shape ─────────────────────────────────────────────────────────────────
class ApiKeyIn(BaseModel):
    key: str
    persist: bool = True


class OAuthTokenIn(BaseModel):
    token: str
    persist: bool = True


# An OAuth token (sk-ant-oat…) and an API key (sk-ant-api…) share the sk-ant-
# prefix, so match the oauth marker specifically to give a clear error when a
# user pastes the wrong one into the wrong field.
_ANTHROPIC_KEY_PATTERN = re.compile(r"^sk-ant-[a-zA-Z0-9_\-]{16,}$")
_OAUTH_TOKEN_PATTERN = re.compile(r"^sk-ant-oat[a-zA-Z0-9_\-]{16,}$")


def _validate_api_key(key: str) -> None:
    """Cheap format validation. We don't roundtrip the Anthropic API here
    because (a) heavyweight, (b) flaky if the user's network is bad, and
    (c) the backend will surface real auth errors on first call anyway."""
    if not key or not isinstance(key, str):
        raise HTTPException(status_code=400, detail="key must be a non-empty string")
    stripped = key.strip()
    if not _ANTHROPIC_KEY_PATTERN.match(stripped):
        raise HTTPException(status_code=400,
                            detail="key doesn't look like an Anthropic API key (sk-ant-…)")


def _validate_oauth_token(token: str) -> None:
    if not token or not isinstance(token, str):
        raise HTTPException(status_code=400, detail="token must be a non-empty string")
    if not _OAUTH_TOKEN_PATTERN.match(token.strip()):
        raise HTTPException(
            status_code=400,
            detail="that doesn't look like a Claude Code OAuth token (sk-ant-oat…). "
                   "Run `claude setup-token` in a terminal and paste what it prints.")


# ─── persistence ───────────────────────────────────────────────────────────
def _parse_config_env(text: str) -> dict[str, str]:
    """Parse `export K=V` lines from config.env. Tolerant of quotes and
    comments; preserves keys we don't recognize so we don't blow away
    other lines if a future version adds fields."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        body = line[len("export "):] if line.startswith("export ") else line
        if "=" not in body:
            continue
        k, v = body.split("=", 1)
        k = k.strip()
        v = v.strip()
        # Drop matching surrounding quotes
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        out[k] = v
    return out


def _emit_config_env(entries: dict[str, str]) -> str:
    """Render entries back to a bash-sourceable config.env. Keys are
    written in deterministic order so re-emission is stable."""
    lines = ["# ABA config — sourced by ~/bin/aba at startup.",
             "# Auto-managed; safe to read, edit-by-hand at your own risk.",
             ""]
    for k in sorted(entries):
        lines.append(f'export {k}={shlex.quote(entries[k])}')
    return "\n".join(lines) + "\n"


def _read_config_env() -> dict[str, str]:
    p = config_env()
    if not p.exists():
        return {}
    return _parse_config_env(p.read_text())


def _write_config_env(entries: dict[str, str]) -> None:
    p = config_env()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(_emit_config_env(entries))
    os.chmod(p, 0o600)


# ─── endpoints ─────────────────────────────────────────────────────────────
@router.post("/apikey")
def set_apikey(payload: ApiKeyIn) -> dict:
    """Persist an Anthropic API key. Defaults runtime_dir + AUTH_FLOW so the
    backend gets the full env on next start without us asking again."""
    _validate_api_key(payload.key)
    if not payload.persist:
        # Held in process memory only — useful for "try without saving" flows
        return {"ok": True, "persisted": False}
    entries = _read_config_env()
    entries["ANTHROPIC_API_KEY"] = payload.key.strip()
    entries["ANTHROPIC_AUTH_FLOW"] = "api_key"
    # Switching from a prior OAuth setup → drop the subscription creds so the
    # backend doesn't see a stale mode.
    entries.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    entries.pop("ABA_LLM_CREDENTIAL", None)
    entries.setdefault("ABA_RUNTIME_DIR", str(runtime_dir()))
    entries.setdefault("ABA_HOME", str(aba_home()))
    _write_config_env(entries)
    return {"ok": True, "persisted": True}


@router.post("/oauth")
def set_oauth(payload: OAuthTokenIn) -> dict:
    """Persist a Claude Code OAuth token (Claude.ai Pro/Max subscription).

    Writes CLAUDE_CODE_OAUTH_TOKEN + ABA_LLM_CREDENTIAL=oauth_cc so the
    backend bills the user's subscription and can use non-Haiku models.
    """
    _validate_oauth_token(payload.token)
    if not payload.persist:
        return {"ok": True, "persisted": False}
    entries = _read_config_env()
    entries["CLAUDE_CODE_OAUTH_TOKEN"] = payload.token.strip()
    entries["ABA_LLM_CREDENTIAL"] = "oauth_cc"
    entries["ANTHROPIC_AUTH_FLOW"] = "oauth"
    # Switching from a prior API-key setup → drop the key.
    entries.pop("ANTHROPIC_API_KEY", None)
    entries.setdefault("ABA_RUNTIME_DIR", str(runtime_dir()))
    entries.setdefault("ABA_HOME", str(aba_home()))
    _write_config_env(entries)
    return {"ok": True, "persisted": True}


@router.get("/status")
def auth_status() -> dict:
    """Whether credentials exist, and which flow. NEVER echoes the key itself."""
    entries = _read_config_env()
    flow = entries.get("ANTHROPIC_AUTH_FLOW")
    has_key = bool(entries.get("ANTHROPIC_API_KEY")
                   or entries.get("ANTHROPIC_AUTH_TOKEN")
                   or entries.get("CLAUDE_CODE_OAUTH_TOKEN"))
    return {
        "credentials": has_key,
        "flow": flow,
        # Suffix for visual confirmation — last 4 chars only, never the whole key.
        "key_suffix": (entries.get("ANTHROPIC_API_KEY") or "")[-4:] if entries.get("ANTHROPIC_API_KEY") else None,
    }


@router.post("/clear")
def clear_credentials() -> dict:
    """Remove the API key / token from config.env. Other entries preserved."""
    entries = _read_config_env()
    removed = []
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_AUTH_FLOW",
              "CLAUDE_CODE_OAUTH_TOKEN", "ABA_LLM_CREDENTIAL"):
        if entries.pop(k, None) is not None:
            removed.append(k)
    if entries:
        _write_config_env(entries)
    else:
        # If nothing's left, remove the file entirely
        p = config_env()
        if p.exists():
            p.unlink()
    return {"ok": True, "removed": removed}
