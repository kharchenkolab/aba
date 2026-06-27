"""Tier-0-tray model action + model state fetch.

The tray's Model submenu reads /api/auth/model on every poll and posts the
chosen id back on click. Both are pure HTTP wrappers with the helper-
offline error mapped to the same actionable message the other actions use.
"""
from __future__ import annotations
import json
import urllib.error

import pytest

from aba_installer.tray import actions, status_poll as sp


class _StubResp:
    def __init__(self, payload: dict, status: int = 200):
        self._b = json.dumps(payload).encode()
        self.status = status
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return self._b


# ─── status_poll.fetch_model_state ────────────────────────────────────
def test_fetch_model_state_decodes_response():
    payload = {"model": "claude-sonnet-4-6",
               "available": [{"id": "a", "label": "A", "note": ""},
                             {"id": "b", "label": "B", "note": ""}]}
    def fake(req, timeout=None):
        return _StubResp(payload)
    ms = sp.fetch_model_state(port=8765, urlopen=fake)
    assert ms.current == "claude-sonnet-4-6"
    assert len(ms.available) == 2
    assert ms.available[0]["id"] == "a"


def test_fetch_model_state_returns_empty_on_helper_offline():
    """Helper unreachable → no current, empty available. The menu code
    then renders an empty submenu rather than crashing."""
    def fake(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    ms = sp.fetch_model_state(port=8765, urlopen=fake)
    assert ms.current is None
    assert ms.available == []


# ─── actions.set_model ────────────────────────────────────────────────
def test_set_model_posts_to_endpoint_with_payload():
    seen: list = []
    def fake(req, timeout=None):
        seen.append({"url": req.full_url, "method": req.get_method(),
                     "data": req.data})
        return _StubResp({"ok": True, "model": "claude-sonnet-4-6",
                          "applied_on_next_turn": True})
    res = actions.set_model(model_id="claude-sonnet-4-6", port=8765, urlopen=fake)
    assert res.ok
    assert res.applied_on_next_turn is True
    # Right endpoint + method
    assert seen[0]["url"].endswith("/api/auth/model")
    assert seen[0]["method"] == "POST"
    # Model id is in the payload body
    body = json.loads(seen[0]["data"])
    assert body == {"model": "claude-sonnet-4-6"}


def test_set_model_no_apply_when_unchanged():
    def fake(req, timeout=None):
        return _StubResp({"ok": True, "model": "claude-opus-4-7",
                          "applied_on_next_turn": False})
    res = actions.set_model(model_id="claude-opus-4-7", port=8765, urlopen=fake)
    assert res.ok
    assert res.applied_on_next_turn is False


def test_set_model_returns_actionable_error_on_helper_offline():
    def fake(req, timeout=None):
        raise urllib.error.URLError("connection refused")
    res = actions.set_model(model_id="claude-haiku-4-5", port=8765, urlopen=fake)
    assert not res.ok
    assert "helper" in res.message.lower()
