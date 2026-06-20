"""Lean catalog: description compression + priority tiering.

Background: dropping tools from lean's allowlist masked function the
agent needed (prj_a6f40e94 2026-06-20 — figure-update bug, ensure_capability
gap, …). The redesign keeps every tool reachable; lean instead
COMPRESSES the catalog prefix.

What we test here:

  - `_compact_description` returns a 1-line summary, ≤ 200 chars,
    preserving the first sentence.
  - `list_tools(compact=True)` returns smaller descriptions and
    leaves `input_schema` exactly as is (parameter contract intact).
  - `priority_tools` keep their FULL description — the two-tier shape.
  - Empty / single-line descriptions survive compaction without
    corruption.
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_lean_compress_")
os.environ.setdefault("ABA_DB_PATH",     str(Path(_tmp) / "lc.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)
os.environ.setdefault("ABA_WORK_DIR",    str(Path(_tmp) / "work"))
os.environ.setdefault("ABA_ENVS_DIR",    str(Path(_tmp) / "envs"))
os.environ.setdefault("DATA_DIR",        str(Path(_tmp) / "data"))
os.environ.setdefault("ARTIFACTS_DIR",   str(Path(_tmp) / "artifacts"))
sys.path.insert(0, str(ROOT / "backend"))


pytestmark = pytest.mark.bio


# ── 1. _compact_description unit ─────────────────────────────────────
def test_compact_description_returns_first_paragraph():
    from core.runtime.mcp.gateway import _compact_description
    txt = ("Glob-style file search across the project tree. Use this when "
           "you need to locate a file by name without remembering the "
           "exact path.\n\nUSE THIS instead of subprocess.run(['find', ...]).\n\n"
           "Arguments:\n  pattern: glob like '*.rds'\n  root: …")
    out = _compact_description(txt)
    # First paragraph only.
    assert "USE THIS" not in out
    assert "Arguments:" not in out
    assert "glob-style file search" in out.lower()


def test_compact_description_returns_first_line_within_paragraph():
    """Some docstrings have multi-line first paragraphs. Pick the first
    line to keep the summary tight."""
    from core.runtime.mcp.gateway import _compact_description
    txt = "Read a CSV/TSV preview — first 5 rows + column types.\nSupports gzipped files."
    out = _compact_description(txt)
    assert out == "Read a CSV/TSV preview — first 5 rows + column types."


def test_compact_description_caps_at_200_chars():
    from core.runtime.mcp.gateway import _compact_description
    long_one_line = "A" * 500
    out = _compact_description(long_one_line)
    assert len(out) <= 200
    assert out == "A" * 200


def test_compact_description_handles_empty():
    from core.runtime.mcp.gateway import _compact_description
    assert _compact_description("") == ""
    assert _compact_description(None) == ""    # type: ignore[arg-type]


def test_compact_description_handles_single_short_line():
    """A short single-line description should pass through unchanged."""
    from core.runtime.mcp.gateway import _compact_description
    out = _compact_description("List files in DATA_DIR.")
    assert out == "List files in DATA_DIR."


# ── 2. list_tools(compact=True) end-to-end ───────────────────────────
def _bootstrap_aba_core():
    """Idempotent: register aba_core in-process so list_tools() returns
    real bio tools."""
    from core.graph._schema import init_db
    init_db()
    from core.runtime.mcp import register_inprocess_server, _reset_for_testing
    from content.bio.mcp_servers.aba_core import make_server
    _reset_for_testing()
    register_inprocess_server(
        "aba_core", make_server,
        expose_in_catalog=True, strip_prefix_in_catalog=True,
    )


def test_compact_mode_shrinks_total_catalog():
    """The headline win: compact mode reduces total description
    bytes by a meaningful margin (≥40%). Lock that in."""
    import content.bio  # noqa: F401
    _bootstrap_aba_core()
    from core.runtime.mcp.gateway import list_tools
    full = list_tools(compact=False)
    lean = list_tools(compact=True)
    assert len(full) == len(lean), \
        "compact mode must not drop tools"
    full_bytes = sum(len(t.get("description") or "") for t in full)
    lean_bytes = sum(len(t.get("description") or "") for t in lean)
    reduction = (full_bytes - lean_bytes) / max(full_bytes, 1)
    assert reduction >= 0.40, (
        f"compact mode saved only {reduction:.1%} "
        f"(full={full_bytes} lean={lean_bytes}); expected ≥ 40%")


def test_compact_mode_preserves_input_schema_exactly():
    """The parameter contract is semantic — compaction MUST NOT touch
    `input_schema`. If we ever broke this the agent would call tools
    with the wrong arg shape silently."""
    import content.bio  # noqa: F401
    _bootstrap_aba_core()
    from core.runtime.mcp.gateway import list_tools
    full = {t["name"]: t for t in list_tools(compact=False)}
    lean = {t["name"]: t for t in list_tools(compact=True)}
    for name in full:
        assert full[name]["input_schema"] == lean[name]["input_schema"], (
            f"compact mode mutated input_schema for {name!r}")


def test_priority_tools_keep_full_descriptions():
    """The two-tier shape: a list of tools whose full description
    survives compact mode."""
    import content.bio  # noqa: F401
    _bootstrap_aba_core()
    from core.runtime.mcp.gateway import list_tools
    # `run_python` has a long docstring; assert its lean entry matches
    # the full entry's description verbatim when priority-listed.
    priority = ("run_python", "Skill")
    full = {t["name"]: t for t in list_tools(compact=False)}
    lean = {t["name"]: t for t in list_tools(compact=True,
                                              priority_tools=priority)}
    assert full["run_python"]["description"] == lean["run_python"]["description"]
    assert full["Skill"]["description"]      == lean["Skill"]["description"]
    # And a non-priority tool's lean description should be shorter.
    nonpriority = next(n for n in full if n not in priority)
    if full[nonpriority]["description"]:
        assert len(lean[nonpriority]["description"]) \
            <= len(full[nonpriority]["description"]), (
            f"{nonpriority}: compact didn't shrink")


def test_default_call_no_args_matches_full_mode():
    """Back-compat: callers that didn't know about compact still get
    the verbose catalog (current behavior)."""
    import content.bio  # noqa: F401
    _bootstrap_aba_core()
    from core.runtime.mcp.gateway import list_tools
    default = list_tools()
    explicit_full = list_tools(compact=False)
    assert default == explicit_full


# ── 3. describe_tool — on-demand escape hatch ───────────────────────
def _call_tool(name: str, args: dict) -> dict:
    import json
    from content.bio.tools import execute_tool
    raw = execute_tool(name, args, {"thread_id": "thr_test"})
    return json.loads(raw) if isinstance(raw, str) else raw


def test_describe_tool_returns_full_schema():
    """The escape hatch: when the agent has only the compact 1-liner,
    it can ask for the full description + parameter schema."""
    import content.bio  # noqa: F401
    _bootstrap_aba_core()
    from core.runtime.mcp.gateway import list_tools
    full = {t["name"]: t for t in list_tools(compact=False)}
    # Pick a tool with a long description — find_files is verbose.
    target = "find_files"
    assert target in full and len(full[target]["description"]) > 200
    res = _call_tool("describe_tool", {"name": target})
    assert res.get("description") == full[target]["description"], (
        "describe_tool returned a different description than list_tools "
        "(compact=False) — the escape hatch must round-trip exactly")
    assert res.get("input_schema") == full[target]["input_schema"]


def test_describe_tool_unknown_name_returns_error():
    import content.bio  # noqa: F401
    _bootstrap_aba_core()
    res = _call_tool("describe_tool", {"name": "nonexistent_xyz_tool"})
    assert "error" in res
    assert "nonexistent_xyz_tool" in res["error"]


def test_describe_tool_is_listed_in_catalog():
    """The tool must be reachable from the catalog (otherwise the agent
    can't call it as the escape hatch)."""
    import content.bio  # noqa: F401
    _bootstrap_aba_core()
    from core.runtime.mcp.gateway import list_tools
    names = [t["name"] for t in list_tools()]
    assert "describe_tool" in names


def test_describe_tool_self_describe():
    """Meta: describe_tool can describe itself."""
    import content.bio  # noqa: F401
    _bootstrap_aba_core()
    res = _call_tool("describe_tool", {"name": "describe_tool"})
    assert "error" not in res
    assert "describe_tool" == res.get("name")
    assert "schema" in res.get("description", "").lower() or \
           "describe" in res.get("description", "").lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
