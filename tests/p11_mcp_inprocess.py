"""Phase 6.A scaffold contract (misc/phase6_mcp_wrapping.md): the
in-process MCP server registers, connects via memory transport, and
participates in `list_tools()` / `status()` exactly like a stdio-spawned
server. Today it carries zero tools — the gateway shows it as healthy
and empty.

Subsequent sub-phases extend this test with per-cluster invariants:
6.B asserts `aba_core:list_capabilities` appears, etc.

Run:
    .venv/bin/python tests/p11_mcp_inprocess.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

# Isolated DB so importing main.py / content.bio doesn't touch the
# user's actual data. The gateway itself doesn't use SQLite but
# content.bio importing might.
_tmp = tempfile.mkdtemp(prefix="aba_p11_")
os.environ.setdefault("ABA_DB_PATH", os.path.join(_tmp, "test.db"))


def _fresh_gateway():
    """Tear down + clear gateway state, then return the public surface."""
    from core.runtime.mcp import _reset_for_testing
    _reset_for_testing()
    from core.runtime.mcp import (
        register_inprocess_server, list_tools, status, shutdown,
        is_mcp_tool, call,
    )
    return {
        "register": register_inprocess_server,
        "list_tools": list_tools,
        "status": status,
        "shutdown": shutdown,
        "is_mcp_tool": is_mcp_tool,
        "call": call,
    }


def test_aba_core_connects_via_memory_transport():
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    out = g["register"]("aba_core", make_server)
    assert out["status"] == "connected", out
    assert out["server"] == "aba_core"
    assert out["tools"] == []   # 6.A: zero tools registered

    s = g["status"]()
    assert s["started"] is True
    servers = {srv["name"]: srv for srv in s["servers"]}
    assert "aba_core" in servers
    assert servers["aba_core"]["state"] == "connected"
    assert servers["aba_core"]["tools"] == 0
    assert servers["aba_core"]["last_error"] is None
    g["shutdown"]()


def test_aba_core_list_tools_is_empty_in_6A():
    """Today the in-process server contributes no entries to the
    aggregated `list_tools()` (the agent's tool catalog). Sub-phases
    6.B+ extend this with real tool counts."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)
    tools = g["list_tools"]()
    # 6.A: aba_core is connected but registers 0 tools. list_tools
    # iterates all connected handles; with only aba_core registered
    # and no stdio servers configured, the aggregate is empty.
    assert tools == [], f"expected 0 tools, got {len(tools)}: {[t['name'] for t in tools]}"
    g["shutdown"]()


def test_idempotent_register():
    """A second register_inprocess_server('aba_core', ...) call must
    short-circuit (status='already_connected') rather than rebuilding
    the server. This mirrors add_server's idempotency for stdio
    servers — needed so a uvicorn reload doesn't pile up handles."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)
    out = g["register"]("aba_core", make_server)
    assert out["status"] == "already_connected", out
    g["shutdown"]()


def test_unknown_mcp_tool_call_yields_clean_error():
    """A bogus 'aba_core:noop' lookup goes through gateway.call() but
    raw_name isn't registered — the underlying SDK returns an
    is_error response. We assert the wire shape carries the error
    cleanly rather than throwing across the gateway boundary."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)
    # is_mcp_tool returns False because aba_core has no tool by that name.
    assert g["is_mcp_tool"]("aba_core:noop") is False
    # gateway.call still goes through, but the SDK reports the error.
    r = g["call"]("aba_core:noop", {})
    # Wire shape: {'status': 'ok'|'error', 'content': '...', 'is_error': bool}
    # OR {'status': 'error', 'note': '...'} from the gateway itself.
    # Either is acceptable as long as we don't crash.
    assert isinstance(r, dict), r
    assert ("error" in r.get("status", "") or r.get("is_error")), r
    g["shutdown"]()


def main() -> int:
    tests = [
        test_aba_core_connects_via_memory_transport,
        test_aba_core_list_tools_is_empty_in_6A,
        test_idempotent_register,
        test_unknown_mcp_tool_call_yields_clean_error,
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
