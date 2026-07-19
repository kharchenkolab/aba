"""GET /api/runs/{rid}/archive — the run-level "Local copy all" (§8e.3,
misc/more_weft_ui.md). The zip carries every locally-servable output; files
whose bytes aren't available from this machine (remote in-place keeps,
discarded files) are LISTED in SKIPPED-FILES.txt — the archive never lies
about completeness.

Run: python tests/test_run_archive.py   (or via pytest)
"""
from __future__ import annotations
import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_arch_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "a.db"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

import main  # noqa: E402,F401  (loads the app + type registry)
from core.graph._schema import init_db  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402
from content.bio.web.routes import runs as rt  # noqa: E402
from content.bio.lifecycle import runs as runsmod  # noqa: E402

init_db()


def _mk() -> str:
    out = create_entity(entity_type="analysis", title="Archive Run", metadata={})
    return out if isinstance(out, str) else out["id"]


def test_archive_zips_local_files_and_lists_the_rest(tmp_path, monkeypatch):
    rid = _mk()
    local = tmp_path / "a.txt"
    local.write_text("payload")
    monkeypatch.setattr(runsmod, "run_durable_view", lambda r: {"files": [
        {"rel": "a.txt", "state": "retained", "site": "local"},
        {"rel": "far.dat", "state": "retained", "site": "siteB"},   # bytes not here
        {"rel": "gone.dat", "state": "cleared", "site": None},      # discarded
    ], "summary": {}})
    monkeypatch.setattr(runsmod, "resolve_run_file",
                        lambda r, rel: str(local) if rel == "a.txt" else None)
    monkeypatch.setattr(runsmod, "read_run_file", lambda r, rel: (None, False, 0))
    resp = rt.run_archive(rid)
    assert resp.headers["content-disposition"].endswith('-outputs.zip"')
    zf = zipfile.ZipFile(io.BytesIO(resp.body))
    names = set(zf.namelist())
    assert "a.txt" in names and zf.read("a.txt") == b"payload"
    assert "far.dat" not in names and "gone.dat" not in names
    manifest = zf.read("SKIPPED-FILES.txt").decode()
    assert "far.dat" in manifest and "on siteB" in manifest
    assert "gone.dat" in manifest and "discarded" in manifest


def test_archive_404s_when_no_files(monkeypatch):
    rid = _mk()
    monkeypatch.setattr(runsmod, "run_durable_view",
                        lambda r: {"files": [], "summary": {}})
    try:
        rt.run_archive(rid)
        raise AssertionError("expected 404")
    except Exception as e:  # noqa: BLE001
        assert getattr(e, "status_code", None) == 404


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
    for t in (test_archive_zips_local_files_and_lists_the_rest,
              test_archive_404s_when_no_files):
        mp = _MP()
        kw = {"monkeypatch": mp}
        if "tmp_path" in t.__code__.co_varnames[:t.__code__.co_argcount]:
            kw["tmp_path"] = Path(tempfile.mkdtemp(prefix="arch_"))
        try:
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
