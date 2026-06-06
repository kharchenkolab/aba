"""Stage 6 / Phase C tests: pin_cell MCP agent tool.

Mirrors test_revisions_mcp.py — verifies the tool is registered in the
aba_core catalog, has the expected guardrail wording, and executes
correctly against a real exec record.

Run: .venv/bin/python tests/test_cell_mcp.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_cell_mcp_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "cm.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"] = "/workspace/aba-runtime/envs"
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                  # noqa: E402
from core.graph import exec_records                      # noqa: E402
import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def test_register_cell_tools():
    print("\n[1] register_cell_tools adds pin_cell")
    init_db()
    from mcp.server.fastmcp import FastMCP
    from content.bio.mcp_servers.aba_core.tools.cells import register_cell_tools

    mcp = FastMCP(name="test")
    register_cell_tools(mcp)

    async def _list(): return [t.name for t in await mcp.list_tools()]
    names = asyncio.run(_list())
    check("pin_cell registered", "pin_cell" in names, f"got {names}")


def test_make_server_includes_pin_cell():
    print("\n[2] make_server() catalog includes pin_cell")
    from content.bio.mcp_servers.aba_core.server import make_server
    mcp = make_server()
    async def _list(): return {t.name for t in await mcp.list_tools()}
    names = asyncio.run(_list())
    check("pin_cell in full catalog", "pin_cell" in names,
          f"sample of catalog: {sorted(names)[:10]}…")


def test_guardrail_wording():
    print("\n[3] pin_cell description has 'use only when user' framing")
    from mcp.server.fastmcp import FastMCP
    from content.bio.mcp_servers.aba_core.tools.cells import register_cell_tools
    mcp = FastMCP(name="t")
    register_cell_tools(mcp)
    async def _list(): return await mcp.list_tools()
    tools = asyncio.run(_list())
    by_name = {t.name: t for t in tools}
    pc = by_name.get("pin_cell")
    check("pin_cell tool exists", pc is not None)
    if pc:
        desc = (pc.description or "").lower()
        check("description warns 'only when the user'",
              "only when the user" in desc,
              f"desc: {desc[:200]!r}")


def test_pin_cell_handler_happy():
    print("\n[4] pin_cell handler creates a cell + result")
    # Build a real exec record
    cwd = Path(_tmp) / "cell_mcp"; cwd.mkdir(exist_ok=True)
    ex = exec_records.create(
        thread_id="thr_cm", run_id=None, tool_name="run_python",
        status="ok", code="print('hi')", started_at="2026-06-06T14:00:00Z",
        completed_at="2026-06-06T14:00:01Z", cwd=cwd,
        payload={"stdout_tail": "Significant: 42\nMore details",
                 "stderr_tail": "", "exit_code": 0,
                 "produced": []},
    )
    from mcp.server.fastmcp import FastMCP
    from content.bio.mcp_servers.aba_core.tools.cells import register_cell_tools
    mcp = FastMCP(name="t")
    register_cell_tools(mcp)

    async def _call(args):
        return await mcp.call_tool("pin_cell", args)

    result = asyncio.run(_call({"exec_id": ex, "title": "MCP pinned"}))
    out = None
    if isinstance(result, tuple):
        content, structured = result
        if isinstance(structured, dict):
            out = structured.get("result", structured) if "result" in structured else structured
    else:
        content = result
    if out is None:
        for b in content or []:
            t = getattr(b, "text", None)
            if t:
                try: out = json.loads(t)
                except json.JSONDecodeError: out = {"_raw": t}
                break
    check("handler returned a dict", isinstance(out, dict),
          f"got {type(out).__name__}: {str(out)[:150]}")
    if isinstance(out, dict):
        check("cell_id present", isinstance(out.get("cell_id"), str))
        check("result_id present (wrap_in_result=True by default)",
              isinstance(out.get("result_id"), str))


def test_pin_cell_handler_rejects_unknown_exec():
    print("\n[5] pin_cell rejects unknown exec_id (returns {'error': ...})")
    from mcp.server.fastmcp import FastMCP
    from content.bio.mcp_servers.aba_core.tools.cells import register_cell_tools
    mcp = FastMCP(name="t")
    register_cell_tools(mcp)

    async def _call():
        return await mcp.call_tool("pin_cell", {"exec_id": "exec_does_not_exist"})

    result = asyncio.run(_call())
    out = None
    if isinstance(result, tuple):
        content, structured = result
        if isinstance(structured, dict):
            out = structured.get("result", structured) if "result" in structured else structured
    else:
        content = result
    if out is None:
        for b in content or []:
            t = getattr(b, "text", None)
            if t:
                try: out = json.loads(t)
                except json.JSONDecodeError: out = {"_raw": t}
                break
    check("returned error envelope", isinstance(out, dict) and "error" in (out or {}),
          f"got {out!r}")


def main() -> int:
    test_register_cell_tools()
    test_make_server_includes_pin_cell()
    test_guardrail_wording()
    test_pin_cell_handler_happy()
    test_pin_cell_handler_rejects_unknown_exec()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s) — {_failures}")
        return 1
    print("ALL CELL-MCP CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
