"""Bio's content-provided services for the ``core/services`` seam — so core can
ask bio for values it needs (code-language sniffing for exec backfill; the host
tool catalog for the recovery report) without ``core/`` importing ``content/``.

Registered at import (pulled in by ``content/bio/__init__.py``). The actual bio
imports are deferred to call time so this module is import-order-safe.
"""
from __future__ import annotations

from core.services import register_service


def _language_sniffer(code: str) -> str:
    """R signals beat python signals; default to python on tie. (Wraps the
    scenarios heuristic; imported lazily.)"""
    from content.bio.lifecycle.scenarios import _detect_language
    return _detect_language(code)


def _host_tool_names():
    """The host's agent-visible tool names — the live MCP catalog if the gateway
    has booted, else a scan of aba_core's tool modules, plus the run_* workhorses.
    Returns a set, or None if nothing could be enumerated."""
    names: set[str] = set()
    try:
        from content.bio.tools import TOOL_SCHEMAS
        names.update(t["name"] for t in TOOL_SCHEMAS if isinstance(t, dict) and t.get("name"))
    except Exception:  # noqa: BLE001
        pass
    try:
        import re
        from pathlib import Path
        tools_dir = Path(__file__).resolve().parent / "mcp_servers/aba_core/tools"
        for f in tools_dir.glob("*.py"):
            for line in f.read_text().splitlines():
                m = re.match(r"\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", line)
                if m and not m.group(1).startswith("_"):
                    names.add(m.group(1))
    except Exception:  # noqa: BLE001
        pass
    names.update({"run_python", "run_r"})
    return names or None


register_service("language_sniffer", _language_sniffer)
register_service("host_tool_names", _host_tool_names)
