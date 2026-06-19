"""Cached accessor for the active bundle.

The EffectiveBundle is resolved + composed ONCE at process startup
and cached at module scope. Per-turn code reads from the cache — no
filesystem I/O on the hot path.

Usage:
    from core.bundle.active import get_bundle
    eb = get_bundle()
    text = eb.policy_text
    skills = eb.skills

Tests / admin tooling can call `reload_bundle()` to force a refresh
(picks up new env vars, bundle changes on disk, etc.).
"""
from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

from core.bundle.loader import EffectiveBundle, load_bundle
from core.bundle.scope_resolver import ScopeResolution, resolve_scopes

if TYPE_CHECKING:
    pass


_cache_lock = threading.Lock()
_cached_bundle: EffectiveBundle | None = None
_cached_resolution: ScopeResolution | None = None


def get_bundle() -> EffectiveBundle:
    """Return the active EffectiveBundle, computing it once on first
    access. Thread-safe."""
    global _cached_bundle, _cached_resolution
    if _cached_bundle is not None:
        return _cached_bundle
    with _cache_lock:
        if _cached_bundle is None:
            _cached_resolution = resolve_scopes()
            _cached_bundle = load_bundle(_cached_resolution)
    return _cached_bundle


def get_resolution() -> ScopeResolution:
    """Return the ScopeResolution that produced the cached bundle."""
    if _cached_resolution is None:
        get_bundle()                           # forces resolution
    assert _cached_resolution is not None
    return _cached_resolution


def reload_bundle() -> EffectiveBundle:
    """Force a re-resolution + re-composition. Used by:
      - tests that need to vary env between cases
      - the `aba bundle inspect --reload` admin path
    Returns the freshly-composed bundle."""
    global _cached_bundle, _cached_resolution
    with _cache_lock:
        _cached_resolution = resolve_scopes()
        _cached_bundle = load_bundle(_cached_resolution)
    return _cached_bundle


def _reset_for_testing() -> None:
    """Internal: drop the cache. Tests use this between cases."""
    global _cached_bundle, _cached_resolution
    with _cache_lock:
        _cached_bundle = None
        _cached_resolution = None
