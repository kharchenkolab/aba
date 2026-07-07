"""Parity test: the in-kernel `aba` read verbs (core.exec.kernels.aba_inkernel._Aba,
which query the project SQLite directly with no backend import) return the SAME
entities as the backend read-port (core.graph.entities.find_entities / get_entity)
on the same DB. Guards the Phase-1 tool_library work against schema/predicate drift.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _fresh_db():
    tmp = tempfile.mkdtemp(prefix="aba_inkernel_")
    dbp = str(Path(tmp) / "project.db")
    os.environ["ABA_DB_PATH"] = dbp          # backend read-port target
    os.environ["ABA_PROJECT_DB"] = dbp       # in-kernel aba target (same file)
    from core.graph._schema import init_db, set_db_path
    set_db_path(dbp)
    init_db()
    return dbp


def _seed():
    from core.graph.entities import create_entity, archive_entity
    ids = {}
    ids["ds1"] = create_entity(entity_type="dataset", title="counts matrix")
    ids["ds2"] = create_entity(entity_type="dataset", title="metadata table")
    ids["fig"] = create_entity(entity_type="figure", title="UMAP plot")
    ids["res"] = create_entity(entity_type="result", title="clustering result")
    ids["old"] = create_entity(entity_type="figure", title="old draft figure")
    archive_entity(ids["old"])
    return ids


def _ids(rows):
    return sorted(r["id"] for r in rows)


def test_find_parity_by_type():
    _fresh_db()
    _seed()
    from core.graph.entities import find_entities
    from core.exec.kernels.aba_inkernel import _Aba
    aba = _Aba(db=os.environ["ABA_PROJECT_DB"])
    for typ in ("dataset", "figure", "result", "narrative"):
        backend = find_entities(type=typ, include_archived=False)
        kernel = aba.find(type=typ, include_archived=False, limit=None)
        assert _ids(kernel) == _ids(backend), f"type={typ}: {_ids(kernel)} != {_ids(backend)}"


def test_find_excludes_archived_by_default():
    _fresh_db()
    ids = _seed()
    from core.exec.kernels.aba_inkernel import _Aba
    aba = _Aba(db=os.environ["ABA_PROJECT_DB"])
    figs = _ids(aba.find(type="figure"))            # default: hide archived
    assert ids["fig"] in figs
    assert ids["old"] not in figs, "archived figure leaked into default find"
    with_arch = _ids(aba.find(type="figure", include_archived=True))
    assert ids["old"] in with_arch


def test_find_contains_substring():
    _fresh_db()
    _seed()
    from core.graph.entities import find_entities
    from core.exec.kernels.aba_inkernel import _Aba
    aba = _Aba(db=os.environ["ABA_PROJECT_DB"])
    kernel = aba.find(contains="matrix", limit=None)
    backend = find_entities(title_query="matrix", include_archived=False)
    assert _ids(kernel) == _ids(backend)
    assert len(kernel) == 1


def test_get_parity():
    _fresh_db()
    ids = _seed()
    from core.graph.entities import get_entity
    from core.exec.kernels.aba_inkernel import _Aba
    aba = _Aba(db=os.environ["ABA_PROJECT_DB"])
    k = aba.get(ids["res"])
    b = get_entity(ids["res"])
    assert k is not None and b is not None
    assert k["id"] == b["id"] == ids["res"]
    assert k["type"] == b["type"] == "result"
    assert k["title"] == b["title"] == "clustering result"
    assert aba.get("nonexistent_id") is None


def test_types_counts():
    _fresh_db()
    _seed()
    from core.exec.kernels.aba_inkernel import _Aba
    aba = _Aba(db=os.environ["ABA_PROJECT_DB"])
    counts = {r["type"]: r["n"] for r in aba.types()}
    assert counts.get("dataset") == 2
    assert counts.get("figure") == 1        # archived 'old' excluded
    assert counts.get("result") == 1


def test_no_db_bound_raises():
    from core.exec.kernels.aba_inkernel import _Aba
    saved = os.environ.pop("ABA_PROJECT_DB", None)  # ensure truly unbound
    try:
        aba = _Aba(db=None)
        try:
            aba.find(type="dataset")
        except RuntimeError as e:
            assert "no project database" in str(e).lower()
        else:
            raise AssertionError("expected RuntimeError when no DB bound")
    finally:
        if saved is not None:
            os.environ["ABA_PROJECT_DB"] = saved


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all parity tests passed")
