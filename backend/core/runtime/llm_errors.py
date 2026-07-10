"""Anthropic API error classification + user-facing translation
(WU-5 extraction).

Two helpers extracted from guide.py:

  - `is_transient` — should the agent loop retry, or surface the error?
    True for 429 (rate limit), 5xx, 529 (overloaded), and connection /
    timeout failures.
  - `friendly_error` — turn an exception into a one-line message
    suitable for the chat error pill. Special-cases OAuth token
    expiry / rejection since those have a concrete user action
    ("Run `claude` to refresh").

Pure functions of (Exception) → bool / str. No I/O.
"""
from __future__ import annotations


# Transient API conditions worth retrying: 429 (rate limit), 5xx, 529
# (overloaded), and connection/timeouts. We match on the SDK's status code
# when present and fall back to a string check.
_TRANSIENT_TOKENS = ("overloaded", "rate_limit", "timeout", "connection",
                     "502", "503", "504", "529", "500")


def is_transient(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    if status in (408, 429, 500, 502, 503, 504, 529):
        return True
    return any(tok in str(exc).lower() for tok in _TRANSIENT_TOKENS)


def friendly_error(exc: Exception) -> str:
    s = str(exc).lower()
    # OAuth token expired/missing — surfaces both our pre-flight refusal
    # (OAuthTokenUnavailable) and a stale-token 401 that slipped through.
    from core.llm import OAuthTokenUnavailable, _credential_mode
    if isinstance(exc, OAuthTokenUnavailable):
        return ("Claude Code OAuth token is expired or missing. Run `claude` once "
                "to refresh it (no server restart needed), then retry.")
    # NO credential configured at all (as opposed to one that was rejected). The
    # Anthropic SDK raises TypeError from the client constructor — no status_code,
    # no "authentication_error" token — so it used to fall through to the generic
    # catch-all, whose "Please try again" is actively misleading: a retry can never
    # succeed. This is the first thing a fresh cluster/OOD session hits.
    if "could not resolve authentication method" in s:
        return ("No Anthropic credential is configured for this session. Add one in "
                "Settings → Model account, or paste a key on the OOD launch form "
                "(an API key sk-ant-api… or a Claude Code token sk-ant-oat…).")
    if (getattr(exc, "status_code", None) == 401
            or "authentication_error" in s or "invalid authentication credentials" in s):
        if _credential_mode() == "oauth_cc":
            return ("Claude Code OAuth token rejected (likely expired mid-session). "
                    "Run `claude` once to refresh ~/.claude/.credentials.json, then retry.")
        return ("The model rejected our credentials (401). Update the key in "
                "Settings → Model account (on a local install, ANTHROPIC_API_KEY in .env).")
    if "overloaded" in s or getattr(exc, "status_code", None) == 529:
        return ("The model is overloaded right now and didn't respond after a "
                "few retries. Please try again in a moment.")
    if "rate_limit" in s or getattr(exc, "status_code", None) == 429:
        return "Hit the model's rate limit. Please wait a moment and try again."
    if "timeout" in s or "connection" in s:
        return "Lost the connection to the model. Please try again."
    return "Something went wrong talking to the model. Please try again."
