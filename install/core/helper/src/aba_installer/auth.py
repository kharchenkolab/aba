"""Credential management.

Two ways to authenticate, both persisted to ~/.aba/config.env (mode 0600),
which the ABA launcher ($ABA_HOME/bin/aba) sources at startup:

  • Anthropic API key  → ANTHROPIC_API_KEY (billed to the user's org).
  • Claude.ai subscription → a Claude Code OAuth token (from
    `claude setup-token`) in CLAUDE_CODE_OAUTH_TOKEN, plus
    ABA_LLM_CREDENTIAL=oauth_cc so the backend uses the subscription bearer
    for non-Haiku models. The backend reads CLAUDE_CODE_OAUTH_TOKEN first
    and only falls back to ~/.claude if it's unset — so providing it here
    keeps the backend off ~/.claude entirely (see core/llm.py:_oauth_bearer).
"""
from __future__ import annotations
import base64
import hashlib
import json
import os
import re
import secrets
import shlex
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from aba_installer.paths import aba_home, config_env, runtime_dir


router = APIRouter(prefix="/api/auth", tags=["auth"])
# The OAuth callback lands at the app root (/callback) to match the redirect
# path Anthropic's OAuth client expects; service.py mounts this router.
callback_router = APIRouter(tags=["auth"])


# ─── Claude.ai OAuth (Sign in with Claude.ai) ───────────────────────────────
# These mirror the OAuth client the Claude Code CLI uses for subscription
# login. They are not an officially published third-party API, so if the
# "Sign in" flow ever stops working, re-check these against the current CLI.
_OAUTH_CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
_OAUTH_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
_OAUTH_TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
_OAUTH_SCOPES = "org:create_api_key user:profile user:inference"
# The token endpoint sits behind Cloudflare, which 403s (error 1010) the
# default Python-urllib User-Agent. Any non-default UA gets through.
_OAUTH_USER_AGENT = "aba-installer (kharchenkolab/aba)"

# Single in-flight browser flow (one user, one helper). Guarded by a lock.
_oauth_lock = threading.Lock()
_oauth_flow: dict = {}   # {state, verifier, redirect_uri, status, error}


def _pkce_pair() -> tuple[str, str]:
    """Return (verifier, challenge) for PKCE S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


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
    lines = ["# ABA config — sourced by $ABA_HOME/bin/aba at startup.",
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
    # Default to Opus — Haiku (the backend default) is underpowered for real
    # bioinformatics, and subscription/key users can afford the strongest model.
    # User-overridable via ABA_MODEL.
    entries.setdefault("ABA_MODEL", "claude-opus-4-7")
    _write_config_env(entries)
    return {"ok": True, "persisted": True}


def _persist_oauth_token(token: str) -> None:
    """Write a subscription OAuth bearer to config.env in oauth_cc mode.

    The backend reads CLAUDE_CODE_OAUTH_TOKEN first (core/llm.py), so this
    keeps it off ~/.claude entirely.
    """
    entries = _read_config_env()
    entries["CLAUDE_CODE_OAUTH_TOKEN"] = token.strip()
    entries["ABA_LLM_CREDENTIAL"] = "oauth_cc"
    entries["ANTHROPIC_AUTH_FLOW"] = "oauth"
    # Switching from a prior API-key setup → drop the key.
    entries.pop("ANTHROPIC_API_KEY", None)
    entries.setdefault("ABA_RUNTIME_DIR", str(runtime_dir()))
    entries.setdefault("ABA_HOME", str(aba_home()))
    # Default to Opus — Haiku (the backend default) is underpowered for real
    # bioinformatics, and subscription/key users can afford the strongest model.
    # User-overridable via ABA_MODEL.
    entries.setdefault("ABA_MODEL", "claude-opus-4-7")
    _write_config_env(entries)


def _persist_oauth_store(data: dict) -> None:
    """Write the refreshable OAuth store ($ABA_HOME/oauth.json, mode 0600) from
    the token-exchange response, so the backend can mint a new access token when
    this one expires (core/llm.py) instead of 401ing until a re-auth. Only the
    browser flow yields a refresh_token; the pasted/setup-token path has none,
    so it skips this and stays on the long-lived env-var credential."""
    import time
    rt = data.get("refresh_token")
    if not rt:
        return
    store = {
        "access_token": (data.get("access_token") or "").strip(),
        "refresh_token": rt,
        "expires_at": time.time() + (data.get("expires_in") or 3600),
    }
    p = aba_home() / "oauth.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.parent / "oauth.json.tmp"
        tmp.write_text(json.dumps(store))
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)        # atomic
    except Exception:  # noqa: BLE001
        pass


# ─── headless OAuth (paste-URL, like `claude setup-token`) ──────────────────
# For type-2/3 installs with no local browser: print an authorize URL, the user
# opens it on ANY browser (their laptop), approves, and pastes back the code
# Claude shows. Uses the code-display redirect so no localhost callback is
# needed. Reuses the same client + _exchange_code as the browser flow.
_OAUTH_MANUAL_REDIRECT = "https://console.anthropic.com/oauth/code/callback"


def build_headless_authorize_url() -> dict:
    """Return {authorize_url, state, verifier, redirect_uri} for the manual
    flow. The user opens authorize_url, approves, and copies the code Claude
    shows (format: code#state)."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    params = {
        "code": "true",
        "client_id": _OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": _OAUTH_MANUAL_REDIRECT,
        "scope": _OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    return {"authorize_url": _OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params),
            "state": state, "verifier": verifier, "redirect_uri": _OAUTH_MANUAL_REDIRECT}


def _parse_pasted_code(pasted: str, fallback_state: str) -> tuple[str, str]:
    """Accept whatever the user copies — a bare code, 'code#state', or the full
    redirect URL — and return (code, state)."""
    pasted = (pasted or "").strip()
    if pasted.startswith("http"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(pasted).query)
        return q.get("code", [""])[0].strip(), (q.get("state", [fallback_state])[0] or fallback_state).strip()
    if "#" in pasted:
        code, state = pasted.split("#", 1)
        return code.strip(), (state.strip() or fallback_state)
    return pasted, fallback_state


def complete_headless_oauth(pasted: str, *, state: str, verifier: str,
                            redirect_uri: str) -> dict:
    """Exchange the pasted authorization code for tokens and persist them
    (config.env + refreshable oauth.json). Raises RuntimeError on any problem."""
    code, pasted_state = _parse_pasted_code(pasted, state)
    if not code:
        raise RuntimeError("no authorization code found in what you pasted")
    if pasted_state and pasted_state != state:
        raise RuntimeError("state mismatch — restart the sign-in")
    data = _exchange_code(code, state, verifier, redirect_uri)
    _persist_oauth_token(data["access_token"])
    _persist_oauth_store(data)
    return data


# CLI-friendly persisters (no FastAPI/HTTPException — raise RuntimeError).
def persist_api_key(key: str) -> None:
    key = (key or "").strip()
    if not _ANTHROPIC_KEY_PATTERN.match(key):
        raise RuntimeError("that doesn't look like an Anthropic API key (sk-ant-…)")
    entries = _read_config_env()
    entries["ANTHROPIC_API_KEY"] = key
    entries["ANTHROPIC_AUTH_FLOW"] = "api_key"
    entries.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
    entries.pop("ABA_LLM_CREDENTIAL", None)
    entries.setdefault("ABA_RUNTIME_DIR", str(runtime_dir()))
    entries.setdefault("ABA_HOME", str(aba_home()))
    _write_config_env(entries)


def persist_setup_token(token: str) -> None:
    token = (token or "").strip()
    if not _OAUTH_TOKEN_PATTERN.match(token):
        raise RuntimeError("that doesn't look like a Claude Code OAuth token "
                           "(sk-ant-oat…). Run `claude setup-token` and paste its output.")
    _persist_oauth_token(token)


@router.post("/oauth")
def set_oauth(payload: OAuthTokenIn) -> dict:
    """Persist a pasted Claude Code OAuth token (the manual fallback to the
    browser 'Sign in with Claude.ai' flow). Writes CLAUDE_CODE_OAUTH_TOKEN +
    ABA_LLM_CREDENTIAL=oauth_cc."""
    _validate_oauth_token(payload.token)
    if not payload.persist:
        return {"ok": True, "persisted": False}
    _persist_oauth_token(payload.token)
    return {"ok": True, "persisted": True}


# ─── browser OAuth: Sign in with Claude.ai ──────────────────────────────────
@router.post("/oauth/start")
def oauth_start(request: Request) -> dict:
    """Begin the browser OAuth flow. Returns the claude.ai authorize URL the
    UI should open; the user logs in there and claude.ai redirects back to
    /callback on this helper."""
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    # Redirect back to THIS helper. claude.ai's client accepts localhost
    # callbacks on any port (that's how the CLI logs in locally).
    base = request.base_url  # e.g. http://127.0.0.1:8765/
    port = base.port or 8765
    redirect_uri = f"http://localhost:{port}/callback"
    params = {
        "code": "true",
        "client_id": _OAUTH_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": _OAUTH_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    authorize_url = _OAUTH_AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    with _oauth_lock:
        _oauth_flow.clear()
        _oauth_flow.update(state=state, verifier=verifier,
                           redirect_uri=redirect_uri, status="pending", error=None)
    return {"authorize_url": authorize_url}


@router.get("/oauth/poll")
def oauth_poll() -> dict:
    """The UI polls this after opening the browser. status ∈
    {none, pending, done, error}."""
    with _oauth_lock:
        if not _oauth_flow:
            return {"status": "none"}
        return {"status": _oauth_flow.get("status", "none"),
                "error": _oauth_flow.get("error")}


def _exchange_code(code: str, state: str, verifier: str, redirect_uri: str) -> dict:
    """Exchange an authorization code for tokens. Returns the full token dict
    ({access_token, refresh_token, expires_in}) so the caller can persist the
    refresh_token for later auto-refresh. Raises (with the endpoint's error
    body) on failure; caller records it."""
    body = json.dumps({
        "grant_type": "authorization_code",
        "code": code,
        "state": state,            # required — without it the endpoint 400s
        "redirect_uri": redirect_uri,
        "client_id": _OAUTH_CLIENT_ID,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(
        _OAUTH_TOKEN_URL, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": _OAUTH_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"token endpoint HTTP {e.code}: {detail}")
    tok = data.get("access_token")
    if not tok:
        raise RuntimeError(f"token endpoint returned no access_token: {list(data)}")
    return data


def _oauth_result_page(ok: bool, msg: str) -> str:
    color = "#2c7a3f" if ok else "#c83434"
    head = "Signed in ✓" if ok else "Sign-in failed"
    # On success, nudge the ABA Setup tab (our opener) to advance and close
    # this one. Works when both tabs share an origin — see setup.command,
    # which opens the UI on localhost to match the OAuth callback.
    advance = """
<script>
  try { if (window.opener && !window.opener.closed) window.opener.location.reload(); } catch (e) {}
  setTimeout(function () { try { window.close(); } catch (e) {} }, 1200);
</script>""" if ok else ""
    tail = ("Returning you to ABA Setup…" if ok
            else "You can close this tab and return to ABA Setup.")
    return f"""<!doctype html><meta charset=utf-8>
<title>ABA — {head}</title>
<body style="font:15px -apple-system,system-ui,sans-serif;max-width:520px;margin:80px auto;text-align:center">
<h2 style="color:{color}">{head}</h2>
<p style="color:#555">{msg}</p>
<p style="color:#888">{tail}</p>{advance}
</body>"""


@callback_router.get("/callback", response_class=HTMLResponse)
def oauth_callback(code: str = "", state: str = "", error: str = "") -> HTMLResponse:
    """claude.ai redirects here with ?code&state after the user authorizes."""
    with _oauth_lock:
        flow = dict(_oauth_flow)
    if error:
        _fail_flow(f"claude.ai returned: {error}")
        return HTMLResponse(_oauth_result_page(False, f"claude.ai returned: {error}"), status_code=400)
    if not flow or flow.get("status") != "pending":
        return HTMLResponse(_oauth_result_page(False, "No sign-in was in progress."), status_code=400)
    if not code or state != flow.get("state"):
        _fail_flow("state mismatch or missing code")
        return HTMLResponse(_oauth_result_page(False, "Security check failed (state mismatch)."), status_code=400)
    try:
        data = _exchange_code(code, state, flow["verifier"], flow["redirect_uri"])
        _persist_oauth_token(data["access_token"])   # config.env (back-compat)
        _persist_oauth_store(data)                   # refreshable store (#oauth-refresh)
    except Exception as e:  # noqa: BLE001
        _fail_flow(f"token exchange failed: {e}")
        return HTMLResponse(_oauth_result_page(False, "Could not complete sign-in. Try again, or paste a token instead."), status_code=500)
    with _oauth_lock:
        _oauth_flow["status"] = "done"
    return HTMLResponse(_oauth_result_page(True, "Your Claude.ai subscription is connected."))


def _fail_flow(msg: str) -> None:
    with _oauth_lock:
        if _oauth_flow:
            _oauth_flow["status"] = "error"
            _oauth_flow["error"] = msg


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


# ─── model selector ────────────────────────────────────────────────────
# Surfaced via the Control page's model dropdown so users can flip between
# haiku (cheap, fast), sonnet (balanced), opus (highest quality) without
# editing config.env by hand. Backend reads ABA_MODEL at startup
# (backend/core/config.py:56), so changes need a restart — the response
# carries restart_required=True so the UI can tell the user.
#
# IDs MUST match what backend/core/runtime/agent.py accepts; keep in sync
# if the model registry there changes.
_AVAILABLE_MODELS = [
    {"id": "claude-haiku-4-5",  "label": "Haiku 4.5  (fast, cheap)",
     "note": "Best for simple lookups / quick edits. Default for cost."},
    {"id": "claude-sonnet-4-6", "label": "Sonnet 4.6  (balanced)",
     "note": "Recommended for most real work. Good quality, reasonable cost."},
    {"id": "claude-opus-4-7",   "label": "Opus 4.7  (highest quality)",
     "note": "Best for complex multi-step bioinformatics. Higher cost / latency."},
]
_DEFAULT_MODEL = "claude-opus-4-7"      # mirrors auth.py:175 default for authed users
_MODEL_IDS = {m["id"] for m in _AVAILABLE_MODELS}


def get_model_tool() -> dict:
    """Tool-shaped accessor (no FastAPI) — exposes current model + the
    choices list. Used by the router below and by the unit tests."""
    entries = _read_config_env()
    return {"model": entries.get("ABA_MODEL") or _DEFAULT_MODEL,
            "available": list(_AVAILABLE_MODELS)}


def set_model_tool(payload: dict) -> dict:
    """Tool-shaped setter. Validates the model id, persists, signals
    `applied_on_next_turn` when the value actually changed.

    Hot-switch contract: the backend's guide.py reads
    config.current_model_for_primary() at the start of each turn, so a
    write to ~/.aba/config.env takes effect on the *next* user turn — no
    restart required. The response carries `applied_on_next_turn: true`
    on a real change so the UI / tray can word the notification correctly.
    """
    model = (payload or {}).get("model")
    if not isinstance(model, str) or model not in _MODEL_IDS:
        raise HTTPException(status_code=400,
                            detail=f"unknown model id {model!r}; valid: "
                                   f"{sorted(_MODEL_IDS)}")
    entries = _read_config_env()
    current = entries.get("ABA_MODEL")
    if current == model:
        return {"ok": True, "model": model, "applied_on_next_turn": False}
    entries["ABA_MODEL"] = model
    _write_config_env(entries)
    return {"ok": True, "model": model, "applied_on_next_turn": True,
            "note": "Backend resolves the model at each turn boundary — "
                    "the new model takes effect on your next message."}


@router.get("/model")
def get_model_endpoint() -> dict:
    return get_model_tool()


@router.post("/model")
def set_model_endpoint(payload: dict) -> dict:
    return set_model_tool(payload)


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
