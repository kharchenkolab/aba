"""Sync-RPC (tool_library S1): the in-kernel `aba` backend-reads (search/capabilities)
loop back to /api/aba_rpc. Fast checks — dispatch parity, graceful degradation, token.
The full kernel↔server loopback is exercised by the integration smoke (not CI: needs a
real kernel + a served app)."""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _db():
    os.environ["ABA_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="aba_rpc_")) / "s.db")
    from core.graph._schema import init_db, set_db_path
    set_db_path(os.environ["ABA_DB_PATH"]); init_db()


def test_rpc_dispatch_parity():
    _db()
    import content.bio  # noqa: F401
    import content.bio.services  # noqa: F401  registers aba_rpc
    from core.services import call_service
    from content.bio.tools.discovery import search_skills_tool
    from content.bio.tools.simple import list_capabilities_tool
    q = {"query": "differential expression", "limit": 5}
    assert call_service("aba_rpc", "search", q, None) == search_skills_tool(q)
    assert call_service("aba_rpc", "capabilities", {"query": "align"}, None) == \
        list_capabilities_tool({"query": "align"})
    from content.bio.tools.curation import find_reference_tool
    assert call_service("aba_rpc", "find_reference", {"organism": "human"}, None) == \
        find_reference_tool({"organism": "human", "role": None, "assembly": None, "all": None}, None)
    assert "error" in call_service("aba_rpc", "bogus", {}, None)


def test_rpc_graceful_degradation():
    from core.exec.kernels.aba_inkernel import _Aba
    saved = os.environ.pop("ABA_RPC_URL", None)
    try:
        aba = _Aba()
        try:
            aba._rpc("search", query="x")
        except RuntimeError as e:
            assert "background" in str(e).lower() or "aba_rpc_url" in str(e).lower()
        else:
            raise AssertionError("expected RuntimeError when no ABA_RPC_URL")
    finally:
        if saved is not None:
            os.environ["ABA_RPC_URL"] = saved


def test_rpc_token_stable():
    from core.config import rpc_token
    assert rpc_token() == rpc_token() and len(rpc_token()) >= 16


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"ok  {name}")
    print("all aba_rpc tests passed")
