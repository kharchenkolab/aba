"""Guard for WeftAdapter._refresh_shims (adapter.py): after a weft update,
already-registered sites must re-run ensure_bootstrap so the site shim tracks the
INSTALLED weft — register_site only bootstraps NEW sites, so without this a weft
bump leaves stale shims and new shim verbs (e.g. file-root data_fingerprint) fail.
Best-effort: a dead host is skipped, not fatal."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ABA_HOME", tempfile.mkdtemp(prefix="aba_shim_"))
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.bio

from core.compute.adapter import WeftAdapter  # noqa: E402


class _Adapter:
    def __init__(self, boom=False):
        self.calls = 0
        self.boom = boom

    def ensure_bootstrap(self):
        self.calls += 1
        if self.boom:
            raise RuntimeError("dead host")


class _FakeWeft:
    def __init__(self, adapters):
        self.adapters = adapters


def test_refresh_shims_bootstraps_every_site_and_swallows_errors():
    comp = WeftAdapter.__new__(WeftAdapter)      # skip __init__ (no real weft/pixi)
    ok, dead = _Adapter(), _Adapter(boom=True)
    comp._weft = _FakeWeft({"local": ok, "cluster": dead})
    comp._refresh_shims()                          # must not raise despite the dead site
    assert ok.calls == 1 and dead.calls == 1       # both attempted; error swallowed


def test_refresh_shims_noop_without_adapters():
    comp = WeftAdapter.__new__(WeftAdapter)
    comp._weft = _FakeWeft({})
    comp._refresh_shims()                           # no sites → clean no-op


_TESTS = [
    test_refresh_shims_bootstraps_every_site_and_swallows_errors,
    test_refresh_shims_noop_without_adapters,
]
