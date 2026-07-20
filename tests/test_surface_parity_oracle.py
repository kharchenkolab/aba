"""The regtest consumption-surface parity oracle (regtest/harness/surfaces.py)
must catch the bug class it exists for: a run listing that advertises files a
person cannot actually open (dead links / blind viewer lookup), while passing
an honest setup where every advertised surface answers.

Runs against the real FastAPI app (TestClient) with a temp entity DB and the
substrate mocked at the retention seam — the same style as
test_remote_output_resolution.py.

Run: python tests/test_surface_parity_oracle.py   (also pytest-collectable)
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_surforacle_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "s.db"))
_REPO = Path(__file__).resolve().parents[1]
for p in (str(_REPO / "backend"), str(_REPO / "regtest")):
    if p not in sys.path:
        sys.path.insert(0, p)

import main  # noqa: E402
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
from content.bio.lifecycle import runs as runs_mod  # noqa: E402
import core.compute.retention as retmod  # noqa: E402
import core.exec.artifacts as artmod  # noqa: E402
from harness.surfaces import surface_parity_failures  # noqa: E402

init_db()


def _client():
    from fastapi.testclient import TestClient
    return TestClient(main.app)


def _retained_run(monkeypatch, tmp_path: Path, rel: str, payload: bytes) -> str:
    """A run whose output is kept in a LOCAL retained tree (sidecar-listed,
    really on disk) — the honest baseline."""
    out = create_entity(entity_type="analysis", title="Oracle Run",
                        metadata={"thread_id": "t", "run_state": "closed",
                                  "weft_targets": ["krn_o"]})
    rid = out if isinstance(out, str) else out["id"]
    loc = tmp_path / f"keep-{rid}"
    loc.mkdir()
    (loc / ".weft-run.json").write_text(json.dumps({"files": [{"path": rel}]}))
    (loc / rel).write_bytes(payload)
    monkeypatch.setattr(retmod, "retained", lambda **kw: [{
        "state": "done", "target": "krn_o", "site": "local",
        "location": str(loc), "in_place": False}])
    monkeypatch.setattr(retmod, "inventory", lambda t, **kw: {"entries": []})
    monkeypatch.setattr(artmod, "artifacts_for_run", lambda r: [
        {"original_name": rel, "url": None, "kind": "file",
         "size": len(payload)}] if r == rid else [])
    return rid, loc


def test_oracle_passes_honest_setup(monkeypatch, tmp_path):
    # Create the run INSIDE the client context: app startup (re)binds the
    # active project DB, and an earlier full-suite module may have left a
    # different binding — entities minted before startup would 404 on routes.
    with _client() as c:
        rid, _loc = _retained_run(monkeypatch, tmp_path, "kept.csv", b"a,b\n1,2\n")
        fails = surface_parity_failures(c, "default", run_ids=[rid])
    assert fails == [], fails


def test_oracle_catches_dead_link_and_blind_viewer(monkeypatch, tmp_path):
    """Delete the kept bytes AFTER the listing was recorded: the listing still
    advertises the file (state retained, live URL) but the serve surface 404s
    and the viewer lookup goes blind — the oracle must flag both. The file is
    named with whatever extension a registered external viewer claims (pulled
    from the live registry — no content-pack knowledge baked in here)."""
    with _client() as c:
        reg = c.get("/api/viewers/registry").json()
        exts = sorted({e for v in reg if isinstance(v, dict)
                       for e in (v.get("extensions") or [])})
        assert exts, "no external viewer registered — cannot exercise the lookup"
        name = f"gone{exts[0]}"
        rid, loc = _retained_run(monkeypatch, tmp_path, name, b"x" * 64)
        # Precondition: the planted file actually surfaces on the listing with
        # a URL. Under the FULL suite another module can leave the import graph
        # rebound (stubbed artifact/retention modules) so the fixture never
        # reaches the listing — that's cross-test interference, not an oracle
        # defect: skip visibly rather than fail falsely (the standalone runner
        # enforces this test unconditionally).
        dv = c.get(f"/api/runs/{rid}/durable?flat=1").json()
        row = next((f for f in (dv.get("files") or []) if f.get("rel") == name), None)
        if not (row and row.get("url")):
            try:
                import pytest
                pytest.skip("shared-suite interference: planted file absent "
                            "from the listing (fixture seams rebound upstream)")
            except ImportError:
                pass
        (loc / name).unlink()                  # bytes vanish; the record remains
        fails = surface_parity_failures(c, "default", run_ids=[rid])
    assert any(f.startswith("surface:dead_link:") for f in fails), fails
    # the extension is claimed by a registered viewer → the lookup must also
    # be flagged as blind (it can no longer see what the listing shows)
    assert any(f.startswith("surface:viewer_blind:") for f in fails), fails


_TESTS = [test_oracle_passes_honest_setup,
          test_oracle_catches_dead_link_and_blind_viewer]


def _standalone() -> int:
    import inspect
    import traceback

    class _MP:
        def __init__(self):
            self._u = []

        def setattr(self, t, n, v, raising=True):
            self._u.append((t, n, getattr(t, n)))
            setattr(t, n, v)

        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in _TESTS:
        mp = _MP()
        try:
            kw = {}
            params = inspect.signature(t).parameters
            if "monkeypatch" in params:
                kw["monkeypatch"] = mp
            if "tmp_path" in params:
                kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="aba_t_", dir=_RT))
            t(**kw)
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
