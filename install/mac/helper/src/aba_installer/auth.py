"""Credential management.

For v1: API-key only. The user pastes an Anthropic API key in the UI; we
persist it to ~/.aba/config.env at mode 0600.
The ABA launcher (~/bin/aba) sources this file at startup so the
backend has ANTHROPIC_API_KEY in env.

OAuth via claude.ai is deferred to H8 (requires hosted callback or the
copy-paste device-flow shape).
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


_ANTHROPIC_KEY_PATTERN = re.compile(r"^sk-ant-[a-zA-Z0-9_\-]{16,}$")


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
    entries.setdefault("ABA_RUNTIME_DIR", str(runtime_dir()))
    entries.setdefault("ABA_HOME", str(aba_home()))
    _write_config_env(entries)
    return {"ok": True, "persisted": True}


@router.get("/status")
def auth_status() -> dict:
    """Whether credentials exist, and which flow. NEVER echoes the key itself."""
    entries = _read_config_env()
    flow = entries.get("ANTHROPIC_AUTH_FLOW")
    has_key = bool(entries.get("ANTHROPIC_API_KEY") or entries.get("ANTHROPIC_AUTH_TOKEN"))
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
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_AUTH_FLOW"):
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
