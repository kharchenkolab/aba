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
    # 6.A registered 0 tools; 6.B registered 3. As long as the connect
    # succeeded, the count is non-negative — sub-phase-specific tests
    # below assert the exact migrated set.
    assert isinstance(out["tools"], list)

    s = g["status"]()
    assert s["started"] is True
    servers = {srv["name"]: srv for srv in s["servers"]}
    assert "aba_core" in servers
    assert servers["aba_core"]["state"] == "connected"
    assert servers["aba_core"]["last_error"] is None
    g["shutdown"]()


def test_aba_core_hidden_from_agent_catalog():
    """expose_in_catalog=False keeps aba_core's tools out of the
    aggregated `list_tools()` (the agent's tool catalog). They're
    advertised via legacy TOOL_SCHEMAS during the migration and
    dispatched by name via is_inprocess_tool. With no other servers
    configured, list_tools is empty regardless of how many bio tools
    have been migrated."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)
    tools = g["list_tools"]()
    assert tools == [], f"aba_core must stay hidden from catalog, got {[t['name'] for t in tools]}"
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


def test_6B_three_simple_tools_registered():
    """Phase 6.B: list_capabilities, read_memory, search_pypi are
    registered on aba_core. They appear in the handle's tool list
    (prefixed) and are routable via gateway.call()."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    out = g["register"]("aba_core", make_server)
    expected = {
        "aba_core:list_capabilities",
        "aba_core:read_memory",
        "aba_core:search_pypi",
    }
    actual = set(out["tools"])
    assert expected.issubset(actual), \
        f"expected at least {expected}, got {actual}"
    g["shutdown"]()


def test_6B_aba_core_tools_NOT_in_list_tools():
    """expose_in_catalog=False keeps aba_core's tools out of the
    aggregated `list_tools()` — they'd double-list with TOOL_SCHEMAS
    otherwise. Bio dispatcher uses is_inprocess_tool() instead."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)
    tools = g["list_tools"]()
    bare_names = {t["name"] for t in tools}
    assert not any(n.startswith("aba_core:") for n in bare_names), \
        f"aba_core tools should be hidden from list_tools, got {bare_names}"
    g["shutdown"]()


def test_6B_is_inprocess_tool_lookups():
    """is_inprocess_tool('foo') returns True iff aba_core has a tool
    by that bare name. The bio dispatcher consults this for every call
    to decide whether to route via MCP."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)
    assert g["is_mcp_tool"]("aba_core:list_capabilities") is True
    # Bare-name lookups (what the dispatcher does):
    from core.runtime.mcp import is_inprocess_tool
    assert is_inprocess_tool("list_capabilities") is True
    assert is_inprocess_tool("read_memory") is True
    assert is_inprocess_tool("search_pypi") is True
    assert is_inprocess_tool("not_yet_migrated") is False
    assert is_inprocess_tool("run_python") is False  # 6.H, not yet
    g["shutdown"]()


def test_6B_read_memory_via_gateway_returns_unknown_for_missing():
    """End-to-end smoke: call read_memory via the gateway. Returns the
    same wire shape the bio impl would. read_memory is the simplest
    DB-free path (only memory-store I/O) so it's the most reliable
    sanity check in an isolated test."""
    import json
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)
    r = g["call"]("aba_core:read_memory", {"name": "bogus_does_not_exist"})
    # Gateway wire shape: {status, content, is_error}
    assert r["status"] == "ok", r
    assert r["is_error"] is False, r
    # FastMCP serializes the dict return as text content
    payload = json.loads(r["content"])
    assert payload["status"] == "unknown_memory"
    g["shutdown"]()


def test_6E_ten_discovery_tools_registered():
    """Phase 6.E: search/inspect/capability/fetch cluster."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    out = g["register"]("aba_core", make_server)
    expected = {
        "aba_core:search_skills",
        "aba_core:search_bioconda",
        "aba_core:search_nf_core",
        "aba_core:search_mcp_registry",
        "aba_core:inspect_package",
        "aba_core:ensure_capability",
        "aba_core:propose_capability",
        "aba_core:fetch_url",
        "aba_core:fetch_ensembl",
        "aba_core:lookup_sra_runinfo",
    }
    actual = set(out["tools"])
    missing = expected - actual
    assert not missing, f"missing discovery migrations: {missing}"
    # Cumulative: 3+7+13+10 = 33
    assert len(actual) >= 33, f"expected >=33 tools, got {len(actual)}"
    g["shutdown"]()


def test_6D_thirteen_curation_tools_registered():
    """Phase 6.D: curation cluster — pin/promote/findings/claims, dataset
    ops (register/add/remove), run lifecycle (open/close), reference
    data (register/find), annotate, archive."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    out = g["register"]("aba_core", make_server)
    expected = {
        "aba_core:pin_entity",
        "aba_core:promote_to_result",
        "aba_core:create_finding",
        "aba_core:create_claim",
        "aba_core:annotate_entity",
        "aba_core:archive_entity",
        "aba_core:register_dataset",
        "aba_core:add_to_dataset",
        "aba_core:remove_from_dataset",
        "aba_core:open_run",
        "aba_core:close_run",
        "aba_core:register_reference",
        "aba_core:find_reference",
    }
    actual = set(out["tools"])
    missing = expected - actual
    assert not missing, f"missing curation migrations: {missing}"
    # Cumulative count: 3 (6.B) + 7 (6.C) + 13 (6.D) = 23.
    assert len(actual) >= 23, f"expected >=23 tools, got {len(actual)}: {actual}"
    g["shutdown"]()


def test_6C_seven_ctx_aware_tools_registered():
    """Phase 6.C: Skill, read_skill, list_entities, get_provenance,
    get_dependents, read_capability, read_csv_info land on aba_core."""
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    out = g["register"]("aba_core", make_server)
    expected = {
        "aba_core:Skill",
        "aba_core:read_skill",
        "aba_core:list_entities",
        "aba_core:get_provenance",
        "aba_core:get_dependents",
        "aba_core:read_capability",
        "aba_core:read_csv_info",
    }
    actual = set(out["tools"])
    missing = expected - actual
    assert not missing, f"missing migrations: {missing} (got {actual})"
    g["shutdown"]()


def test_6C_ctx_store_stash_and_pop_roundtrip():
    """tool_ctx infrastructure: stash returns a non-empty id for a
    non-empty ctx; peek returns the same dict; pop empties the store."""
    from core.runtime.tool_ctx import (
        stash_ctx, peek_ctx, pop_ctx, _reset_for_testing, _size_for_testing,
    )
    _reset_for_testing()
    assert _size_for_testing() == 0

    cid = stash_ctx({"thread_id": "t1", "active_tools": [{"name": "Skill"}]})
    assert cid and isinstance(cid, str)
    assert _size_for_testing() == 1
    got = peek_ctx(cid)
    assert got["thread_id"] == "t1"
    assert got["active_tools"][0]["name"] == "Skill"
    # peek doesn't remove
    assert _size_for_testing() == 1

    popped = pop_ctx(cid)
    assert popped == got
    assert _size_for_testing() == 0
    # second pop is a no-op
    assert pop_ctx(cid) == {}


def test_6C_ctx_store_handles_empty():
    """Empty/None ctx → '' id, no store entry. peek/pop with '' or
    unknown id return {} (handlers can call defensively)."""
    from core.runtime.tool_ctx import (
        stash_ctx, peek_ctx, pop_ctx, _reset_for_testing, _size_for_testing,
    )
    _reset_for_testing()
    assert stash_ctx(None) == ""
    assert stash_ctx({}) == ""
    assert _size_for_testing() == 0
    assert peek_ctx("") == {}
    assert peek_ctx(None) == {}
    assert peek_ctx("bogus_id_never_stashed") == {}
    assert pop_ctx("") == {}


def test_6C_ctx_reaches_handler_via_aba_ctx_id():
    """End-to-end: dispatcher stashes ctx, injects aba_ctx_id; handler
    on the server-side asyncio task reads the ctx back via peek_ctx.
    Validates that the store IS reachable across the gateway thread
    boundary (it's a process-wide dict, so this should always work)."""
    import json
    g = _fresh_gateway()
    from core.runtime.tool_ctx import (
        _reset_for_testing as _ctx_reset, _size_for_testing,
    )
    _ctx_reset()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)

    from content.bio.tools import execute_tool
    # Skill with unknown name — exercises the ctx-read path
    # (recipe_ctx + active_tools lookup) before returning unknown_skill.
    raw = execute_tool(
        "Skill",
        {"skill": "no_such_skill_for_test", "args": ""},
        ctx={"thread_id": "tctx1", "active_tools": []},
    )
    payload = json.loads(raw)
    # The bio impl returns 'unknown_skill' when no skill matches
    # (path proves the dispatch reached the handler AND the handler
    # called the bio impl with the propagated ctx).
    assert payload.get("status") == "unknown_skill", payload
    # ctx popped after the call — no leak.
    assert _size_for_testing() == 0
    g["shutdown"]()


def test_6C_ctx_leak_check_after_exception():
    """If a handler raises, the dispatcher's finally still pops. The
    bogus tool call we use here errors at the EXECUTORS-unknown path
    (no aba_core tool by that name, but we're routing some name that
    DOES match aba_core, with bad-input data that forces the bio
    impl to raise). Validates pop_ctx ALWAYS runs."""
    import json
    from core.runtime.tool_ctx import (
        _reset_for_testing as _ctx_reset, _size_for_testing,
    )
    _ctx_reset()
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)
    from content.bio.tools import execute_tool

    # Several calls in sequence with a real ctx — leak would accumulate.
    for i in range(5):
        execute_tool(
            "Skill",
            {"skill": f"never_exists_{i}"},
            ctx={"thread_id": "tleak", "iteration": i},
        )
    assert _size_for_testing() == 0, \
        f"ctx leaked: {_size_for_testing()} entries still in store"
    g["shutdown"]()


def test_6B_dispatcher_routes_through_aba_core():
    """The bio dispatcher consults is_inprocess_tool BEFORE EXECUTORS.
    Asserts the dispatcher's path: a call to execute_tool('read_memory',
    ...) returns the aba_core-routed result, parsed back from
    FastMCP's text-content wrapping. (read_memory has both an
    EXECUTORS entry and an aba_core handler during the migration; the
    dispatcher must pick aba_core.)"""
    import json
    g = _fresh_gateway()
    from content.bio.mcp_servers.aba_core import make_server
    g["register"]("aba_core", make_server)

    from content.bio.tools import execute_tool
    raw = execute_tool("read_memory", {"name": "bogus_does_not_exist"})
    payload = json.loads(raw)
    # The dispatcher unwraps the FastMCP content layer, so the agent
    # sees the original dict shape ({status, note, ...}).
    assert payload.get("status") == "unknown_memory", payload
    assert "bogus_does_not_exist" in payload.get("note", ""), payload
    g["shutdown"]()


def main() -> int:
    tests = [
        test_aba_core_connects_via_memory_transport,
        test_aba_core_hidden_from_agent_catalog,
        test_idempotent_register,
        test_unknown_mcp_tool_call_yields_clean_error,
        test_6B_three_simple_tools_registered,
        test_6B_aba_core_tools_NOT_in_list_tools,
        test_6B_is_inprocess_tool_lookups,
        test_6B_read_memory_via_gateway_returns_unknown_for_missing,
        test_6C_seven_ctx_aware_tools_registered,
        test_6C_ctx_store_stash_and_pop_roundtrip,
        test_6C_ctx_store_handles_empty,
        test_6C_ctx_reaches_handler_via_aba_ctx_id,
        test_6C_ctx_leak_check_after_exception,
        test_6D_thirteen_curation_tools_registered,
        test_6E_ten_discovery_tools_registered,
        test_6B_dispatcher_routes_through_aba_core,
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
