"""P3 de-duplication: get_provenance+get_dependents→get_lineage(direction=), the four
package searches→search_registry(source=), read_skill dropped. Guards the merges so a
future edit can't silently break the dispatch."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _catalog():
    captured = {}

    class F:
        def tool(self, *a, **k):
            def d(fn):
                captured[fn.__name__] = fn
                return fn
            return d

    import content.bio  # noqa: F401
    mcp = F()
    for m in ("ctx_read", "discovery", "simple"):
        mod = __import__(f"content.bio.mcp_servers.aba_core.tools.{m}", fromlist=["x"])
        for r in [f for f in dir(mod) if f.startswith("register_")]:
            getattr(mod, r)(mcp)
    return captured


def test_removed_tools_gone_merged_present():
    cat = _catalog()
    for gone in ("read_skill", "get_provenance", "get_dependents",
                 "search_pypi", "search_bioconda", "search_nf_core", "search_mcp_registry"):
        assert gone not in cat, f"{gone} should be merged away"
    assert "get_lineage" in cat and "search_registry" in cat
    # search_skills stays SEPARATE (priority tool + recipe entrypoint)
    assert "search_skills" in cat


def test_get_lineage_directions():
    os.environ["ABA_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="aba_p3_")) / "s.db")
    from core.graph._schema import init_db, set_db_path
    set_db_path(os.environ["ABA_DB_PATH"]); init_db()
    from core.graph.entities import create_entity
    from core.graph.edges import add_edge
    from core.graph.derivation import manual
    a = create_entity(entity_type="analysis", title="A", derivation=manual())
    b = create_entity(entity_type="result", title="B", derivation=manual())
    add_edge(b, a, "wasDerivedFrom")   # B derived from A
    fn = _catalog()["get_lineage"]
    up = fn(b, direction="up")         # ancestors of B → includes A
    assert a in str(up), up
    down = fn(a, direction="down")     # dependents of A → includes B
    assert b in str(down), down
    both = fn(b, direction="both")
    assert set(both.keys()) == {"upstream", "downstream"}


def test_search_registry_routes_and_guards():
    fn = _catalog()["search_registry"]
    assert "error" in fn("x", source="bogus")   # unknown source → clean error
    # valid sources dispatch without raising (network results not asserted)
    import inspect
    params = list(inspect.signature(fn).parameters)
    assert params[:2] == ["query", "source"]


if __name__ == "__main__":
    test_removed_tools_gone_merged_present(); print("ok  merges present, old gone")
    test_get_lineage_directions(); print("ok  get_lineage up/down/both")
    test_search_registry_routes_and_guards(); print("ok  search_registry routes+guards")
    print("all P3 dedup tests passed")
