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


def test_actor_mechanism():
    from core.runtime.actor import acting_as, current_actor
    assert current_actor() is None
    with acting_as("human:local"):
        assert current_actor() == "human:local"
        with acting_as("agent:r9"):          # nests + restores
            assert current_actor() == "agent:r9"
        assert current_actor() == "human:local"
    assert current_actor() is None


def test_create_entity_defaults_actor_from_ambient():
    from core.runtime.actor import acting_as
    with acting_as("human:local"):
        eid = create_entity(entity_type="narrative", title="amb1")
    assert get_entity(eid)["actor"] == "human:local"        # ambient default
    eid2 = create_entity(entity_type="narrative", title="amb2")
    assert get_entity(eid2)["actor"] is None                # no ambient -> None
    with acting_as("human:local"):
        eid3 = create_entity(entity_type="narrative", title="amb3", actor="agent:r1")
    assert get_entity(eid3)["actor"] == "agent:r1"          # explicit wins


def test_agent_actor_for_exec(monkeypatch):
    from core.graph import derivation as D
    import core.graph.exec_records as ER
    monkeypatch.setattr(ER, "get", lambda eid: {"run_id": "run77"} if eid == "x" else None)
    assert D.agent_actor_for_exec("x") == "agent:run77"
    assert D.agent_actor_for_exec(None) is None          # no exec_id
    assert D.agent_actor_for_exec("missing") is None      # exec not found
    monkeypatch.setattr(ER, "get", lambda eid: {"run_id": None})
    assert D.agent_actor_for_exec("anything") is None     # exec has no run_id


def test_create_thread_manual_derivation():
    from core.graph.threads import create_thread
    tid = create_thread("My investigation", "the question")
    assert get_entity(tid)["derivation"] == {"kind": "manual"}   # a thread is a container


def test_from_lineage():
    from core.graph.derivation import from_lineage, imported
    assert from_lineage({"wasDerivedFrom": ["a", "b"]}, imported("x")) == {"kind": "derived_from", "sources": ["a", "b"]}
    assert from_lineage({"wasDerivedFrom": "single"}, imported("x")) == {"kind": "derived_from", "sources": ["single"]}
    assert from_lineage(None, imported("x")) == {"kind": "imported", "source": "x"}
    assert from_lineage({"supports": ["c"]}, imported("x")) == {"kind": "imported", "source": "x"}  # non-derivation rel


def test_register_artifact_no_lineage_is_imported():
    from core.data.store import register
    eid = register("/tmp/y.csv", kind="dataset")
    assert get_entity(eid)["derivation"] == {"kind": "imported", "source": "y.csv"}


def test_warns_once_on_unbound_create(caplog, monkeypatch):
    import core.projects as P
    import core.graph.entities as E
    from core.graph.derivation import manual
    E._warned_unbound = False                          # reset the once-per-process dedupe
    monkeypatch.setattr(P, "current", lambda: None)     # simulate truly-unbound
    with caplog.at_level("WARNING"):
        create_entity(entity_type="narrative", title="unbound1", derivation=manual())
        create_entity(entity_type="narrative", title="unbound2", derivation=manual())
    warns = [r for r in caplog.records if "no bound project" in r.getMessage()]
    assert len(warns) == 1                              # once per process, not per call


def test_no_warn_when_bound(caplog, monkeypatch):
    import core.projects as P
    import core.graph.entities as E
    from core.graph.derivation import manual
    E._warned_unbound = False
    monkeypatch.setattr(P, "current", lambda: "proj1")  # bound
    with caplog.at_level("WARNING"):
        create_entity(entity_type="narrative", title="bound1", derivation=manual())
    assert not [r for r in caplog.records if "no bound project" in r.getMessage()]
