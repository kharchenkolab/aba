"""Backend-owned LLM credential management (Settings → Account).

The desktop helper normally owns this; for the web Settings UI the backend must
own it too. We read/write ~/.aba/config.env (mode 0600 — the launcher sources it
at startup) AND update os.environ so a change is LIVE on the next turn without a
restart (mode + OAuth token are read live by core.llm; the API key is read live
via core.llm._current_api_key). The full browser "Sign in with Claude.ai" flow
stays a helper/CLI concern — here we accept a pasted token.

config.env is shared with the helper (same format: `export K=<shlex-quoted>`); we
upsert only the credential keys and preserve everything else.
"""
from __future__ import annotations
import os
import re
import shlex
from pathlib import Path

# Same patterns the helper validates against (install/.../auth.py).
_API_KEY_RE = re.compile(r"^sk-ant-[a-zA-Z0-9_\-]{16,}$")
_OAUTH_TOKEN_RE = re.compile(r"^sk-ant-oat[a-zA-Z0-9_\-]{16,}$")

_CRED_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_FLOW",
              "CLAUDE_CODE_OAUTH_TOKEN", "ABA_LLM_CREDENTIAL")


def _config_env_path() -> Path:
    home = Path(os.environ.get("ABA_HOME", str(Path.home() / ".aba")))
    return home / "config.env"


def _parse(text: str) -> dict:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        body = line[len("export "):] if line.startswith("export ") else line
        if "=" not in body:
            continue
        k, v = body.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        out[k] = v
    return out


def read() -> dict:
    p = _config_env_path()
    if not p.exists():
        return {}
    try:
        return _parse(p.read_text())
    except Exception:  # noqa: BLE001
        return {}


def write(entries: dict) -> None:
    """Atomic, 0600. Preserves non-credential keys the helper/launcher set."""
    p = _config_env_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# ABA config — sourced by $ABA_HOME/bin/aba at startup.",
             "# Auto-managed; safe to read, edit-by-hand at your own risk.", ""]
    for k in sorted(entries):
        lines.append(f"export {k}={shlex.quote(str(entries[k]))}")
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(p)


def _clear_llm_client_cache() -> None:
    try:
        from core import llm
        llm._ASYNC_CLIENT_CACHE.clear()
        llm._SYNC_CLIENT_CACHE.clear()
    except Exception:  # noqa: BLE001
        pass


def status() -> dict:
    """Current credential state for the UI. Never echoes the secret itself —
    only a 4-char suffix, the mode, and (for refreshable OAuth) the expiry."""
    cfg = read()
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or cfg.get("ANTHROPIC_API_KEY") or "")
    if not api_key:
        try:
            from core.config import API_KEY as _frozen
            api_key = _frozen or ""
        except Exception:  # noqa: BLE001
            api_key = ""
    mode = (os.environ.get("ABA_LLM_CREDENTIAL") or cfg.get("ABA_LLM_CREDENTIAL")
            or "apikey").lower()
    pasted = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
              or cfg.get("CLAUDE_CODE_OAUTH_TOKEN") or "")
    # Use the SAME resolver a turn uses (oauth.json → CLAUDE_CODE_OAUTH_TOKEN →
    # ~/.claude/.credentials.json) so status matches reality, not just our keys.
    oauth_active = False
    expires_at = None
    source = None
    try:
        from core.llm import _oauth_bearer, _load_oauth_store
        oauth_active = bool(_oauth_bearer())
        store = _load_oauth_store() or {}
        if store.get("access_token"):
            expires_at = store.get("expires_at")
            source = "refreshable_store"
        elif pasted:
            source = "pasted_token"
        elif oauth_active:
            source = "claude_cli"          # ~/.claude/.credentials.json
    except Exception:  # noqa: BLE001
        oauth_active = bool(pasted)
        source = "pasted_token" if pasted else None
    return {
        "mode": mode,
        "has_api_key": bool(api_key),
        "key_suffix": api_key[-4:] if api_key else None,
        "has_oauth": oauth_active,
        "oauth_source": source,
        "oauth_expires_at": expires_at,
    }


def set_api_key(key: str) -> dict:
    key = (key or "").strip()
    if not _API_KEY_RE.match(key):
        raise ValueError("That doesn't look like an Anthropic API key (expected sk-ant-…).")
    entries = read()
    entries["ANTHROPIC_API_KEY"] = key
    entries["ANTHROPIC_AUTH_FLOW"] = "api_key"
    # Leaving OAuth set would keep the backend in oauth mode — drop it.
    entries.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    entries.pop("ABA_LLM_CREDENTIAL", None)
    write(entries)
    os.environ["ANTHROPIC_API_KEY"] = key
    os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    os.environ.pop("ABA_LLM_CREDENTIAL", None)  # → _credential_mode() defaults to apikey
    _clear_llm_client_cache()
    return status()


def set_oauth_token(token: str) -> dict:
    token = (token or "").strip()
    if not _OAUTH_TOKEN_RE.match(token):
        raise ValueError("That doesn't look like a Claude.ai OAuth token (expected sk-ant-oat…).")
    entries = read()
    entries["CLAUDE_CODE_OAUTH_TOKEN"] = token
    entries["ABA_LLM_CREDENTIAL"] = "oauth_cc"
    entries["ANTHROPIC_AUTH_FLOW"] = "oauth"
    write(entries)
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = token
    os.environ["ABA_LLM_CREDENTIAL"] = "oauth_cc"
    _clear_llm_client_cache()
    return status()
