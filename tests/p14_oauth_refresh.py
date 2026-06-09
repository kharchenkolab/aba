"""Tier-2 OAuth refresh: the backend mints a new access token from the stored
refresh_token when the old one is near/past expiry, instead of 401ing until a
restart. Token endpoint is mocked — no network, no real ~/.claude.

Run:
    .venv/bin/python tests/p14_oauth_refresh.py
"""
from __future__ import annotations
import os
import sys
import json
import time
import tempfile
import threading
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TMP = tempfile.mkdtemp(prefix="aba_oauth_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = str(Path(_TMP) / "x.db")
os.environ["ABA_HOME"] = _TMP
os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
sys.path.insert(0, str(ROOT / "backend"))

import core.llm as llm   # noqa: E402

_STORE = os.path.join(_TMP, "oauth.json")


def _write_store(d):
    with open(_STORE, "w") as f:
        json.dump(d, f)


def _read_store():
    with open(_STORE) as f:
        return json.load(f)


class _FakeResp:
    def __init__(self, payload):
        self._b = json.dumps(payload).encode()
    def read(self):
        return self._b
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _install_fake_endpoint(payload, *, calls, delay=0.0, raise_exc=None):
    real = urllib.request.urlopen
    def fake(req, timeout=None):
        calls.append(1)
        if delay:
            time.sleep(delay)
        if raise_exc:
            raise raise_exc
        return _FakeResp(payload)
    urllib.request.urlopen = fake
    return real


def test_valid_store_returns_token_without_refresh():
    _write_store({"access_token": "AT-valid", "refresh_token": "RT", "expires_at": time.time() + 3600})
    calls = []
    real = _install_fake_endpoint({}, calls=calls)
    try:
        assert llm._oauth_bearer() == "AT-valid"
        assert calls == [], "should not refresh a still-valid token"
    finally:
        urllib.request.urlopen = real


def test_expired_store_refreshes_rotates_and_persists():
    _write_store({"access_token": "AT-old", "refresh_token": "RT-old", "expires_at": time.time() - 10})
    calls = []
    real = _install_fake_endpoint(
        {"access_token": "AT-new", "refresh_token": "RT-new", "expires_in": 3600}, calls=calls)
    try:
        tok = llm._oauth_bearer()
        assert tok == "AT-new", f"expected refreshed token, got {tok}"
        assert len(calls) == 1, f"expected exactly one refresh call, got {len(calls)}"
        s = _read_store()
        assert s["access_token"] == "AT-new"
        assert s["refresh_token"] == "RT-new", "refresh token must rotate"
        assert s["expires_at"] > time.time() + 3000, "expires_at must be pushed forward"
    finally:
        urllib.request.urlopen = real


def test_expired_no_refresh_token_returns_none():
    _write_store({"access_token": "AT-old", "expires_at": time.time() - 10})
    assert llm._oauth_bearer() is None


def test_refresh_failure_returns_none():
    _write_store({"access_token": "AT-old", "refresh_token": "RT", "expires_at": time.time() - 10})
    calls = []
    real = _install_fake_endpoint({}, calls=calls, raise_exc=RuntimeError("boom"))
    try:
        assert llm._oauth_bearer() is None, "a failed refresh must not ship the expired token"
    finally:
        urllib.request.urlopen = real


def test_concurrent_turns_refresh_exactly_once():
    _write_store({"access_token": "AT-old", "refresh_token": "RT-old", "expires_at": time.time() - 10})
    calls = []
    real = _install_fake_endpoint(
        {"access_token": "AT-new", "refresh_token": "RT-new", "expires_in": 3600},
        calls=calls, delay=0.05)
    try:
        results = []
        threads = [threading.Thread(target=lambda: results.append(llm._oauth_bearer())) for _ in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert len(calls) == 1, f"refresh token is single-use — expected 1 refresh, got {len(calls)}"
        assert all(r == "AT-new" for r in results), f"all turns should see the fresh token: {results}"
    finally:
        urllib.request.urlopen = real


def test_falls_back_to_env_when_no_store():
    os.remove(_STORE)
    os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = "ENV-TOK"
    try:
        assert llm._oauth_bearer() == "ENV-TOK"
    finally:
        os.environ.pop("CLAUDE_CODE_OAUTH_TOKEN", None)


def main() -> int:
    tests = [test_valid_store_returns_token_without_refresh,
             test_expired_store_refreshes_rotates_and_persists,
             test_expired_no_refresh_token_returns_none,
             test_refresh_failure_returns_none,
             test_concurrent_turns_refresh_exactly_once,
             test_falls_back_to_env_when_no_store]
    failed = []
    for t in tests:
        try:
            t(); print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append(t.__name__); print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback; failed.append(t.__name__)
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}"); traceback.print_exc()
    print(f"\n{'all passed' if not failed else f'{len(failed)} failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
