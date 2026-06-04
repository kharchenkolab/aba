"""Phase 4.5 (misc/phase4_entity_types.md): write-side validators
(warning-only).

Five invariants:

1. Good creates produce no warning. (Figure with title + artifact_path)
2. Bad creates DO produce a warning. (Figure without artifact_path)
3. Good edges produce no warning. (Result --supports--> Claim)
4. Bad edges DO produce a warning. (Workspace --supports--> Claim is
   not in workspace.allowed_edges.out)
5. Unknown types are silently skipped (no warning, no crash). Critical
   for legacy data + synthetic test entities.

Deterministic. Isolated temp DB. No model.

Run:
    .venv/bin/python tests/p9_entity_type_validation.py
"""
from __future__ import annotations
import logging
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

_tmp = tempfile.mkdtemp(prefix="aba_v_")
os.environ["ABA_DB_PATH"] = os.path.join(_tmp, "test.db")
from core.graph import _schema  # noqa: E402
_schema.set_db_path(os.environ["ABA_DB_PATH"])
_schema.init_db()

# Importing bio loads the entity-type YAMLs.
import content.bio  # noqa: E402, F401
from core.graph.entities import create_entity  # noqa: E402
from core.graph.edges import add_edge  # noqa: E402


class _CaptureHandler(logging.Handler):
    """Capture WARNING+ records emitted by core.graph.* loggers."""
    def __init__(self):
        super().__init__(level=logging.WARNING)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):  # noqa: D401
        self.records.append(record)


def _capture():
    h = _CaptureHandler()
    # Validators emit on these two named loggers.
    for name in ("core.graph.entities", "core.graph.edges"):
        logging.getLogger(name).addHandler(h)
    return h


def _drop(h: _CaptureHandler):
    for name in ("core.graph.entities", "core.graph.edges"):
        logging.getLogger(name).removeHandler(h)


def test_good_create_no_warning():
    h = _capture()
    try:
        create_entity(entity_type="figure", title="t",
                      artifact_path="/x/y.png")
    finally:
        _drop(h)
    msgs = [r.getMessage() for r in h.records]
    assert not msgs, f"unexpected warnings: {msgs}"


def test_bad_create_warns_on_missing_artifact_path():
    h = _capture()
    try:
        # figure's schema.required = [title, artifact_path]; omit the latter.
        create_entity(entity_type="figure", title="missing-artifact")
    finally:
        _drop(h)
    msgs = [r.getMessage() for r in h.records]
    assert any("artifact_path" in m and "figure" in m for m in msgs), \
        f"expected a warning about missing artifact_path, got: {msgs}"


def test_unknown_type_silently_skipped():
    """Synthetic test type 'type_a' isn't in the registry — validation
    should be a no-op, not a crash or a warning."""
    h = _capture()
    try:
        create_entity(entity_type="type_a", title="t",
                      entity_id="ent_unknown_a")
    finally:
        _drop(h)
    msgs = [r.getMessage() for r in h.records if "type_a" in r.getMessage()]
    assert not msgs, f"unknown type should not warn: {msgs}"


def test_good_edge_no_warning():
    # Pre-create both endpoints.
    cid = create_entity(entity_type="claim", title="c1",
                        metadata={"statement": "x", "confidence": "preliminary"})
    rid = create_entity(entity_type="result", title="r1")
    h = _capture()
    try:
        add_edge(rid, cid, "supports")
    finally:
        _drop(h)
    msgs = [r.getMessage() for r in h.records if "supports" in r.getMessage()]
    assert not msgs, f"good edge should not warn: {msgs}"


def test_bad_edge_warns():
    # Workspace.allowed_edges.out = [] — supports is not in it.
    cid = create_entity(entity_type="claim", title="c2",
                        metadata={"statement": "x", "confidence": "preliminary"})
    h = _capture()
    try:
        add_edge("workspace", cid, "supports")
    finally:
        _drop(h)
    msgs = [r.getMessage() for r in h.records if "workspace" in r.getMessage()]
    assert msgs, "expected workspace-out-supports warning"


def test_unknown_endpoint_silently_skipped():
    # Source doesn't exist in DB; _edge_validate returns silently.
    h = _capture()
    try:
        add_edge("ent_does_not_exist", "workspace", "anything")
    finally:
        _drop(h)
    msgs = [r.getMessage() for r in h.records]
    assert not msgs, f"missing endpoint should not warn: {msgs}"


def main() -> int:
    tests = [
        test_good_create_no_warning,
        test_bad_create_warns_on_missing_artifact_path,
        test_unknown_type_silently_skipped,
        test_good_edge_no_warning,
        test_bad_edge_warns,
        test_unknown_endpoint_silently_skipped,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
    if failed:
        print(f"\n{len(failed)} / {len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
