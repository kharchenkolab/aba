"""Viewer stores are same-origin by default; an allowlist widens it deliberately.

The store route serves a project's own bytes and adds no auth of its own, so the
origin check IS the access boundary. `*` would let any page the user happens to
visit read their data, which is why it is never honoured however it is configured.

Requested by pagoda3, whose reasoning about the read side is right: without
`Access-Control-Expose-Headers` a cross-origin reader gets a perfectly good 206
whose `Content-Range` it cannot read, so every size/consistency check breaks —
including the 3-byte probe that catches a truncated store. That is exactly how the
16 MiB truncation would have stayed invisible from the browser; it was caught
same-origin, where headers are readable.

The `Vary: Origin` case is the one with teeth: ref-arm chunks are served
`immutable` for a day, so without Vary a cache can hand one origin's
`Allow-Origin` to a different origin and the header outlives the request by as
long as the bytes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

ALLOWED = "https://pagoda3.example.org"
OTHER = "https://evil.example.com"


@pytest.fixture
def cors(monkeypatch):
    """`(origin, *allowlist) -> headers` against the real policy function."""
    import main
    from core import config

    def go(origin, *allow):
        class _S:
            @staticmethod
            def get():
                return tuple(allow)
        monkeypatch.setattr(config.settings, "store_allowed_origins", _S)
        return main._store_cors(origin)
    return go


# ── default: same-origin only ────────────────────────────────────────────────

def test_no_allowlist_means_no_allow_origin(cors):
    """CEILING and default: today's behaviour is unchanged for every deployment
    that sets nothing."""
    h = cors(ALLOWED)                       # an Origin arrives, but nothing is allowed
    assert "Access-Control-Allow-Origin" not in h
    assert h["Cross-Origin-Resource-Policy"] == "same-origin"


def test_a_same_origin_request_carries_no_origin_header(cors):
    """The ordinary case: same-origin fetches send no Origin, and must not be
    handed CORS headers they never asked for."""
    h = cors(None, ALLOWED)
    assert "Access-Control-Allow-Origin" not in h


# ── an allowed origin gets exactly what it needs ─────────────────────────────

def test_an_allowed_origin_is_echoed_with_expose_headers(cors):
    h = cors(ALLOWED, ALLOWED)
    assert h["Access-Control-Allow-Origin"] == ALLOWED
    for want in ("Content-Range", "Content-Length", "Accept-Ranges"):
        assert want in h["Access-Control-Expose-Headers"], want
    assert h["Cross-Origin-Resource-Policy"] == "cross-origin"


def test_the_echo_is_never_a_wildcard(cors):
    """Echoing the request's origin, not `*`, is what keeps the allowlist an
    allowlist — a wildcard would also break credentialed requests silently."""
    assert cors(ALLOWED, ALLOWED)["Access-Control-Allow-Origin"] == ALLOWED


def test_a_trailing_slash_still_matches(cors):
    """WIDE — the shape an operator actually types into a config file."""
    assert cors(ALLOWED, ALLOWED + "/")["Access-Control-Allow-Origin"] == ALLOWED
    assert cors(ALLOWED + "/", ALLOWED)["Access-Control-Allow-Origin"] == ALLOWED + "/"


# ── the security-relevant refusals ───────────────────────────────────────────

def test_an_unlisted_origin_gets_nothing(cors):
    """THE assertion this file exists for: a configured allowlist must not become
    a general opening."""
    h = cors(OTHER, ALLOWED)
    assert "Access-Control-Allow-Origin" not in h
    assert h["Cross-Origin-Resource-Policy"] == "same-origin"


def test_a_wildcard_in_the_allowlist_is_REFUSED(cors):
    """An operator writing `*` — or a copied example — must not turn the whole
    store tree world-readable. These are project bytes on a server with no auth
    of its own."""
    for origin in (ALLOWED, OTHER, "http://localhost:9999"):
        h = cors(origin, "*")
        assert "Access-Control-Allow-Origin" not in h, origin


def test_a_wildcard_alongside_a_real_entry_does_not_widen_it(cors):
    """WIDE: `*` mixed with a legitimate entry keeps the entry and drops the
    wildcard — it must not fall back to allowing everything."""
    assert cors(ALLOWED, "*", ALLOWED)["Access-Control-Allow-Origin"] == ALLOWED
    assert "Access-Control-Allow-Origin" not in cors(OTHER, "*", ALLOWED)


def test_a_prefix_of_an_allowed_origin_is_not_allowed(cors):
    """Substring matching is the classic CORS hole: `evil.example.org` must not
    match because it ends with an allowed suffix, nor a longer host that starts
    with one."""
    for origin in ("https://pagoda3.example.org.evil.com",
                   "https://notpagoda3.example.org",
                   "http://pagoda3.example.org"):        # scheme differs
        assert "Access-Control-Allow-Origin" not in cors(origin, ALLOWED), origin


def test_empty_and_blank_entries_are_ignored(cors):
    """WIDE — the degenerate config: trailing commas leave empty strings, and an
    empty entry must never match the absent-Origin case."""
    h = cors(None, "", "  ")
    assert "Access-Control-Allow-Origin" not in h
    assert "Access-Control-Allow-Origin" not in cors("", "", ALLOWED)


# ── caching correctness ──────────────────────────────────────────────────────

def test_vary_origin_is_always_present(cors):
    """Ref-arm chunks are served `immutable` for a day. Without Vary a cache can
    serve one origin's Allow-Origin to another, and the mistake lives as long as
    the bytes. Present in BOTH branches, or the allowed case is the unsafe one."""
    assert cors(ALLOWED, ALLOWED)["Vary"] == "Origin"
    assert cors(OTHER, ALLOWED)["Vary"] == "Origin"
    assert cors(None)["Vary"] == "Origin"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
