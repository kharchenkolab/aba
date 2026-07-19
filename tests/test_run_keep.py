"""F2: POST /api/runs/{rid}/keep — the user late-pin. Retains one file against the
Run's weft target(s), labeled to the Run (output_durability.md §6.2). Route called
directly against a temp entity DB with retention.retain monkeypatched.

Run: python tests/test_run_keep.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_keep_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "k.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main  # noqa: E402  (loads the app + type registry)
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
from content.bio.web.routes import runs as rt  # noqa: E402
import core.compute.retention as retmod  # noqa: E402

init_db()


def _mk(**md) -> str:
    out = create_entity(entity_type="analysis", title="Keep Run", metadata=md)
    return out if isinstance(out, str) else out["id"]


def test_keep_retains_rel_against_each_target(monkeypatch):
    """P1: the late-pin goes through the CUMULATIVE keep machinery — the rel is
    recorded as a keep decision and the submitted selection carries every prior
    keep too (a bare retain([rel]) would replace the Run's stored selection)."""
    import json
    calls = []
    monkeypatch.setattr(retmod, "retain",
                        lambda target, **kw: calls.append((target, kw))
                        or {"state": "pinned-pending"})
    monkeypatch.setattr(retmod, "retained", lambda **kw: [
        {"state": "pinned-pending",
         "selection": json.dumps({"include": ["earlier.csv"]})}])
    rid = _mk(thread_id="t", run_state="open", weft_targets=["krn_a", "jb_b"])
    out = rt.run_keep(rid, rt._KeepBody(rel="figs/big.h5ad"))
    assert out["ok"] and out["rel"] == "figs/big.h5ad"
    assert out["decision"]["include"] == ["figs/big.h5ad"]   # recorded level-2 keep
    assert {c[0] for c in calls} == {"krn_a", "jb_b"}
    for _target, kw in calls:
        # cumulative: the new rel PLUS the earlier pin, never a delta
        assert kw["include"] == ["earlier.csv", "figs/big.h5ad"]
        assert kw["label"] == rid and kw["layout"] == "label"


def test_keep_without_targets_is_400(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(retmod, "retain", lambda *a, **k: {"state": "done"})
    rid = _mk(thread_id="t", run_state="open")           # no weft_targets
    try:
        rt.run_keep(rid, rt._KeepBody(rel="x"))
        raise AssertionError("expected HTTPException(400)")
    except HTTPException as e:
        assert e.status_code == 400


def test_keep_blank_rel_is_400(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(retmod, "retain", lambda *a, **k: {"state": "done"})
    rid = _mk(thread_id="t", run_state="open", weft_targets=["krn_a"])
    try:
        rt.run_keep(rid, rt._KeepBody(rel="   "))
        raise AssertionError("expected HTTPException(400)")
    except HTTPException as e:
        assert e.status_code == 400


def test_exec_record_round_trips_weft_target():
    """Regression for the single-turn Run bug: record_weft_target fired BEFORE the
    ambient Run existed (active_run_id None) → the Run had no target → retention never
    fired. Fix: run_exec stashes the kernel id in the exec payload, and the artifact
    registration hook records it once the Run exists. Guard the payload round-trip the
    hook reads (rec.get('weft_target'))."""
    from core.graph import exec_records as er
    eid = er.create(thread_id="t", tool_name="run_python", status="ok",
                    started_at="2026-07-16T00:00:00+00:00", cwd=_RT,
                    payload={"executor": "kernel:python", "weft_target": "krn_zzz",
                             "produced": []})
    rec = er.get(eid)
    assert rec.get("weft_target") == "krn_zzz"


_TESTS = [test_keep_retains_rel_against_each_target,
          test_keep_without_targets_is_400, test_keep_blank_rel_is_400,
          test_exec_record_round_trips_weft_target]


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
