"""retention2 in aba: the size-gated no-durable keep policy (misc/retention2.md
+ compute_settings) — small keeper sets ship home with a note; big ones become
a Run alert carrying the levers; other retain errors pass through unchanged."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_ret2_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.bio

from core.compute.errors import ComputeError  # noqa: E402
from content.bio.lifecycle.runs import _no_durable_keep_policy  # noqa: E402


class FakeComp:
    def __init__(self, sizes):
        self.sizes = sizes

    def sync_call(self, name, target):
        assert name == "run_inventory"
        return {"target": target, "entries": [
            {"path": p, "bytes": b, "mtime": 1} for p, b in self.sizes.items()]}


@pytest.fixture()
def env(monkeypatch):
    import core.compute.adapter as ad
    from core.compute import retention
    calls = []

    def fake_retain(target, **kw):
        calls.append(kw)
        if kw.get("dest") != "@workspace":
            raise ComputeError("retain.no_durable", "no durable storage",
                               hints={})
        return {"state": "queued"}
    monkeypatch.setattr(retention, "retain", fake_retain)
    return monkeypatch, ad, calls


def test_small_keepers_ship_home_silently(env):
    monkeypatch, ad, calls = env
    monkeypatch.setattr(ad, "get_compute",
                        lambda: FakeComp({"results/a.csv": 5_000_000}))
    err = _no_durable_keep_policy("jb_1", ["results/a.csv"], "run_x")
    assert err is None
    assert calls[-1]["dest"] == "@workspace"        # retried with ship-home


def test_big_keepers_become_an_alert_with_levers(env):
    monkeypatch, ad, calls = env
    monkeypatch.setattr(ad, "get_compute",
                        lambda: FakeComp({"results/big.h5": 5 * 1024**3}))
    err = _no_durable_keep_policy("jb_1", ["results/big.h5"], "run_x")
    assert err and "5.4 GB" in err
    assert "durable storage" in err and "Settings" in err
    assert not [c for c in calls if c.get("dest")]  # nothing shipped


def test_unknown_size_reads_as_big(env):
    monkeypatch, ad, calls = env

    def boom():
        raise RuntimeError("substrate hiccup")
    monkeypatch.setattr(ad, "get_compute", boom)
    err = _no_durable_keep_policy("jb_1", ["x"], "run_x")
    assert err and "unknown size" in err
    assert not [c for c in calls if c.get("dest")]


def test_basename_matching(env):
    monkeypatch, ad, calls = env
    monkeypatch.setattr(ad, "get_compute",
                        lambda: FakeComp({"results/deep/a.csv": 1000}))
    assert _no_durable_keep_policy("jb_1", ["a.csv"], "run_x") is None
