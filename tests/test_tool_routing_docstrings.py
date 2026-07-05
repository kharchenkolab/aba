"""Behavior-policy guards for the agent's tool selection on focused-
figure derivatives. The agent picks tools partly from training prior +
partly from docstrings; when the docstrings under-describe a contract,
the prior wins. Live session 2026-06-07 (thr_b80bc612, prj_2c23e5b5)
showed the agent reaching for run_r to make a "PDF version of this
figure with the legend on the bottom" — bypassing make_revision and
losing the chain context.

This test pins down the docstring contract so the routing policy can't
silently regress:

  - make_revision covers all four axes (content / layout / style /
    format) explicitly, including the words 'PDF version', 'SVG',
    'legend on the bottom', 'Nature-style'.
  - run_python / run_r both carry a routing note pointing at
    make_revision for focused-figure derivatives.
  - The focus preamble carries the deixis rule that 'this figure'
    always resolves to the focused entity.

Run: .venv/bin/python tests/test_tool_routing_docstrings.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_tool_routing_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "r.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _mcp_tools() -> dict:
    """Spin up the aba_core MCP server, return its registered tools."""
    from mcp.server.fastmcp import FastMCP
    mcp = FastMCP("aba_core")
    from content.bio.mcp_servers.aba_core.tools.revisions import register_revision_tools
    from content.bio.mcp_servers.aba_core.tools.run_exec import register_run_exec_tools
    register_revision_tools(mcp)
    register_run_exec_tools(mcp)
    # FastMCP keeps tools in _tool_manager._tools (an internal dict);
    # tolerate alternate names across versions.
    mgr = getattr(mcp, "_tool_manager", None) or getattr(mcp, "_tools", None)
    if hasattr(mgr, "_tools"):
        return {n: t for n, t in mgr._tools.items()}
    if isinstance(mgr, dict):
        return mgr
    # Last-ditch: pull from the public listing
    import asyncio
    listed = asyncio.get_event_loop().run_until_complete(mcp.list_tools())
    return {t.name: t for t in listed}


def _docstring(tools: dict, name: str) -> str:
    t = tools.get(name)
    if t is None:
        return ""
    # FastMCP tool wrapper exposes .description (string) or .fn.__doc__
    return (getattr(t, "description", None)
            or (getattr(t, "fn", None) and t.fn.__doc__)
            or "")


def test_make_revision_covers_all_four_axes():
    print("\n[1] make_revision docstring covers content / layout / style / format")
    tools = _mcp_tools()
    doc = _docstring(tools, "make_revision").lower()
    check("doc mentions 'content change'", "content change" in doc, f"doc[:200]={doc[:200]!r}")
    check("doc mentions 'layout change'", "layout change" in doc)
    check("doc mentions 'style change'", "style change" in doc)
    check("doc mentions 'format change'", "format change" in doc)
    # Specific trigger phrases the live session used:
    check("doc explicitly lists 'pdf version'", "pdf version" in doc,
          "the live miss used this exact phrase")
    check("doc explicitly lists 'svg'", "svg" in doc)
    check("doc mentions 'legend on the bottom'",
          "legend on the bottom" in doc,
          "the live miss used this exact phrase")
    check("doc mentions 'nature-style'", "nature-style" in doc)


def test_make_revision_tells_agent_not_to_ask_first():
    print("\n[2] make_revision: 'do not first ask whether to revise' (with focused figure)")
    tools = _mcp_tools()
    doc = _docstring(tools, "make_revision").lower()
    check("doc says 'do not first ask the user whether to make a revision'",
          "do not first ask the user whether to make a revision" in doc,
          f"doc[-400:]={doc[-400:]!r}")


def test_run_python_routes_to_make_revision_for_derivatives():
    print("\n[3] run_python docstring has a ROUTING NOTE pointing at make_revision")
    tools = _mcp_tools()
    doc = _docstring(tools, "run_python")
    check("'ROUTING NOTE' present", "ROUTING NOTE" in doc, f"doc[:300]={doc[:300]!r}")
    check("doc mentions 'make_revision'", "make_revision" in doc)
    check("doc says 'modified version of an existing focused figure'",
          "modified version" in doc.lower() and "focused figure" in doc.lower())


def test_run_r_routes_to_make_revision_for_derivatives():
    print("\n[4] run_r docstring has a ROUTING NOTE pointing at make_revision")
    tools = _mcp_tools()
    doc = _docstring(tools, "run_r")
    check("'ROUTING NOTE' present", "ROUTING NOTE" in doc, f"doc[:300]={doc[:300]!r}")
    check("doc mentions 'make_revision'", "make_revision" in doc)
    # The R routing note also names typical R-specific construction patterns
    check("doc names cairo_pdf / ggsave / ComplexHeatmap as examples",
          all(x in doc.lower() for x in ("cairo_pdf", "ggsave", "complexheatmap")))


def test_focus_preamble_carries_deixis_rule():
    print("\n[5] render_focus_preamble adds the deixis rule for focused entities")
    from core.graph._schema import init_db
    import content.bio  # noqa: F401 — register card builders
    init_db()
    # Build a Result manifest so we have a focused entity to render.
    from core.manifest.assembler import build_manifest, render_focus_preamble
    from core.graph.entities import create_entity
    rid = create_entity(entity_type="result", title="A Result",
                        metadata={"members": []})
    manifest = build_manifest(session_id="s", turn_index=0,
                              focus_entity_id=rid, thread_id="t")
    text, _ = render_focus_preamble(manifest)
    lc = text.lower()
    check("preamble mentions 'deictic references'", "deictic" in lc,
          f"preamble[:400]={text[:400]!r}")
    check("preamble lists 'this figure'", "'this figure'" in text)
    check("preamble lists 'this result'", "'this result'" in text)
    check("preamble explicitly says 'always resolve'", "always resolve" in lc)
    check("preamble says 'do not ask the user to choose'",
          "do not ask the user to choose" in lc)


def main() -> int:
    test_make_revision_covers_all_four_axes()
    test_make_revision_tells_agent_not_to_ask_first()
    test_run_python_routes_to_make_revision_for_derivatives()
    test_run_r_routes_to_make_revision_for_derivatives()
    test_focus_preamble_carries_deixis_rule()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL TOOL-ROUTING-DOCSTRINGS CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
