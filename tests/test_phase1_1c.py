"""Phase 1, 1C — cheap cross-cut holes.
  1C.1  lean-mode behavior is bundle-sourced (layerable), not a raw disk read.
  1C.2  create_entity honors status_model.initial, not a hardcoded 'active'.
         (All current bio types declare initial='active', so the fix is invisible
          today but future-proofs a type that declares otherwise — tested via a
          synthetic registry spec.)
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_phase1_1c_")
os.environ.update({
    "ABA_DB_PATH": str(Path(_tmp) / "t.db"),
    "ABA_RUNTIME_DIR": _tmp,
    "ARTIFACTS_DIR": str(Path(_tmp) / "artifacts"),
    "ABA_WORK_DIR": str(Path(_tmp) / "work"),
    "DATA_DIR": str(Path(_tmp) / "data"),
})
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db, _conn   # noqa: E402
init_db()
import content.bio  # noqa: E402,F401 — loads the entity-type registry
from core.graph.entities import create_entity   # noqa: E402


def _status(eid: str) -> str:
    with _conn() as c:
        return c.execute("SELECT status FROM entities WHERE id=?", (eid,)).fetchone()[0]


def test_behavior_slim_is_bundle_sourced(monkeypatch):
    """1C.1: the lean-mode behavior block resolves through the bundle, so a
    lab/institution override reaches it — the first positive bundle-coverage check."""
    from content.bio.prompts import build
    assert (ROOT / "backend/system_bundle/rules/behavior_slim.md").is_file()
    assert not (ROOT / "backend/content/bio/prompts/behavior_slim.md").exists()

    class _FakeBundle:
        def rule_content(self, name):
            return "LAB-OVERRIDE-SENTINEL" if name == "behavior_slim.md" else None
    monkeypatch.setattr("core.bundle.active.get_bundle", lambda: _FakeBundle())
    assert build._bundle_rule_text("behavior_slim.md") == "LAB-OVERRIDE-SENTINEL"


def test_create_entity_uses_registry_initial(monkeypatch):
    """1C.2: create_entity reads status_model.initial from the registry, not a literal."""
    import core.entity_types.registry as reg

    class _Spec:
        def initial_status(self):
            return "draft"

    monkeypatch.setattr(reg, "get_type", lambda name: _Spec())
    assert _status(create_entity(entity_type="synthetic_t", title="d1")) == "draft"


def test_create_entity_real_type_defaults_active():
    """Real bio types declare status_model.initial='active' — end-to-end, unchanged."""
    assert _status(create_entity(entity_type="figure", title="f1", artifact_path="/tmp/f1.png")) == "active"


def test_create_entity_unknown_type_falls_back_active():
    """A type the registry doesn't know falls back to 'active' (no crash)."""
    assert _status(create_entity(entity_type="zzz_not_a_type", title="x1")) == "active"
