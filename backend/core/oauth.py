"""Subscription sign-in (OAuth PKCE) for Settings → Agent → Subscription.

Connect a plan instead of pasting an API key — Anthropic (Claude.ai) or OpenAI
(ChatGPT/Codex). Two flow shapes, because the providers differ:

  • paste   (Anthropic): the authorize redirect SHOWS a code; the user copies it
             back and we exchange it. Works local AND behind a remote/OOD proxy.
  • callback (OpenAI/Codex): the authorize redirect goes to a fixed
             http://localhost:1455/auth/callback; we bind that port, capture the
             code automatically, and exchange. Requires the browser to reach the
             ABA host on :1455 (local deploy, or tunnel :1455 alongside :8000).

Both use PKCE S256. Gated behind ABA_SUBSCRIPTION_OAUTH. Constants below mirror the
public CLI OAuth clients (openai/codex, Claude Code) — real values, but the auth
backends are not official APIs and can change. See misc/model_providers.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

from core import config


def enabled(provider: str = "anthropic") -> bool:
    """Is subscription sign-in offered for `provider` on THIS deployment?

    Gated by ABA_SUBSCRIPTION_OAUTH, read as a CAPABILITY LEVEL — because the two flow
    shapes have different reachability needs (see the module docstring):

      off (default / 0 / no / false)  — no subscription sign-in anywhere.
      paste                           — PASTE-mode flows only (Anthropic). Safe behind a
                                        reverse proxy / remote session (OOD): the code is
                                        shown for the user to copy, no local listener.
      1 / true / yes / on / all       — ALL flows, incl. CALLBACK-mode (OpenAI, which binds
                                        localhost:1455). Only a LOCAL desktop — browser on the
                                        ABA host — can complete that; behind OOD it never can.

    Per-provider + mode-aware so a proxied deploy can offer Anthropic-subscription WITHOUT
    dangling a broken OpenAI-subscription button. (The old gate was a single global bool, so
    turning subscription on for Anthropic also advertised OpenAI's unreachable callback.)"""
    raw = (config.settings.subscription_oauth.get() or "").strip().lower()
    if raw in ("", "0", "off", "no", "false"):
        return False
    flow = _FLOWS.get(provider)
    if not flow:
        return False
    full = raw in ("1", "true", "yes", "on", "all")     # local desktop: callback reachable
    if flow.mode == "callback":
        return full                                     # callback needs a reachable localhost port
    return full or raw in ("paste", "paste-only", "proxy")   # paste works at any enabled level


@dataclass(frozen=True)
class ProviderFlow:
    provider: str
    client_id: str
    authorize_url: str
    token_url: str
    redirect_uri: str
    scopes: str
    mode: str = "paste"                 # "paste" | "callback"
    callback_port: int = 0              # for mode == "callback"
    extra_authorize: dict = field(default_factory=dict)


_FLOWS: dict[str, ProviderFlow] = {
    # Claude Code's public OAuth client — paste-code redirect (shows the code).
    "anthropic": ProviderFlow(
        provider="anthropic",
        client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        authorize_url="https://claude.ai/oauth/authorize",
        token_url="https://console.anthropic.com/v1/oauth/token",
        redirect_uri="https://console.anthropic.com/oauth/code/callback",
        scopes="org:create_api_key user:profile user:inference",
        mode="paste",
    ),
    # OpenAI Codex CLI's public OAuth client — localhost:1455 callback. Backend =
    # ChatGPT WHAM. client_id + endpoints confirmed from the openai/codex CLI.
    "openai": ProviderFlow(
        provider="openai",
        client_id=config.settings.openai_oauth_client_id.get(),
        authorize_url="https://auth.openai.com/oauth/authorize",
        token_url="https://auth.openai.com/oauth/token",
        redirect_uri="http://localhost:1455/auth/callback",
        scopes="openid profile email offline_access",
        mode="callback",
        callback_port=1455,
        extra_authorize={
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "originator": "codex_cli",
        },
    ),
}

# In-memory, short-lived flow store (flow_id → dict). A flow is a few-minute
# interaction; a process restart just means "start over".
_FLOWS_LIVE: dict[str, dict] = {}
_FLOW_TTL_S = 900  # 15 min


def _pkce() -> tuple[str, str]:
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _gc(now: float) -> None:
    for k in [k for k, v in _FLOWS_LIVE.items() if now - v["created"] > _FLOW_TTL_S]:
        _close_listener(_FLOWS_LIVE.pop(k, None))


def _close_listener(flow: dict | None) -> None:
    srv = (flow or {}).get("_server")
    if srv is not None:
        # shutdown() stops serve_forever; server_close() RELEASES the socket/port
        # (without it the port stays bound → next sign-in "address already in use").
        try:
            srv.shutdown()
        except Exception:  # noqa: BLE001
            pass
        try:
            srv.server_close()
        except Exception:  # noqa: BLE001
            pass
        flow["_server"] = None


def start(provider: str) -> dict:
    """Begin a subscription sign-in. Returns {flow_id, authorize_url, mode}. For a
    callback flow, also binds the local callback port to capture the code."""
    flow = _FLOWS.get(provider)
    if not flow:
        raise ValueError(f"No subscription sign-in for provider {provider!r}.")
    if not enabled(provider):
        if flow.mode == "callback":
            raise ValueError(
                f"{provider} subscription sign-in uses a localhost:{flow.callback_port} "
                "callback the browser must reach on the ABA host — not possible in a "
                "remote/OOD session. Paste an API key instead, or set "
                "ABA_SUBSCRIPTION_OAUTH=all on a local desktop deployment.")
        raise ValueError("Subscription sign-in isn't enabled on this deployment "
                         "(set ABA_SUBSCRIPTION_OAUTH=paste to allow it).")
    now = time.time(); _gc(now)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    flow_id = secrets.token_urlsafe(18)
    live = {"provider": provider, "verifier": verifier, "state": state,
            "created": now, "code": None, "error": None, "_server": None}
    if flow.mode == "callback":
        _start_callback_listener(flow, live)   # binds flow.callback_port
    _FLOWS_LIVE[flow_id] = live
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
    return {"flow_id": flow_id, "mode": flow.mode,
            "authorize_url": flow.authorize_url + "?" + urlencode(params)}


def _start_callback_listener(flow: ProviderFlow, live: dict) -> None:
    """Bind the provider's fixed localhost callback port and capture the first
    code/state it receives into `live`. Raises ValueError if the port can't bind."""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs

    # Retry-safe: a prior sign-in the user abandoned may still hold this port —
    # tear down any of OUR live listeners on it (a new attempt supersedes them)
    # before binding. + allow_reuse_address so a just-closed socket in TIME_WAIT
    # doesn't block the rebind.
    for fid in [k for k, v in _FLOWS_LIVE.items()
                if v is not live and (v.get("_server") is not None)
                and v.get("provider") == flow.provider]:
        _close_listener(_FLOWS_LIVE.pop(fid, None))

    expected_state = live["state"]

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = parse_qs(urlparse(self.path).query)
            code = (q.get("code") or [None])[0]
            st = (q.get("state") or [None])[0]
            err = (q.get("error") or [None])[0]
            if err:
                live["error"] = str(err)
            elif code and st == expected_state:
                live["code"] = code
            else:
                live["error"] = "state mismatch"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body style='font-family:sans-serif'>"
                             b"<h3>Signed in to ABA.</h3>"
                             b"You can close this tab and return to ABA.</body></html>")

        def log_message(self, *a):  # silence
            pass

    try:
        HTTPServer.allow_reuse_address = True
        srv = HTTPServer(("127.0.0.1", flow.callback_port), _H)
    except OSError as e:
        raise ValueError(
            f"Couldn't open the sign-in callback port {flow.callback_port} "
            f"({e.strerror}). Close whatever is using it (e.g. a running Codex CLI) "
            f"and try again, or use an API key instead.")
    live["_server"] = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()


def poll(flow_id: str) -> dict:
    """Callback-flow status. {state: pending|done|error, credential?, detail?}.
    On a captured code, exchanges + persists once, then reports done."""
    live = _FLOWS_LIVE.get(flow_id)
    if not live:
        return {"state": "error", "detail": "This sign-in expired — start it again."}
    if live.get("error"):
        _close_listener(live); _FLOWS_LIVE.pop(flow_id, None)
        return {"state": "error", "detail": f"Sign-in failed ({live['error']})."}
    if not live.get("code"):
        if time.time() - live["created"] > _FLOW_TTL_S:
            _close_listener(live); _FLOWS_LIVE.pop(flow_id, None)
            return {"state": "error", "detail": "Sign-in timed out — start it again."}
        return {"state": "pending"}
    try:
        cred = _finish(flow_id, live, live["code"])
        return {"state": "done", "credential": cred}
    except ValueError as e:
        return {"state": "error", "detail": str(e)}


def submit(flow_id: str, code: str) -> dict:
    """Paste-flow completion: exchange the pasted code + persist. Returns status."""
    live = _FLOWS_LIVE.get(flow_id)
    if not live:
        raise ValueError("This sign-in expired — start it again.")
    if time.time() - live["created"] > _FLOW_TTL_S:
        _close_listener(live); _FLOWS_LIVE.pop(flow_id, None)
        raise ValueError("This sign-in expired — start it again.")
    code = (code or "").strip()
    if "#" in code:                 # some redirects show `<code>#<state>`
        code = code.split("#", 1)[0]
    if not code:
        raise ValueError("Paste the code from the sign-in page.")
    return _finish(flow_id, live, code)


def _finish(flow_id: str, live: dict, code: str) -> dict:
    """Exchange the code, persist the token, tear down, return credential status."""
    flow = _FLOWS[live["provider"]]
    token = _exchange(flow, code, live["verifier"], live.get("state"))
    _close_listener(live)
    _FLOWS_LIVE.pop(flow_id, None)
    from core import credentials
    return credentials.store_oauth_token(live["provider"], token)


def _decode_jwt_claims(tok: str) -> dict:
    """Best-effort: decode a JWT payload WITHOUT verification (we only read claims
    like the ChatGPT account id; the token's validity is enforced by the API)."""
    try:
        payload = tok.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:  # noqa: BLE001
        return {}


def _chatgpt_account_id(token: dict) -> str | None:
    """Extract the ChatGPT-Account-Id the WHAM backend wants, from the id/access
    token JWT: `chatgpt_account_id`, a namespaced auth claim, or organizations[0]."""
    for tok in (token.get("id_token"), token.get("access_token")):
        if not tok:
            continue
        claims = _decode_jwt_claims(tok)
        if claims.get("chatgpt_account_id"):
            return claims["chatgpt_account_id"]
        auth = claims.get("https://api.openai.com/auth") or {}
        if isinstance(auth, dict):
            if auth.get("chatgpt_account_id"):
                return auth["chatgpt_account_id"]
            orgs = auth.get("organizations") or []
            if orgs and isinstance(orgs, list) and isinstance(orgs[0], dict) and orgs[0].get("id"):
                return orgs[0]["id"]
        orgs = claims.get("organizations") or []
        if orgs and isinstance(orgs, list) and isinstance(orgs[0], dict) and orgs[0].get("id"):
            return orgs[0]["id"]
    return None


# Anthropic's token endpoint sits behind Cloudflare, which 403s (error 1010) the
# default Python-urllib User-Agent — any non-default UA gets through. Matches the
# mac installer (install/core/helper/src/aba_installer/auth.py), the working reference.
_OAUTH_USER_AGENT = "aba (kharchenkolab/aba)"


def _exchange(flow: ProviderFlow, code: str, verifier: str, state: str | None = None) -> dict:
    """POST the token endpoint. Returns {access_token, refresh_token?, id_token?,
    expires_at?, account_id?}.

    Provider-specific, matching each endpoint's real client:
      - anthropic (console.anthropic.com): JSON body that MUST include `state` (it
        400s without it) + a non-default User-Agent (else Cloudflare 403s w/ 1010).
      - openai (auth.openai.com): the OAuth-standard form encoding (already working)."""
    import urllib.error
    import urllib.request
    fields = {
        "grant_type": "authorization_code",
        "client_id": flow.client_id,
        "code": code,
        "redirect_uri": flow.redirect_uri,
        "code_verifier": verifier,
    }
    if flow.provider == "anthropic":
        if state:
            fields["state"] = state          # required — the endpoint 400s without it
        body = json.dumps(fields).encode()
        headers = {"Content-Type": "application/json", "Accept": "application/json",
                   "User-Agent": _OAUTH_USER_AGENT}
    else:
        body = urlencode(fields).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded",
                   "User-Agent": _OAUTH_USER_AGENT}
    req = urllib.request.Request(flow.token_url, data=body, method="POST", headers=headers)
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
    out: dict = {"access_token": access}
    for k in ("refresh_token", "id_token"):
        if data.get(k):
            out[k] = data[k]
    if data.get("expires_in"):
        try:
            out["expires_at"] = int(time.time()) + int(data["expires_in"])
        except (TypeError, ValueError):
            pass
    acct = _chatgpt_account_id(out)
    if acct:
        out["account_id"] = acct
    return out
