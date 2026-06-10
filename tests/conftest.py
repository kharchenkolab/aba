"""Shared pytest setup.

Wave 2 A.3: guide.py now reads `active_pack()` at the top of
stream_response. Tests that exercise stream_response (or import guide.py
in a way that runs it) would fail with "no content pack registered" if
nothing set it up. The production path is main.py startup; for tests
we do the same here, once per process.

Tests marked @pytest.mark.platform that don't need bio CAN still run
without this — they just shouldn't be importing guide.py. The
platform-purity gate (tests/test_platform_test_imports.py) catches
that case.
"""
from __future__ import annotations

import os
import sys

# Make backend/ importable from tests/ — mirrors what main.py does as
# the live entry point. The standalone-script tests do this via a
# `sys.path.insert(0, ...)` block at the top of each file; pytest-
# discovered tests benefit from a single conftest line.
_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.normpath(os.path.join(_HERE, "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def _register_bio_pack_once() -> None:
    """Idempotent: registering the same pack twice is a no-op; trying
    to register a DIFFERENT pack raises (test would have to clear
    state via clear_active_pack_for_testing first)."""
    try:
        from core.runtime.content_pack import active_pack, set_active_pack
    except ImportError:
        return  # backend not on path yet (very early collection)
    try:
        active_pack()
        return  # already set — fine
    except RuntimeError:
        pass
    try:
        from content.bio import BIO_PACK
    except ImportError:
        # No bio? Then nothing to register. Platform-tier tests run.
        return
    set_active_pack(BIO_PACK)
    BIO_PACK.register_hooks()


_register_bio_pack_once()
