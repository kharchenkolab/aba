"""Per-provider, mode-aware subscription-OAuth gating (core.oauth.enabled).

The two subscription flows have different reachability needs, so a single global
on/off gate was wrong: turning it on to offer Anthropic (a paste-code flow that works
behind any proxy) also advertised OpenAI (a localhost:1455 callback the browser can't
reach in a remote/OOD session). ABA_SUBSCRIPTION_OAUTH is now a capability LEVEL:
  off (default) · paste (paste-flows only) · 1|all (all flows, incl. callback).
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture(autouse=True)
def _restore_env():
    prev = os.environ.get("ABA_SUBSCRIPTION_OAUTH")
    yield
    if prev is None:
        os.environ.pop("ABA_SUBSCRIPTION_OAUTH", None)
    else:
        os.environ["ABA_SUBSCRIPTION_OAUTH"] = prev


def _set(level):
    if level is None:
        os.environ.pop("ABA_SUBSCRIPTION_OAUTH", None)
    else:
        os.environ["ABA_SUBSCRIPTION_OAUTH"] = level


def test_off_by_default_both_providers():
    from core import oauth
    _set(None)
    assert oauth.enabled("anthropic") is False
    assert oauth.enabled("openai") is False
    assert oauth.enabled() is False               # no-arg back-compat (defaults to anthropic)


@pytest.mark.parametrize("off", ["", "0", "off", "no", "false"])
def test_explicit_off_values(off):
    from core import oauth
    _set(off)
    assert oauth.enabled("anthropic") is False and oauth.enabled("openai") is False


def test_paste_level_enables_anthropic_only():
    """`paste` = proxy-safe: Anthropic (paste flow) on, OpenAI (localhost callback) off.
    This is the setting an OOD/remote deployment uses."""
    from core import oauth
    _set("paste")
    assert oauth.enabled("anthropic") is True
    assert oauth.enabled("openai") is False


@pytest.mark.parametrize("full", ["1", "true", "yes", "on", "all"])
def test_full_level_enables_both(full):
    """Legacy/local desktop level: the localhost callback is reachable, so both flows on.
    Preserves the pre-change meaning of ABA_SUBSCRIPTION_OAUTH=1."""
    from core import oauth
    _set(full)
    assert oauth.enabled("anthropic") is True
    assert oauth.enabled("openai") is True


def test_start_openai_under_paste_level_refuses_with_actionable_message():
    from core import oauth
    _set("paste")
    with pytest.raises(ValueError) as ei:
        oauth.start("openai")
    msg = str(ei.value).lower()
    assert "localhost" in msg and ("remote" in msg or "ood" in msg)   # explains WHY
    assert "api key" in msg or "all" in msg                            # offers the remedy


def test_start_anthropic_under_paste_level_works():
    from core import oauth
    os.environ["ABA_HOME"] = tempfile.mkdtemp(prefix="aba_oauth_pp_")
    _set("paste")
    out = oauth.start("anthropic")
    assert out["mode"] == "paste" and out["authorize_url"].startswith("https://claude.ai/oauth/authorize?")


def test_unknown_provider_never_enabled():
    from core import oauth
    _set("all")
    assert oauth.enabled("gemini") is False
