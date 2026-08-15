"""Settings must describe the credential it would actually send.

Live, 2026-08-15: an OOD session 401'd on every turn ("OAuth access token has
been revoked") while Settings → Agent showed a green dot and "expires
7/30/2026" — a date sixteen days in the past. Three credentials were in play:

  tier 1  $ABA_HOME/oauth.json      expired 07-31, refresh 400s (17 times)
  tier 2  $CLAUDE_CODE_OAUTH_TOKEN  the one actually sent — and revoked
  tier 3  ~/.claude/.credentials.json   not present in that container at all

`has_oauth` came from the resolver (which fell through to tier 2), while
`oauth_source`/`oauth_expires_at` came from tier 1 whenever tier 1 had an
access_token. The page therefore described a credential nobody was sending,
dated by a clock nobody was reading, and called it valid.

These guards fix the SHAPE, not the incident: one resolver answers, and the
provider's own verdict is what falsifies `valid` — because the pasted-token
tier has no local expiry, so presence is otherwise unfalsifiable.
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.credentials as creds          # noqa: E402
import core.llm as llm                    # noqa: E402
from core.runtime.llm_errors import friendly_error   # noqa: E402

PASTED = "sk-ant-oat01-" + "p" * 40
STORED = "sk-ant-oat01-" + "s" * 40


class Rejected(Exception):
    """Shaped like the SDK's AuthenticationError: a status_code plus the
    provider's own words. The fake must carry what the real one carries —
    'revoked' and 'expired' are different diagnoses with different fixes."""
    status_code = 401

    def __init__(self, msg="Error code: 401 - {'type': 'error', 'error': "
                           "{'type': 'authentication_error', 'message': "
                           "'OAuth access token has been revoked.'}}"):
        super().__init__(msg)


@pytest.fixture
def live_shape(monkeypatch, tmp_path):
    """The exact three-tier situation above: a dead tier-1 store whose refresh
    really 400s, and a pasted tier-2 token that resolves."""
    store = tmp_path / "oauth.json"
    store.write_text(json.dumps({"access_token": STORED,
                                 "refresh_token": "rt-dead",
                                 "expires_at": time.time() - 15.7 * 86400}))
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setattr(llm, "_oauth_store_path", lambda: str(store))
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", PASTED)
    monkeypatch.setenv("ABA_LLM_CREDENTIAL", "oauth_cc")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(creds, "read", lambda: {})

    # The refresh fails the way it fails in production — HTTP 400 out of
    # urlopen — not by a stubbed `_refresh_oauth` that politely returns None.
    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, None)
    monkeypatch.setattr(urllib.request, "urlopen", boom)

    llm._refresh_failure.update(at=None, error=None)
    llm._CLI_CRED_CACHE.update(tok=None, exp=None, until=0.0)
    creds.clear_auth_rejection()
    yield tmp_path
    llm._refresh_failure.update(at=None, error=None)
    creds.clear_auth_rejection()


def test_status_names_the_credential_that_would_be_sent(live_shape):
    """THE regression. The resolver falls through to the pasted token, so the
    reported source and expiry must be the pasted token's — not the dead
    store's stale date."""
    st = creds.status("anthropic")

    assert st["has_oauth"] is True
    assert st["oauth_source"] == "pasted_token"
    # A pasted setup-token has no local expiry. Reporting one — any one — is the
    # lie: it was the dead store's, rendered beside a green dot.
    assert st["oauth_expires_at"] is None

    # And the resolver agrees with the page about WHO answers.
    token, source, exp = llm._oauth_bearer_detail()
    assert (token, source, exp) == (PASTED, "pasted_token", None)


def test_dead_store_says_so_even_while_chat_still_works(live_shape):
    """The fallthrough keeps chat alive, which is exactly why the dead store has
    to be announced: 17 identical refresh failures were logged and the UI never
    mentioned one."""
    creds.status("anthropic")                       # drives the refresh attempt
    st = creds.status("anthropic")

    assert st["oauth_refresh_failed"], "a store that cannot renew must be reported"
    assert "400" in st["oauth_refresh_failed"]["error"]
    assert st["oauth_refresh_failed"]["at"] > 0


def test_provider_rejection_falsifies_valid(live_shape):
    """`valid` for a pasted token is unfalsifiable locally — no expiry, no
    checksum. The provider's 401 is the only thing that can disprove it."""
    assert creds.status("anthropic")["valid"] is True       # before the verdict

    rec = creds.note_auth_failure(Rejected())
    assert rec and rec["reason"] == "revoked"               # not "likely expired"
    assert rec["source"] == "pasted_token"

    st = creds.status("anthropic")
    assert st["valid"] is False
    assert st["has_oauth"] is False
    assert st["rejected"]["reason"] == "revoked"


def test_rejection_is_keyed_to_the_credential_not_the_session(live_shape, monkeypatch):
    """The other side: pasting a NEW token must clear the red, and re-pasting the
    SAME dead one must not. A session-wide latch would force a restart after any
    401; a no-op would let a revoked token read green forever."""
    creds.note_auth_failure(Rejected())
    assert creds.status("anthropic")["valid"] is False

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat01-" + "n" * 40)
    assert creds.status("anthropic")["valid"] is True       # a new credential, a new verdict

    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", PASTED)
    assert creds.status("anthropic")["valid"] is False      # the dead one is still dead


@pytest.mark.parametrize("exc", [
    RuntimeError("Error code: 429 - rate_limit_error"),
    RuntimeError("Error code: 529 - overloaded_error"),
    RuntimeError("Connection error."),
])
def test_non_auth_failures_leave_the_verdict_alone(live_shape, exc):
    """A rate limit says nothing about the credential. Marking it dead here
    would strand a working session behind a Connect button."""
    assert creds.note_auth_failure(exc) is None
    assert creds.status("anthropic")["valid"] is True


def test_status_never_carries_the_secret(live_shape):
    """Whatever else changes, the payload the browser gets must not contain a
    token. The fingerprint exists so rejections can be compared without one."""
    blob = json.dumps(creds.status("anthropic"))
    assert PASTED not in blob and STORED not in blob
    assert creds.fingerprint(PASTED) != creds.fingerprint(STORED)


# ── the remedy has to match the credential in play ───────────────────────────

def test_pasted_token_401_does_not_send_the_user_to_the_cli(live_shape):
    """The old message told everyone to "run `claude` to refresh
    ~/.claude/.credentials.json". For this session that file did not exist (the
    container's HOME is the group runtime dir) and could not have helped if it
    had — the credential lives in Settings. Naming it is the defect; asserting
    only that *some* message appears would not catch it."""
    msg = friendly_error(Rejected())

    assert "~/.claude" not in msg
    assert "claude setup-token" in msg or "Settings" in msg
    assert "revoked" in msg                 # the provider's word, not our guess
    assert "expired" not in msg.lower()


def test_cli_tier_still_gets_the_cli_remedy(live_shape, monkeypatch):
    """Degenerate case in the other direction: where `run claude` IS the fix,
    it must still be offered. A blanket rewrite would have broken personal
    installs, whose bearer really is the CLI file."""
    monkeypatch.setattr(llm, "_oauth_bearer_detail",
                        lambda: ("cli-tok", "claude_cli", time.time() + 3600))
    msg = friendly_error(Rejected())
    assert "~/.claude/.credentials.json" in msg


def test_auth_error_is_not_masked_as_no_provider(live_shape):
    """friendly_error short-circuits to "no provider is connected" when nothing
    is configured. Recording the rejection makes that check False, so without
    the ordering guard the specific diagnosis disappears the moment it becomes
    true."""
    creds.note_auth_failure(Rejected())
    msg = friendly_error(Rejected())
    assert "No model provider is connected yet" not in msg
    assert "revoked" in msg
