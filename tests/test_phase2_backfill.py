"""Phase 2, 2D — legacy backfill: existing (pre-provenance) entities get a typed
derivation inferred from exec_id / lineage edges, or an honest `legacy` marker."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_phase2_2d_")
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
from core.graph.entities import get_entity   # noqa: E402
from core.graph.derivation_backfill import backfill_derivations   # noqa: E402


def _seed_legacy():
    """Raw-insert pre-provenance rows (derivation/actor NULL) to simulate an old DB."""
    with _conn() as c:
        c.execute("INSERT INTO entities (id,type,title,status,exec_id,created_at,updated_at)"
                  " VALUES ('e_exec','figure','f','active','ex9','t','t')")
        c.execute("INSERT INTO entities (id,type,title,status,created_at,updated_at)"
                  " VALUES ('e_target','figure','tg','active','t','t')")
        c.execute("INSERT INTO entities (id,type,title,status,created_at,updated_at)"
                  " VALUES ('e_edge','result','r','active','t','t')")
        c.execute("INSERT INTO entities (id,type,title,status,created_at,updated_at)"
                  " VALUES ('e_bare','narrative','n','active','t','t')")
        c.execute("INSERT INTO entity_edges (source_id,target_id,rel_type,created_at)"
                  " VALUES ('e_edge','e_target','wasDerivedFrom','t')")
        c.commit()


def test_backfill_infers_or_marks_legacy():
    _seed_legacy()
    assert get_entity("e_bare")["derivation"] is None   # pre-backfill: NULL
    n = backfill_derivations()
    assert n >= 4   # my 4 + init_db's default workspace entity
    # exec_id -> exec
    assert get_entity("e_exec")["derivation"] == {"kind": "exec", "exec_id": "ex9"}
    # came-from edge -> derived_from([targets])
    assert get_entity("e_edge")["derivation"] == {"kind": "derived_from", "sources": ["e_target"]}
    # nothing to infer -> honest legacy marker (never fabricated)
    assert get_entity("e_bare")["derivation"] == {"kind": "legacy"}
    # historical actor is unknowable -> legacy
    assert get_entity("e_bare")["actor"] == "legacy"
    assert get_entity("e_exec")["actor"] == "legacy"


def test_backfill_is_idempotent():
    # already-backfilled rows are untouched on a second run
    assert backfill_derivations() == 0


def test_backfill_fires_on_multi_mode_open(tmp_path):
    """In MULTI mode (no ABA_DB_PATH) opening a project backfills its legacy
    entities. Verified in a clean subprocess — THIS module runs SINGLE (it sets
    ABA_DB_PATH), where the per-project ensure_opened path no-ops."""
    import subprocess
    import sys as _sys
    import textwrap
    script = textwrap.dedent(f'''
        import os, sys
        os.environ["ABA_RUNTIME_DIR"] = {str(tmp_path)!r}
        os.environ.pop("ABA_DB_PATH", None)
        sys.path.insert(0, {str(ROOT / "backend")!r})
        import core.projects as P
        from core.graph._schema import _conn, init_db
        from core.graph.entities import get_entity
        assert P.SINGLE is False, "expected MULTI mode"
        with P.bind("p"):
            init_db()
            with _conn() as c:
                c.execute("INSERT INTO entities (id,type,title,status,created_at,updated_at)"
                          " VALUES ('e','narrative','x','active','t','t')"); c.commit()
            assert get_entity("e")["derivation"] is None
        P._opened_pids.discard("p")
        with P.bind("p"):
            P.ensure_opened("p")
            assert get_entity("e")["derivation"] == {{"kind": "legacy"}}, "backfill did not fire on open"
        print("WIRING_OK")
    ''')
    r = subprocess.run([_sys.executable, "-c", script], capture_output=True, text=True)
    assert "WIRING_OK" in r.stdout, r.stdout + r.stderr


def test_derivation_coverage_invariant():
    from core.graph.entities import create_entity
    from core.graph.derivation import manual
    from core.graph.derivation_backfill import derivation_coverage_violations
    create_entity(entity_type="narrative", title="cov1", derivation=manual())          # threaded
    create_entity(entity_type="figure", title="cov2", artifact_path="/tmp/c.png", exec_id="exC")  # exec auto
    with _conn() as c:                                                                   # raw legacy NULL
        c.execute("INSERT INTO entities (id,type,title,status,created_at,updated_at)"
                  " VALUES ('cov_raw','narrative','x','active','t','t')"); c.commit()
    assert derivation_coverage_violations() == []   # backfill covers it -> no entity left NULL
