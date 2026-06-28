"""A2 tests: MCP tool registration for make_revision + reproduce_from_exec.

Instantiates a FastMCP server, registers the revision tools, and verifies
both tools appear in the server's tool list with the expected names +
input schemas. Also exercises the tools via the in-process gateway end
to end (the path the dispatcher uses) for a roundtrip sanity check.

Run: .venv/bin/python tests/test_revisions_mcp.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_rev_mcp_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "rm.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_register_revision_tools():
    print("\n[1] register_revision_tools adds the revision tools to FastMCP")
    init_db()
    from mcp.server.fastmcp import FastMCP
    from content.bio.mcp_servers.aba_core.tools.revisions import register_revision_tools

    mcp = FastMCP(name="test")
    register_revision_tools(mcp)

    # FastMCP exposes tools via async list_tools()
    async def _list():
        tools = await mcp.list_tools()
        return [t.name for t in tools]

    names = asyncio.run(_list())
    check("make_revision registered", "make_revision" in names,
          f"got {names}")
    check("reproduce_from_exec registered", "reproduce_from_exec" in names)
    check("delete_revision registered", "delete_revision" in names,
          f"got {names}")
    check("list_revisions registered", "list_revisions" in names,
          f"got {names}")
    check("set_current_revision registered",
          "set_current_revision" in names, f"got {names}")
    # The five core revision tools, plus the env-reproduction trio added by
    # the provenance P4/P5 work (1b60e8c, 2026-06-26): diff_env / rebuild_env
    # / export_reproduction_bundle. Kept as an exact-set check so an
    # accidental leak (e.g. a lifecycle helper registered by mistake) is
    # still caught.
    expected = {"make_revision", "reproduce_from_exec",
                "delete_revision", "list_revisions", "set_current_revision",
                "diff_env", "rebuild_env", "export_reproduction_bundle"}
    check("revision tool set is exactly the expected eight",
          set(names) == expected, f"got {names}")


def test_make_server_includes_revision_tools():
    print("\n[2] aba_core.make_server() includes the revision tools")
    from content.bio.mcp_servers.aba_core.server import make_server

    mcp = make_server()

    async def _list():
        tools = await mcp.list_tools()
        return {t.name for t in tools}

    names = asyncio.run(_list())
    check("make_revision in catalog", "make_revision" in names,
          f"missing — catalog had {sorted(names)[:10]}…")
    check("reproduce_from_exec in catalog",
          "reproduce_from_exec" in names)


def test_tool_descriptions_have_guardrails():
    print("\n[3] tool descriptions contain the 'use only when user asks' framing")
    from mcp.server.fastmcp import FastMCP
    from content.bio.mcp_servers.aba_core.tools.revisions import register_revision_tools

    mcp = FastMCP(name="test")
    register_revision_tools(mcp)

    async def _list():
        return await mcp.list_tools()

    tools = asyncio.run(_list())
    by_name = {t.name: t for t in tools}

    mr = by_name.get("make_revision")
    check("make_revision tool exists", mr is not None)
    if mr:
        desc = (mr.description or "").lower()
        # Guardrail: docstring discourages calling unprompted. Wording
        # has drifted ("only when the user" → "Do NOT call this on your
        # own initiative"); accept either phrasing as long as the
        # don't-self-trigger framing is there.
        check("make_revision description discourages unprompted calls",
              ("only when the user" in desc or "only when user" in desc
               or "do not call this on your own" in desc
               or "do not use it to" in desc),
              f"desc: {desc[:200]}")
        check("make_revision description mentions wasRevisionOf",
              "wasrevisionof" in desc)

    rp = by_name.get("reproduce_from_exec")
    check("reproduce_from_exec tool exists", rp is not None)
    if rp:
        desc = (rp.description or "").lower()
        check("reproduce_from_exec description mentions drift",
              "drift" in desc)


def test_make_revision_end_to_end_via_handler():
    """Call the tool handler directly (bypassing the gateway since we
    don't want to spin up a full event loop just for a roundtrip
    sanity check). The schema test above covers the catalog wiring;
    this confirms the call path through the @mcp.tool() handler.
    """
    print("\n[4] make_revision handler executes against a real figure")
    # Make a seed figure first
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[4,5,6])\n"
        "plt.savefig('mcp_seed.png'); plt.close('all')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": "thr_mcp", "tool_use_id": "tu_mcp"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id="thr_mcp",
    )
    # Post Phase 5: explicitly materialize the figure artifact for the test
    ex = res.get("exec_id")
    check("seed exec_id present", isinstance(ex, str))
    if not ex:
        return
    parent_id = pin_artifact(ex, "figure", 0, wrap_in_result=False,
                              thread_id="thr_mcp")["entity_id"]

    # Get the registered tool's function and call it directly
    from mcp.server.fastmcp import FastMCP
    from content.bio.mcp_servers.aba_core.tools.revisions import register_revision_tools
    mcp = FastMCP(name="t")
    register_revision_tools(mcp)

    async def _call_make_revision():
        # FastMCP's call_tool returns content blocks; we can also use the
        # raw handler accessible via the tool manager.
        return await mcp.call_tool("make_revision", {
            "entity_id": parent_id,
            "modified_code": (
                "import matplotlib\nmatplotlib.use('Agg')\n"
                "import matplotlib.pyplot as plt\n"
                "plt.figure(); plt.plot([1,2,3],[40,50,60])\n"
                "plt.savefig('mcp_rev.png'); plt.close('all')\n"
            ),
            "title": "MCP revision",
        })

    result = asyncio.run(_call_make_revision())
    # FastMCP returns a tuple of (content_blocks, structured_output) on
    # newer SDK versions; older returns just content blocks. Normalize.
    if isinstance(result, tuple):
        content_blocks, structured = result
    else:
        content_blocks, structured = result, None

    # The handler returns a dict; FastMCP serializes it as JSON content.
    # Pull the structured value out either way.
    import json as _json
    text = None
    if structured is not None:
        # FastMCP wraps the dict under a "result" key when serializing
        # for structured_output — peel it back to the original handler return.
        if isinstance(structured, dict) and "result" in structured and len(structured) == 1:
            structured = structured["result"]
        out = structured
    else:
        for block in content_blocks or []:
            t = getattr(block, "text", None)
            if t:
                text = t
                break
        try:
            out = _json.loads(text) if text else {}
        except _json.JSONDecodeError:
            out = {"_raw": text}

    check("handler returned a dict", isinstance(out, dict),
          f"got {type(out).__name__}: {str(out)[:200]}")
    if isinstance(out, dict):
        check("response includes new_entity_id",
              isinstance(out.get("new_entity_id"), str),
              f"keys: {list(out.keys())}")
        check("wasRevisionOf points at parent",
              out.get("wasRevisionOf") == parent_id)


def test_reproduce_handler():
    print("\n[5] reproduce_from_exec handler executes against a real figure")
    from content.bio.tools.run_exec import run_python
    from content.bio.lifecycle.registry import register_artifacts_from_tool_result
    from content.bio.lifecycle.artifacts import pin_artifact
    code = (
        "import matplotlib\nmatplotlib.use('Agg')\n"
        "import matplotlib.pyplot as plt\n"
        "plt.figure(); plt.plot([1,2,3],[7,8,9])\n"
        "plt.savefig('repro.png'); plt.close('all')\n"
    )
    res = run_python({"code": code}, ctx={"thread_id": "thr_mcp2", "tool_use_id": "tu_mcp2"})
    register_artifacts_from_tool_result(
        tool_name="run_python", tool_input={"code": code},
        result_obj=res, focused_entity_id=None,
        analysis_ctx={}, thread_id="thr_mcp2",
    )
    ex = res.get("exec_id")
    if not ex:
        check("seed exec_id present", False)
        return
    parent_id = pin_artifact(ex, "figure", 0, wrap_in_result=False,
                              thread_id="thr_mcp2")["entity_id"]

    from mcp.server.fastmcp import FastMCP
    from content.bio.mcp_servers.aba_core.tools.revisions import register_revision_tools
    mcp = FastMCP(name="t2")
    register_revision_tools(mcp)

    async def _call():
        return await mcp.call_tool("reproduce_from_exec", {"entity_id": parent_id})

    result = asyncio.run(_call())
    # Normalize: FastMCP returns (content_blocks, structured) on newer
    # versions; older returns just content_blocks. Either way the handler's
    # dict should be reachable as JSON in the first text content block.
    import json as _json
    out = None
    if isinstance(result, tuple):
        content_blocks, structured = result
        if isinstance(structured, dict) and "result" in structured and len(structured) == 1:
            out = structured["result"]
        elif isinstance(structured, dict):
            out = structured
    else:
        content_blocks = result
    if out is None:
        text = None
        for block in content_blocks or []:
            t = getattr(block, "text", None)
            if t:
                text = t
                break
        try:
            out = _json.loads(text) if text else {}
        except _json.JSONDecodeError:
            out = {"_raw": text}
    if not isinstance(out, dict):
        check("output is a dict", False, f"got {type(out).__name__}: {out!r}")
        return
    check("reproduced = True", out.get("reproduced") is True,
          f"got out={out!r}")
    check("new_exec_id present", isinstance(out.get("new_exec_id"), str))
    check("env_drift = False (same kernel)",
          out.get("env_drift") is False)


def main() -> int:
    test_register_revision_tools()
    test_make_server_includes_revision_tools()
    test_tool_descriptions_have_guardrails()
    test_make_revision_end_to_end_via_handler()
    test_reproduce_handler()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL REVISIONS-MCP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
