"""POST /api/runs/{rid}/bring-back (§8e.4, misc/more_weft_ui.md): ship a Run's
KEPT files to the workspace — location axis only (the in-place keeps stay
kept). Selection comes from the retained index per target; nothing unkept
rides along.

Run: python tests/test_bring_back.py   (or via pytest)
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_bb_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "b.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main  # noqa: E402,F401
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
from content.bio.lifecycle import runs as runsmod  # noqa: E402
import core.compute.retention as retmod  # noqa: E402

init_db()


def _mk(**md) -> str:
    out = create_entity(entity_type="analysis", title="BB Run", metadata=md)
    return out if isinstance(out, str) else out["id"]


def test_bring_back_ships_kept_selection_to_workspace(monkeypatch):
    rid = _mk(weft_targets=["krn_bb"])
    calls = []
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{
        "target": "krn_bb", "state": "done", "site": "siteB", "in_place": 1,
        "location": "/remote/only/runs/x",
        "selection": json.dumps({"include": ["out/model.bin", "out/table.csv"]}),
    }])
    monkeypatch.setattr(retmod, "retain",
                        lambda t, **kw: calls.append((t, kw)) or {"state": "queued"})
    out = runsmod.bring_back_run(rid)
    assert out["ok"] is True and out["requested"] == 2
    (t, kw), = calls
    assert t == "krn_bb" and kw["dest"] == "@workspace"
    assert sorted(kw["include"]) == ["out/model.bin", "out/table.csv"]
    assert kw["label"] == rid


def test_bring_back_with_nothing_kept_errors(monkeypatch):
    rid = _mk(weft_targets=["krn_e"])
    monkeypatch.setattr(retmod, "retained", lambda **kw: [])
    out = runsmod.bring_back_run(rid)
    assert "error" in out


def test_bring_back_without_targets_errors():
    rid = _mk()
    out = runsmod.bring_back_run(rid)
    assert "error" in out


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
    for t in (test_bring_back_ships_kept_selection_to_workspace,
              test_bring_back_with_nothing_kept_errors,
              test_bring_back_without_targets_errors):
        mp = _MP()
        try:
            t(mp) if t.__code__.co_argcount else t()
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
