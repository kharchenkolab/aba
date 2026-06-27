"""Phase 2, 2A — typed derivation + actor at the create_entity seam (additive,
non-breaking: optional now, enforced in 2C)."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_phase2_2a_")
os.environ.update({
    "ABA_DB_PATH": str(Path(_tmp) / "t.db"),
    "ABA_RUNTIME_DIR": _tmp,
    "ARTIFACTS_DIR": str(Path(_tmp) / "artifacts"),
    "ABA_WORK_DIR": str(Path(_tmp) / "work"),
    "DATA_DIR": str(Path(_tmp) / "data"),
})
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db   # noqa: E402
init_db()
from core.graph.entities import create_entity, get_entity   # noqa: E402
from core.graph import derivation as D   # noqa: E402


def test_derivation_constructors():
    assert D.exec_derivation("ex1") == {"kind": "exec", "exec_id": "ex1"}
    assert D.derived_from(["a", "b"]) == {"kind": "derived_from", "sources": ["a", "b"]}
    assert D.imported("upload") == {"kind": "imported", "source": "upload"}
    assert D.manual() == {"kind": "manual"}
    assert D.legacy() == {"kind": "legacy"}
    assert D.agent_actor("run9") == "agent:run9"
    assert D.human_actor() == "human:local"
    assert D.is_valid(D.manual()) and not D.is_valid(None) and not D.is_valid({"kind": "x"})


def test_create_stores_derivation_and_actor():
    eid = create_entity(entity_type="narrative", title="n1",
                        derivation=D.manual(), actor=D.human_actor())
    e = get_entity(eid)
    assert e["derivation"] == {"kind": "manual"}
    assert e["actor"] == "human:local"


def test_create_auto_derives_exec_from_exec_id():
    eid = create_entity(entity_type="figure", title="f1", artifact_path="/tmp/f.png",
                        exec_id="ex42", actor=D.agent_actor("run1"))
    e = get_entity(eid)
    assert e["derivation"] == {"kind": "exec", "exec_id": "ex42"}   # auto-derived
    assert e["actor"] == "agent:run1"


def test_create_derived_from():
    eid = create_entity(entity_type="finding", title="find1",
                        derivation=D.derived_from(["res1", "res2"]), actor=D.human_actor())
    assert get_entity(eid)["derivation"] == {"kind": "derived_from", "sources": ["res1", "res2"]}


def test_create_without_derivation_is_none_for_now():
    # 2A is additive: an un-updated call site still works; derivation stays None
    # until 2B wires it and 2C enforces it.
    eid = create_entity(entity_type="narrative", title="n2")
    assert get_entity(eid)["derivation"] is None
    assert get_entity(eid)["actor"] is None
