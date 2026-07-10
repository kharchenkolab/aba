"""Subscription sign-in (OAuth PKCE) for the Settings → Agent "Subscription" button.

Lets a user connect their Anthropic (Claude.ai) or OpenAI (ChatGPT/Codex) *plan*
instead of pasting an API key — the analog of the existing paste-a-token path, but
in-app. Flow (works both locally and behind a remote/OOD proxy, so we use the
authorize→paste-code model rather than a localhost callback):

  1. start(provider) → generate PKCE (verifier/challenge) + state, build the
     provider authorize URL, stash the flow (in-memory, short-lived), return
     {flow_id, authorize_url}.
  2. The UI opens authorize_url; the user signs in and copies the shown code.
  3. submit(flow_id, code) → exchange (code + verifier) at the token endpoint,
     verify, and persist via core.credentials, returning the new status.

⚠ REVERSE-ENGINEERED / FRAGILE. The per-provider constants below (client ids,
endpoints, redirect, scopes) mirror the public CLI OAuth clients and are NOT
official APIs — they can change without notice. Gated behind ABA_SUBSCRIPTION_OAUTH
(off by default); the paste-token method stays the always-works fallback. The flow
framework + PKCE are correct and unit-tested; the constants need LIVE validation
against each provider before this ships enabled.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode


def enabled() -> bool:
    return (os.environ.get("ABA_SUBSCRIPTION_OAUTH") or "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ProviderFlow:
    provider: str
    client_id: str
    authorize_url: str
    token_url: str
    redirect_uri: str
    scopes: str
    # extra static params some providers require on the authorize call
    extra_authorize: dict = field(default_factory=dict)


# NOTE: these mirror the public CLI OAuth clients. Marked TENTATIVE where the exact
# value needs confirmation against a live sign-in. Do NOT enable the flag until
# validated end-to-end.
_FLOWS: dict[str, ProviderFlow] = {
    # Claude Code's public OAuth client (client_id is public; the paste-code
    # redirect shows the auth code for the user to copy).
    "anthropic": ProviderFlow(
        provider="anthropic",
        client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        authorize_url="https://claude.ai/oauth/authorize",
        token_url="https://console.anthropic.com/v1/oauth/token",
        redirect_uri="https://console.anthropic.com/oauth/code/callback",
        scopes="org:create_api_key user:profile user:inference",
    ),
    # OpenAI Codex CLI OAuth (TENTATIVE — client_id/endpoints/redirect need live
    # confirmation; ChatGPT-backend auth differs from the api.openai.com API).
    "openai": ProviderFlow(
        provider="openai",
        client_id=os.environ.get("ABA_OPENAI_OAUTH_CLIENT_ID", "app_codex"),  # TENTATIVE
        authorize_url="https://auth.openai.com/oauth/authorize",              # TENTATIVE
        token_url="https://auth.openai.com/oauth/token",                      # TENTATIVE
        redirect_uri="https://auth.openai.com/oauth/callback",               # TENTATIVE
        scopes="openid profile email offline_access",                        # TENTATIVE
    ),
}

# In-memory, short-lived flow store (flow_id → dict). Not persisted: a flow is a
# few-minute interaction; a process restart just means "start over".
_FLOWS_LIVE: dict[str, dict] = {}
_FLOW_TTL_S = 900  # 15 min


def _pkce() -> tuple[str, str]:
    """(verifier, challenge) — RFC 7636 S256."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _gc(now: float) -> None:
    dead = [k for k, v in _FLOWS_LIVE.items() if now - v["created"] > _FLOW_TTL_S]
    for k in dead:
        _FLOWS_LIVE.pop(k, None)


def start(provider: str) -> dict:
    """Begin a subscription sign-in. Returns {flow_id, authorize_url}."""
    if not enabled():
        raise ValueError("Subscription sign-in isn't enabled on this deployment.")
    flow = _FLOWS.get(provider)
    if not flow:
        raise ValueError(f"No subscription sign-in for provider {provider!r}.")
    now = time.time(); _gc(now)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    flow_id = secrets.token_urlsafe(18)
    _FLOWS_LIVE[flow_id] = {"provider": provider, "verifier": verifier,
                            "state": state, "created": now}
    params = {
        "response_type": "code",
        "client_id": flow.client_id,
        "redirect_uri": flow.redirect_uri,
        "scope": flow.scopes,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **flow.extra_authorize,
    }
    return {"flow_id": flow_id, "authorize_url": flow.authorize_url + "?" + urlencode(params)}


def submit(flow_id: str, code: str) -> dict:
    """Exchange the pasted authorization code for a token, verify + persist it via
    core.credentials, and return the new credential status. Raises ValueError on a
    bad/expired flow or a rejected exchange."""
    live = _FLOWS_LIVE.get(flow_id)
    if not live:
        raise ValueError("This sign-in expired — start it again.")
    if time.time() - live["created"] > _FLOW_TTL_S:
        _FLOWS_LIVE.pop(flow_id, None)
        raise ValueError("This sign-in expired — start it again.")
    flow = _FLOWS[live["provider"]]
    code = (code or "").strip()
    # Codes are often shown as `<code>#<state>` — split the fragment off.
    if "#" in code:
        code = code.split("#", 1)[0]
    if not code:
        raise ValueError("Paste the code from the sign-in page.")
    token = _exchange(flow, code, live["verifier"])
    _FLOWS_LIVE.pop(flow_id, None)
    from core import credentials
    return credentials.store_oauth_token(live["provider"], token)


def _exchange(flow: ProviderFlow, code: str, verifier: str) -> dict:
    """POST the token endpoint; return the token payload
    ({access_token, refresh_token?, expires_at?}). Raises ValueError on failure."""
    import json
    import urllib.error
    import urllib.request
    body = json.dumps({
        "grant_type": "authorization_code",
        "client_id": flow.client_id,
        "code": code,
        "redirect_uri": flow.redirect_uri,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(flow.token_url, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode()[:200]
        except Exception:  # noqa: BLE001
            pass
        raise ValueError(f"Sign-in exchange was rejected ({e.code}). {detail}".strip())
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Could not complete sign-in ({type(e).__name__}).")
    access = data.get("access_token")
    if not access:
        raise ValueError("Sign-in didn't return an access token.")
    out = {"access_token": access}
    if data.get("refresh_token"):
        out["refresh_token"] = data["refresh_token"]
    if data.get("expires_in"):
        try:
            out["expires_at"] = int(time.time()) + int(data["expires_in"])
        except (TypeError, ValueError):
            pass
    return out
