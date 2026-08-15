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

from core import config

# Same patterns the helper validates against (install/.../auth.py).
_API_KEY_RE = re.compile(r"^sk-ant-[a-zA-Z0-9_\-]{16,}$")
_OAUTH_TOKEN_RE = re.compile(r"^sk-ant-oat[a-zA-Z0-9_\-]{16,}$")
# OpenAI keys: legacy `sk-…` and project `sk-proj-…` (proj- is [A-Za-z0-9_-]).
_OPENAI_KEY_RE = re.compile(r"^sk-[A-Za-z0-9_\-]{20,}$")

_CRED_KEYS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_FLOW",
              "CLAUDE_CODE_OAUTH_TOKEN", "ABA_LLM_CREDENTIAL",
              "OPENAI_API_KEY", "ABA_OPENAI_API_KEY", "ABA_OPENAI_BASE_URL",
              "OPENAI_AUTH_FLOW", "OPENAI_OAUTH_TOKEN")

_OPENAI_DEFAULT_BASE = "https://api.openai.com/v1"


def _config_env_path() -> Path:
    return config.aba_home() / "config.env"


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


def status(provider: str = "anthropic") -> dict:
    """Current credential state for a provider (Settings → Agent). Never echoes the
    secret — only a 4-char suffix, the mode, and (for refreshable OAuth) expiry."""
    if provider == "openai":
        return _openai_status()
    cfg = read()
    api_key = (os.environ.get("ANTHROPIC_API_KEY") or cfg.get("ANTHROPIC_API_KEY") or "")
    if not api_key:
        try:
            from core.config import API_KEY as _frozen
            api_key = _frozen or ""
        except Exception:  # noqa: BLE001
            api_key = ""
    try:                                   # reflect the oauth_cc auto-default, not just the raw env
        from core.llm import _credential_mode
        mode = _credential_mode()
    except Exception:  # noqa: BLE001
        mode = (config.settings.llm_credential.get() or cfg.get("ABA_LLM_CREDENTIAL")
                or "apikey").lower()
    pasted = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
              or cfg.get("CLAUDE_CODE_OAUTH_TOKEN") or "")
    # Use the SAME resolver a turn uses (oauth.json → CLAUDE_CODE_OAUTH_TOKEN →
    # ~/.claude/.credentials.json), and take the SOURCE AND EXPIRY FROM THAT SAME
    # ANSWER. Reading `has_oauth` from the resolver but `source`/`expires_at` from
    # the tier-1 store is how this page reported, live on 2026-08-15, a green dot
    # beside an expiry sixteen days past — while every turn 401'd on a third
    # credential (a pasted token) the page never named. One resolver, one answer.
    oauth_active = False
    expires_at = None
    source = None
    token = None
    refresh_failure = None
    try:
        from core.llm import _oauth_bearer_detail, oauth_refresh_failure
        token, source, expires_at = _oauth_bearer_detail()
        oauth_active = bool(token)
        refresh_failure = oauth_refresh_failure()
    except Exception:  # noqa: BLE001
        token = pasted or None
        oauth_active = bool(pasted)
        source = "pasted_token" if pasted else None
    has_api_key = bool(api_key)
    # Presence is not usability. A pasted `setup-token` has no local expiry at
    # all, and a refreshable store's expiry says nothing about REVOCATION — so
    # the only honest falsifier is the provider's own verdict, recorded by
    # note_auth_failure() when a turn is rejected. `valid` stays True until the
    # credential we would actually send is the one that got turned away.
    rejected = _rejection_for(token) if token else None
    if rejected:
        oauth_active = False
    api_rejected = _rejection_for(api_key) if api_key else None
    st = {
        "provider": "anthropic",
        "mode": mode,
        "has_api_key": has_api_key,
        "key_suffix": api_key[-4:] if api_key else None,
        "has_oauth": oauth_active,
        "oauth_source": source,
        "oauth_expires_at": expires_at,
        # The UI shows status+Change when valid, and the input field immediately
        # when not. Validity = a credential is present, not locally expired, and
        # not one the provider has already rejected this session.
        "valid": (has_api_key and not api_rejected) or oauth_active,
    }
    # Why it is not valid / what is quietly broken. Absent keys mean "nothing to
    # report" — the UI shows a plain green state only when both are absent.
    if rejected or api_rejected:
        st["rejected"] = rejected or api_rejected
    if refresh_failure:
        # The store is dead and cannot renew itself. Chat may still work (the
        # chain falls through to another tier) — which is precisely why this has
        # to be said out loud rather than inferred from a working session.
        st["oauth_refresh_failed"] = refresh_failure
    return st


# Credentials the PROVIDER has rejected this process, keyed by fingerprint. Kept
# in memory on purpose: a restart re-tries, and a stale file claiming a
# now-rotated credential is dead would be a new lie of the same species.
_rejections: dict[str, dict] = {}


def fingerprint(secret: str) -> str:
    """Stable, non-reversible id for a credential — safe to log and compare.
    Never put a token itself in a status payload, a log line, or an assertion."""
    import hashlib
    return hashlib.sha256((secret or "").encode()).hexdigest()[:12]


def _rejection_for(secret: str | None):
    return _rejections.get(fingerprint(secret)) if secret else None


def any_rejected() -> bool:
    """Has the provider turned down a credential this session? Distinguishes
    "nothing is connected" from "what is connected was refused" — two states
    that any_configured() reports identically and that need opposite advice."""
    return bool(_rejections)


def note_auth_failure(exc: Exception) -> dict | None:
    """Record that the credential we WOULD send was rejected by the provider.

    Called on a turn failure (guide.py). Only 401/authentication_error counts —
    a rate limit or an overload says nothing about the credential. Returns the
    recorded entry, or None if this exception was not an auth rejection.

    This is what makes `valid` falsifiable for the pasted-token tier, which has
    no local expiry to check: without it, "valid" degrades to "a string exists"
    and a revoked token reads exactly like a working one."""
    import time
    from core.runtime.llm_errors import is_auth_error
    s = str(exc).lower()
    if not is_auth_error(exc):
        return None
    try:
        from core.llm import _oauth_bearer_detail
        token, source, _ = _oauth_bearer_detail()
    except Exception:  # noqa: BLE001
        token, source = None, None
    if not token:
        token = (os.environ.get("ANTHROPIC_API_KEY") or read().get("ANTHROPIC_API_KEY") or "")
        source = "api_key" if token else source
    if not token:
        return None
    # "revoked" and "expired" call for different fixes and the provider says
    # which; guessing "likely expired" at a revoked token sent one operator to
    # refresh a file the session does not even read.
    reason = "revoked" if "revoked" in s else ("expired" if "expired" in s else "rejected")
    entry = {"reason": reason, "source": source, "at": time.time(),
             "fingerprint": fingerprint(token)}
    _rejections[entry["fingerprint"]] = entry
    return entry


def clear_auth_rejection(secret: str | None = None) -> None:
    """Forget a rejection — for a specific credential, or all of them. Called
    when a credential is saved: the new one deserves its own verdict."""
    if secret:
        _rejections.pop(fingerprint(secret), None)
    else:
        _rejections.clear()


def any_configured() -> dict:
    """Is ANY model provider usable right now? Drives the app's first-run gate and
    the credential-less-start flow (the backend serves without a credential; chat is
    gated until this is True — lazy_env_init.md). Returns
    {"configured": bool, "provider": <first valid provider or None>}."""
    for prov in ("anthropic", "openai"):
        try:
            if status(prov).get("valid"):
                return {"configured": True, "provider": prov}
        except Exception:  # noqa: BLE001
            continue
    return {"configured": False, "provider": None}


def _claude_cli_expiry():
    """expiresAt (→ unix seconds) from ~/.claude/.credentials.json, or None."""
    import json
    p = os.path.expanduser("~/.claude/.credentials.json")
    try:
        oa = (json.load(open(p)).get("claudeAiOauth") or {})
        e = oa.get("expiresAt")
        if isinstance(e, (int, float)):
            return int(e / 1000)            # stored as ms
    except Exception:  # noqa: BLE001
        pass
    return None


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
    clear_auth_rejection(key)      # a freshly saved credential gets its own verdict
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
    clear_auth_rejection(token)    # a freshly saved credential gets its own verdict
    return status()


def _detect_kind(cred: str) -> str | None:
    """'oauth' for sk-ant-oat…, 'apikey' for sk-ant-…, else None. OAuth tokens
    also start with sk-ant-, so the oat prefix must be checked first."""
    if _OAUTH_TOKEN_RE.match(cred):
        return "oauth"
    if _API_KEY_RE.match(cred):
        return "apikey"
    return None


def _sanitize_credential(cred: str) -> str:
    """Rescue a mangled paste: drop whitespace (incl. a newline from a terminal-
    wrapped copy, e.g. `more .credentials.json`) and obvious paste punctuation
    (surrounding quotes, trailing comma). sk-ant keys/tokens contain none of these,
    so this only ever helps."""
    return re.sub(r"\s+", "", (cred or "")).strip("'\",;")


def _test_credential(kind: str, cred: str) -> tuple[bool, str | None]:
    """Make a 1-token Haiku call to confirm Anthropic accepts the credential
    BEFORE we persist. (True, None) on success; (False, message) otherwise."""
    import anthropic
    try:
        from core.llm import _httpx_sync_client
        http = _httpx_sync_client()
    except Exception:  # noqa: BLE001
        http = None
    kw = {"http_client": http} if http is not None else {}
    try:
        client = (anthropic.Anthropic(auth_token=cred, **kw) if kind == "oauth"
                  else anthropic.Anthropic(api_key=cred, **kw))
        client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=1,
                               messages=[{"role": "user", "content": "hi"}])
        return True, None
    except anthropic.AuthenticationError:
        return False, "Anthropic rejected the credential — authentication failed."
    except anthropic.PermissionDeniedError:
        return False, "Anthropic denied this credential — permission denied."
    except Exception as e:  # noqa: BLE001
        return False, f"Could not verify the credential ({type(e).__name__})."


def set_credential(cred: str, provider: str = "anthropic") -> dict:
    """Single entry point for Settings → Agent: detect key vs token, VERIFY it with
    the provider, then persist + go live. Raises ValueError (→ HTTP 400) on bad
    format or rejection — nothing is written unless the credential actually works."""
    if provider == "openai":
        return _openai_set_api_key(cred)
    cred = _sanitize_credential(cred)      # rescue terminal-wrapped / quoted pastes
    kind = _detect_kind(cred)
    if kind is None:
        raise ValueError("That doesn't look like an Anthropic API key or OAuth token "
                         "(expected sk-ant-… or sk-ant-oat…).")
    ok, err = _test_credential(kind, cred)
    if not ok:
        raise ValueError(err or "The credential was rejected.")
    return set_oauth_token(cred) if kind == "oauth" else set_api_key(cred)


# ── OpenAI provider ──────────────────────────────────────────────────────────

def _openai_status() -> dict:
    """OpenAI credential state (Settings → Agent, provider=OpenAI). Mirrors the
    Anthropic status shape so the UI is provider-agnostic. Subscription (Codex
    OAuth) fields are filled by the oauth roundtrip; Tier-1 is the API-key path."""
    cfg = read()
    key = (os.environ.get("OPENAI_API_KEY") or config.settings.openai_api_key.get()
           or cfg.get("OPENAI_API_KEY") or "")
    oauth = (os.environ.get("OPENAI_OAUTH_TOKEN") or cfg.get("OPENAI_OAUTH_TOKEN") or "")
    has_key, has_oauth = bool(key), bool(oauth)
    return {
        "provider": "openai",
        "mode": "subscription" if has_oauth else "apikey",
        "has_api_key": has_key,
        "key_suffix": key[-4:] if key else None,
        "has_oauth": has_oauth,
        "oauth_source": "codex_subscription" if has_oauth else None,
        "oauth_expires_at": None,
        "valid": has_key or has_oauth,
    }


def _test_openai_credential(key: str) -> tuple[bool, str | None]:
    """Confirm OpenAI accepts the key BEFORE persisting — a cheap `models.list()`
    (auth-only, no tokens spent). (True, None) on success; (False, message) else."""
    try:
        import openai
    except Exception:  # noqa: BLE001
        return False, "The openai SDK isn't installed in this environment."
    try:
        client = openai.OpenAI(api_key=key, base_url=_OPENAI_DEFAULT_BASE)
        client.models.list()
        return True, None
    except openai.AuthenticationError:
        return False, "OpenAI rejected the key — authentication failed."
    except openai.PermissionDeniedError:
        return False, "OpenAI denied this key — permission denied."
    except Exception as e:  # noqa: BLE001
        return False, f"Could not verify the key ({type(e).__name__})."


def store_oauth_token(provider: str, token: dict) -> dict:
    """Persist a subscription OAuth token (from core.oauth) for a provider + go live.
    `token` = {access_token, refresh_token?, expires_at?}. Returns status(provider).

    Anthropic: stored as the oauth_cc bearer (same path as a pasted Claude.ai token).
    OpenAI: stored as OPENAI_OAUTH_TOKEN in subscription mode (the runtime's
    subscription-bearer path is a follow-up — see misc/model_providers.md)."""
    access = (token or {}).get("access_token")
    if not access:
        raise ValueError("No access token to store.")
    entries = read()
    if provider == "openai":
        # Subscription (Codex/ChatGPT): the access token is a Bearer used against
        # the ChatGPT WHAM backend, with a ChatGPT-Account-Id header from the JWT.
        acct = (token or {}).get("account_id") or ""
        base = "https://chatgpt.com/backend-api/codex"   # Responses backend (not /wham)
        entries["OPENAI_OAUTH_TOKEN"] = access
        entries["OPENAI_AUTH_FLOW"] = "subscription"
        entries["ABA_OPENAI_BASE_URL"] = base
        if acct:
            entries["ABA_OPENAI_ACCOUNT_ID"] = acct
        if (token or {}).get("refresh_token"):
            entries["OPENAI_OAUTH_REFRESH"] = token["refresh_token"]
        entries.pop("OPENAI_API_KEY", None)      # subscription supersedes a stored key
        write(entries)
        os.environ["OPENAI_OAUTH_TOKEN"] = access
        os.environ["OPENAI_AUTH_FLOW"] = "subscription"
        os.environ["ABA_OPENAI_BASE_URL"] = base
        if acct:
            os.environ["ABA_OPENAI_ACCOUNT_ID"] = acct
        os.environ.pop("OPENAI_API_KEY", None)
        return _openai_status()
    # anthropic — reuse the oauth_cc bearer path
    entries["CLAUDE_CODE_OAUTH_TOKEN"] = access
    entries["ABA_LLM_CREDENTIAL"] = "oauth_cc"
    entries["ANTHROPIC_AUTH_FLOW"] = "oauth"
    write(entries)
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = access
    os.environ["ABA_LLM_CREDENTIAL"] = "oauth_cc"
    # Persist the REFRESHABLE store ($ABA_HOME/oauth.json) so the short-lived
    # subscription access token auto-refreshes near expiry (_oauth_bearer priority 1)
    # instead of lapsing into a 401 the next turn. Without this the token drops only
    # into the static CLAUDE_CODE_OAUTH_TOKEN env (priority 2, NOT refreshable) — the
    # bug behind "ping green but chat 401s once the token expires". Store whatever the
    # exchange gave us; the bearer handles refresh / clean-expiry from expires_at.
    try:
        from core.llm import _save_oauth_store
        st = {"access_token": access}
        if (token or {}).get("refresh_token"):
            st["refresh_token"] = token["refresh_token"]
        if (token or {}).get("expires_at"):
            st["expires_at"] = token["expires_at"]
        _save_oauth_store(st)
    except Exception:  # noqa: BLE001 — never let store persistence break sign-in
        pass
    _clear_llm_client_cache()
    # A fresh sign-in also revives the refresh path; drop any recorded verdict on
    # the credential we just replaced so status() judges the new one on its own.
    clear_auth_rejection()
    try:
        from core import llm as _llm
        _llm._refresh_failure.update(at=None, error=None)
    except Exception:  # noqa: BLE001
        pass
    return status("anthropic")


def _openai_set_api_key(key: str) -> dict:
    """Verify + persist an OpenAI API key and point the OpenAI runtime at
    api.openai.com. Sets OPENAI_API_KEY (SDK) + ABA_OPENAI_API_KEY/BASE_URL (what
    OpenAICompatibleRuntime reads) so the change is live next turn."""
    key = _sanitize_credential(key)
    if not _OPENAI_KEY_RE.match(key):
        raise ValueError("That doesn't look like an OpenAI API key (expected sk-…).")
    ok, err = _test_openai_credential(key)
    if not ok:
        raise ValueError(err or "The key was rejected.")
    entries = read()
    entries["OPENAI_API_KEY"] = key
    entries["ABA_OPENAI_API_KEY"] = key
    entries["ABA_OPENAI_BASE_URL"] = _OPENAI_DEFAULT_BASE
    entries["OPENAI_AUTH_FLOW"] = "api_key"
    entries.pop("OPENAI_OAUTH_TOKEN", None)
    write(entries)
    os.environ["OPENAI_API_KEY"] = key
    os.environ["ABA_OPENAI_API_KEY"] = key
    os.environ["ABA_OPENAI_BASE_URL"] = _OPENAI_DEFAULT_BASE
    os.environ.pop("OPENAI_OAUTH_TOKEN", None)
    return _openai_status()
