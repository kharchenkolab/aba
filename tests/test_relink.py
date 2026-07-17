"""§5 verified relink (misc/more_weft_ui.md): accept ONLY on a content match.
The comparator is names+sizes with mtimes EXCLUDED — a legitimate migration
(cp -r / rsync without -t) rewrites mtimes while content is identical; the
registration digest (with mtimes) is the wrong comparator for "it moved".

Run: python tests/test_relink.py   (or via pytest)
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_rl_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import core.data.datasets as ds  # noqa: E402


class _Comp:
    """data_fingerprint stub: path → entries (path, bytes, mtime)."""
    def __init__(self, worlds: dict):
        self.worlds = worlds
    def sync_call(self, name, path, site=None, **kw):
        assert name == "data_fingerprint"
        entries = self.worlds.get(path)
        if entries is None:
            return {"entries": [], "bytes": 0}
        return {"entries": entries, "bytes": sum(e["bytes"] for e in entries)}


def _world(mt):
    return [{"path": "a.csv", "bytes": 10, "mtime": mt},
            {"path": "sub/b.bin", "bytes": 999, "mtime": mt}]


def test_relink_accepts_same_content_despite_new_mtimes(monkeypatch):
    # old home readable; new copy has DIFFERENT mtimes but identical names+sizes
    comp = _Comp({"/old/home": _world(100), "/new/home": _world(555)})
    monkeypatch.setattr(ds, "_comp", lambda: comp)
    meta = {"home": {"site": "siteB", "path": "/old/home"}}
    out = ds.relink(meta, "/new/home")
    assert out["ok"] is True and out["state"] == "relinked"
    assert out["home"] == {"site": "siteB", "path": "/new/home"}
    assert out["fingerprint"]["exists"] is True


def test_relink_rejects_different_content(monkeypatch):
    changed = [{"path": "a.csv", "bytes": 11, "mtime": 1}]   # size differs
    comp = _Comp({"/old/home": _world(1), "/new/home": changed})
    monkeypatch.setattr(ds, "_comp", lambda: comp)
    out = ds.relink({"home": {"site": "siteB", "path": "/old/home"}}, "/new/home")
    assert out["ok"] is False and out["state"] == "mismatch"
    assert out["new_shape"]["n_files"] == 1


def test_relink_falls_back_to_recorded_counts_when_old_home_gone(monkeypatch):
    comp = _Comp({"/new/home": _world(9)})                   # old home unreadable
    monkeypatch.setattr(ds, "_comp", lambda: comp)
    meta = {"home": {"site": "siteB", "path": "/old/home"},
            "fingerprint": {"n_files": 2, "total_bytes": 1009}}
    out = ds.relink(meta, "/new/home")
    assert out["ok"] is True and out["state"] == "relinked"
    # and the weaker comparator still REFUSES a count/byte mismatch
    meta_bad = {"home": {"site": "siteB", "path": "/old/home"},
                "fingerprint": {"n_files": 3, "total_bytes": 1}}
    out2 = ds.relink(meta_bad, "/new/home")
    assert out2["ok"] is False and out2["state"] == "mismatch"


def test_relink_missing_new_path(monkeypatch):
    comp = _Comp({"/old/home": _world(1)})
    monkeypatch.setattr(ds, "_comp", lambda: comp)
    out = ds.relink({"home": {"site": "siteB", "path": "/old/home"}}, "/nowhere")
    assert out["ok"] is False and out["state"] == "missing"


def _standalone() -> int:
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in (test_relink_accepts_same_content_despite_new_mtimes,
              test_relink_rejects_different_content,
              test_relink_falls_back_to_recorded_counts_when_old_home_gone,
              test_relink_missing_new_path):
        mp = _MP()
        try:
            t(mp)
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
