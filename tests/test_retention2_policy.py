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


# ── LIVE (local weft): the new retention semantics through aba's stack ───────

weft_ok = False
try:
    import weft.api  # noqa: F401
    from core.compute import adapter as _ad
    weft_ok = _ad.resolve_pixi() is not None
except Exception:  # noqa: BLE001
    pass


@pytest.mark.skipif(not weft_ok, reason="weft/pixi unavailable")
def test_live_retention2_semantics(tmp_path, monkeypatch):
    """Against real weft: (1) the LOCAL site is durable → keeps pin in place,
    nothing moves; (2) a non-durable aux site refuses with retain.no_durable
    (the code our policy switches on); (3) dest='@workspace' ships; (4) the
    keep anchors re-obtainability — after CAS eviction, data_fetch serves
    from the keep, hash-verified."""
    import time
    from core.compute import adapter as ad
    monkeypatch.setenv("ABA_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(ad, "_adapter", None)
    monkeypatch.setattr(ad, "_status", {"ok": False, "severity": "info",
                                        "detail": "un"})
    st = ad.configure()
    assert st["ok"], st["detail"]
    comp = ad.get_compute()
    try:
        # a second local-kind site with NO durable declaration (topology B)
        r = comp.sync_call("register_site", "auxb", "local",
                           {"root": str(tmp_path / "aux-root")})
        assert r.get("site") == "auxb"

        def run_task(site):
            # commands must DIFFER per site: placement is circumstance, not
            # identity — an identical command memo-hits the other site's job
            # (found live: the "wrong-target retain" was our own memo hit)
            t = comp.sync_call("task_submit", {
                "command": f"mkdir -p out && echo payload-{site} > out/r.txt",
                "site": site, "label": f"ret2-{site}"})
            jid = t["job_id"]
            for _ in range(240):
                s = comp.sync_call("task_status", jid)[0]["state"]
                if s in ("DONE", "FAILED", "CANCELLED"):
                    break
                time.sleep(0.5)
            assert s == "DONE", s
            return jid

        # (1) local site is durable:true → retain pins IN PLACE
        jid = run_task("local")
        k = comp.sync_call("run_retain", jid, include=["out/r.txt"],
                           background=False, label="keepA")
        assert k.get("in_place") is True, k
        assert k.get("moved") in (False, None), k

        # (2) topology-B site refuses with the dedicated code
        jid_b = run_task("auxb")
        from core.compute.errors import ComputeError
        with pytest.raises(ComputeError) as ei:
            comp.sync_call("run_retain", jid_b, include=["out/r.txt"],
                           background=False, label="keepB")
        assert ei.value.code == "retain.no_durable"
        assert "dest" in str(ei.value.hints)          # levers ride the hints

        # (3) explicit ship-home works
        k2 = comp.sync_call("run_retain", jid_b, include=["out/r.txt"],
                            background=False, dest="@workspace", label="keepB")
        assert k2.get("moved") is True or k2.get("state") in ("queued", "done")

        # (4) keep-anchor: register the kept file by (run, rel), evict the
        # CAS copy, fetch again — served from the keep, hash-verified
        ref = comp.sync_call("data_register", run=jid, rel="out/r.txt")["ref"]
        dest1 = tmp_path / "f1.txt"
        comp.sync_call("data_fetch", ref, str(dest1))
        assert "payload-local" in dest1.read_text()
        comp.sync_call("gc_sweep", "local", confirm=True)   # evict caches
        dest2 = tmp_path / "f2.txt"
        comp.sync_call("data_fetch", ref, str(dest2))       # keep re-serves
        assert "payload-local" in dest2.read_text()
    finally:
        try:
            comp.sync_call("run_forget", target=jid_b)
        except Exception:  # noqa: BLE001
            pass
        try:
            comp.sync_call("site_unregister", "auxb")
        except Exception:  # noqa: BLE001
            pass
        ad.shutdown()
