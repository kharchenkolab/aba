"""friendly_error — a session with NO credential must say so, not "try again".

Live friction (CBE-next OOD pilot, 2026-07-09): a fresh OOD session launched
without pasting a key. The first chat turn came back as the generic pill
"Something went wrong talking to the model. Please try again." — which is both
uninformative and wrong, since no retry can ever succeed. The real cause was
sitting in the SSE event's `detail` the whole time:

    TypeError: Could not resolve authentication method. Expected one of
    api_key, auth_token, or credentials to be set.

The Anthropic SDK raises that from the CLIENT CONSTRUCTOR, so it carries no
`status_code` and none of the "authentication_error" tokens the 401 branch
matches — it fell straight through to the catch-all. This is the *first* thing
a new cluster/OOD deployment hits, so it gets its own branch and its own test.

Distinguishes the two credential failures:
  - none configured      -> TypeError from the constructor (this test)
  - configured, rejected -> 401 / authentication_error (the branch below it)
"""
import pytest

from core.runtime.llm_errors import friendly_error, is_transient


class _Status(Exception):
    """An SDK-shaped error carrying a status_code."""
    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.status_code = status_code


def _sdk_no_auth() -> TypeError:
    """Verbatim shape of what anthropic.AsyncAnthropic() raises with no key."""
    return TypeError(
        "Could not resolve authentication method. Expected one of api_key, "
        "auth_token, or credentials to be set. Or for one of the `X-Api-Key` "
        "or `Authorization` headers to be explicitly omitted"
    )


@pytest.fixture(autouse=True)
def _provider_connected(monkeypatch):
    """`friendly_error` FIRST short-circuits to a 'connect a provider' message when NO
    credential is configured — and `credentials.any_configured()` sniffs the AMBIENT
    environment, so on a clean machine (no key/token) that branch would swallow every
    error and these downstream-branch assertions would fail non-deterministically. Pin a
    connected provider here (same reason the 401 test pins `_credential_mode`); the
    short-circuit itself is covered by test_no_provider_connected_short_circuits."""
    from core import credentials
    monkeypatch.setattr(credentials, "any_configured",
                        lambda: {"configured": True, "provider": "anthropic"})


def test_no_provider_connected_short_circuits(monkeypatch):
    """With NO provider connected, ANY model error resolves to the actionable
    connect-a-provider message — not a raw 401 or the generic pill."""
    from core import credentials
    monkeypatch.setattr(credentials, "any_configured", lambda: {"configured": False})
    msg = friendly_error(RuntimeError("kaboom"))
    assert "Something went wrong" not in msg
    assert "provider" in msg.lower() and "Settings" in msg


def test_missing_credential_is_actionable_not_generic():
    msg = friendly_error(_sdk_no_auth())
    assert "Something went wrong" not in msg
    # must name the cause and where to fix it
    assert "credential" in msg.lower()
    assert "Settings" in msg or "launch form" in msg


def test_missing_credential_does_not_invite_a_retry():
    """"Please try again" is a lie here — the turn can never succeed unassisted."""
    assert "try again" not in friendly_error(_sdk_no_auth()).lower()


def test_missing_credential_is_not_transient():
    """A missing credential must not be retried by the agent loop."""
    assert is_transient(_sdk_no_auth()) is False


def test_rejected_credential_still_distinct_from_missing(monkeypatch):
    """A 401 means a credential EXISTS and was refused — a different remedy.

    Pin the mode: `friendly_error`'s 401 branch calls `_credential_mode()`, which
    sniffs the *ambient* environment (it auto-selects `oauth_cc` when a Claude Code
    store is present in $HOME). Without this the assertion passes or fails depending
    on whose machine runs the suite.
    """
    monkeypatch.setenv("ABA_LLM_CREDENTIAL", "apikey")
    missing = friendly_error(_sdk_no_auth())
    rejected = friendly_error(_Status("authentication_error", 401))
    assert missing != rejected
    assert "401" in rejected


def test_rejected_oauth_token_names_the_refresh_action(monkeypatch):
    monkeypatch.setenv("ABA_LLM_CREDENTIAL", "oauth_cc")
    assert "refresh" in friendly_error(_Status("authentication_error", 401)).lower()


def test_unknown_error_still_gets_the_generic_pill():
    """The catch-all must survive: only the *known* cause is special-cased."""
    assert "Something went wrong" in friendly_error(RuntimeError("kaboom"))


@pytest.mark.parametrize("msg", [
    "Could not resolve authentication method. Expected one of api_key...",
    "COULD NOT RESOLVE AUTHENTICATION METHOD",   # matching is case-insensitive
])
def test_match_is_case_insensitive(msg):
    assert "Something went wrong" not in friendly_error(TypeError(msg))
