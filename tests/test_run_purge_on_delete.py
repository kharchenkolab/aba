"""P2b: hard-deleting a Run reclaims its retained bytes (run_forget by label).

Drives the real /api/entities DELETE handler (called directly) against a temp
entity DB, with retention.forget monkeypatched. Verifies the policy from
misc/output_durability.md §7: hard delete of a Run → forget(label=rid); soft
archive keeps the bytes (recoverable); non-Run entities never forget.

Run: python tests/test_run_purge_on_delete.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_purge_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "p.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main  # noqa: E402  (heavy import: builds the FastAPI app, no server start)
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity, get_entity  # noqa: E402
import core.compute.retention as retmod  # noqa: E402

init_db()  # startup hook doesn't fire on import — create the schema ourselves


def _mk(entity_type: str, title: str, **kw) -> str:
    out = create_entity(entity_type=entity_type, title=title, **kw)
    return out if isinstance(out, str) else out["id"]   # returns the id string


def test_hard_delete_analysis_forgets_bytes(monkeypatch):
    calls = []
    monkeypatch.setattr(retmod, "forget",
                        lambda **kw: calls.append(kw) or {"forgotten": []})
    rid = _mk("analysis", "Run A")
    main.entities_delete(entity_id=rid, hard=True, _pid="test")
    assert get_entity(rid) is None                 # entity gone
    assert calls == [{"label": rid}]               # bytes reclaimed by label


def test_soft_archive_analysis_keeps_bytes(monkeypatch):
    calls = []
    monkeypatch.setattr(retmod, "forget", lambda **kw: calls.append(kw))
    rid = _mk("analysis", "Run B")
    main.entities_delete(entity_id=rid, hard=False, _pid="test")   # soft archive
    assert calls == []                             # archive is recoverable: keep bytes
    e = get_entity(rid)
    assert e and e.get("status") == "archived"     # archived, not removed


def test_hard_delete_nonanalysis_does_not_forget(monkeypatch):
    calls = []
    monkeypatch.setattr(retmod, "forget", lambda **kw: calls.append(kw))
    fid = _mk("figure", "Fig", artifact_path="/artifacts/test/x.png")
    main.entities_delete(entity_id=fid, hard=True, _pid="test")
    assert calls == []                             # only a Run owns retained bytes


_TESTS = [
    test_hard_delete_analysis_forgets_bytes,
    test_soft_archive_analysis_keeps_bytes,
    test_hard_delete_nonanalysis_does_not_forget,
]


def _standalone() -> int:
    import inspect
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v, raising=True):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in _TESTS:
        mp = _MP()
        try:
            t(mp) if "monkeypatch" in inspect.signature(t).parameters else t()
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
