"""The dataset card's "Mirror locally" — POST /api/datasets/{did}/mirror.

Contract (live UX finding 2026-07-25: a by-reference dataset's remote home
was invisible on the card, with no way to bring bytes home):
  - stages through core.data.datasets.fetch — the SAME guardrailed,
    fingerprint-verified path compute uses; the route adds no transfer
    mechanism of its own;
  - over the gate → honest 413 naming size, limit, and the placement
    suggestion (never a silent multi-GB transfer);
  - drift/missing surface as 409 (never fetch stale);
  - a local dataset → 400 (nothing to mirror);
  - success records artifact_path + metadata.local_mirror so previews and
    viewers serve without a remote hop.

Run: pytest tests/test_dataset_mirror.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

_tmp = tempfile.mkdtemp(prefix="aba_mirror_")
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "m.db"))
os.environ.setdefault("ARTIFACTS_DIR", str(Path(_tmp) / "artifacts"))
os.environ.setdefault("ABA_WORK_DIR", str(Path(_tmp) / "work"))
os.environ.setdefault("DATA_DIR", str(Path(_tmp) / "data"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402,F401
import core.data.datasets as dmod  # noqa: E402
from core.graph.entities import create_entity, get_entity  # noqa: E402

pytestmark = pytest.mark.bio


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import content.bio.web.routes.datasets as dr
    app = FastAPI()
    app.include_router(dr.router)
    return TestClient(app)


def _remote_ds(**md) -> str:
    return create_entity(
        entity_type="dataset", title="DS-1", artifact_path=None,
        metadata={"by_reference": True,
                  "home": {"site": "siteA", "path": "/data/bundle.bin"},
                  "descriptor": {"total_bytes": 1000, "n_files": 1}, **md})


def test_mirror_success_records_local_copy(monkeypatch):
    calls = {}

    def _fetch(meta, to_path, **kw):
        calls["dest"] = to_path
        Path(to_path).parent.mkdir(parents=True, exist_ok=True)
        Path(to_path).write_bytes(b"bytes")
        return {"ok": True, "ref": "dref:x", "path": to_path}
    monkeypatch.setattr(dmod, "fetch", _fetch)
    did = _remote_ds()
    r = _client().post(f"/api/datasets/{did}/mirror",
                       params={"project_id": "default"})
    assert r.status_code == 200, r.text
    ent = get_entity(did)
    assert ent["artifact_path"] == calls["dest"]
    lm = ent["metadata"]["local_mirror"]
    assert lm["path"] == calls["dest"] and lm["ref"] == "dref:x"
    # the home stays authoritative — mirroring must not erase it
    assert ent["metadata"]["home"]["site"] == "siteA"


def test_mirror_over_gate_is_an_honest_413(monkeypatch):
    monkeypatch.setattr(dmod, "fetch", lambda meta, to, **kw: {
        "error": "fetch_guardrail", "ok": False,
        "total_bytes": 40 * 1024**3, "limit": 2 * 1024**3,
        "suggestion": "run the analysis on siteA instead"})
    did = _remote_ds()
    r = _client().post(f"/api/datasets/{did}/mirror",
                       params={"project_id": "default"})
    assert r.status_code == 413
    d = r.json()["detail"]
    assert "GB" in d and "siteA" in d, \
        "an over-gate refusal must name the size and the placement lever"


def test_mirror_drifted_home_refuses_not_fetches_stale(monkeypatch):
    monkeypatch.setattr(dmod, "fetch", lambda meta, to, **kw: {
        "error": "source_drifted", "state": "drifted"})
    did = _remote_ds()
    r = _client().post(f"/api/datasets/{did}/mirror",
                       params={"project_id": "default"})
    assert r.status_code == 409
    assert "drifted" in r.json()["detail"]


def test_mirror_local_dataset_is_a_400(monkeypatch):
    def _never(*a, **kw):
        raise AssertionError("must not fetch a local dataset")
    monkeypatch.setattr(dmod, "fetch", _never)
    did = create_entity(entity_type="dataset", title="DS-local",
                        artifact_path=str(Path(_tmp) / "x.bin"),
                        metadata={})
    r = _client().post(f"/api/datasets/{did}/mirror",
                       params={"project_id": "default"})
    assert r.status_code == 400


def test_mirror_transfer_failure_cleans_up_and_answers_typed(monkeypatch):
    """A ComputeError mid-transfer (link drops at 60%) must NOT escape as a
    bare 500 leaving a half-copied tree on disk: the partial is removed and
    the caller gets a typed refusal it can render."""
    from core.compute.errors import ComputeError
    seen = {}

    def _fetch(meta, to_path, **kw):
        seen["dest"] = to_path                      # simulate a partial transfer
        p = Path(to_path); p.mkdir(parents=True, exist_ok=True)
        (p / "part.bin").write_bytes(b"half")
        raise ComputeError("transport.failed", "connection reset by siteA")
    monkeypatch.setattr(dmod, "fetch", _fetch)
    did = _remote_ds(layout="directory")
    r = _client().post(f"/api/datasets/{did}/mirror",
                       params={"project_id": "default"})
    assert r.status_code == 409, f"expected a typed refusal, got {r.status_code}"
    assert not Path(seen["dest"]).exists(), \
        "the half-copied tree must not survive a failed mirror"
    ent = get_entity(did)
    assert not (ent["metadata"] or {}).get("local_mirror"), \
        "a failed mirror must not claim a local copy"
    assert ent["artifact_path"] is None


def test_mirror_write_preserves_a_concurrent_metadata_stamp(monkeypatch):
    """The mirror's metadata write must not clobber a single-key stamp that
    landed DURING the transfer (close_run's drift flags are the live case)."""
    from core.graph.entities import patch_metadata

    def _fetch(meta, to_path, **kw):
        patch_metadata(did, {"source_changed": True})   # lands mid-transfer
        Path(to_path).parent.mkdir(parents=True, exist_ok=True)
        Path(to_path).write_bytes(b"bytes")
        return {"ok": True, "ref": "dref:y", "path": to_path}
    monkeypatch.setattr(dmod, "fetch", _fetch)
    did = _remote_ds()
    r = _client().post(f"/api/datasets/{did}/mirror",
                       params={"project_id": "default"})
    assert r.status_code == 200, r.text
    md = get_entity(did)["metadata"]
    assert md.get("local_mirror"), "the mirror must still be recorded"
    assert md.get("source_changed") is True, \
        "a stamp written during the transfer was lost to a whole-blob write"


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "pytest", __file__, "-v"]))
