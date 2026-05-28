"""Global tool kill-switch. Domain-neutral.

A tool named in the `ABA_DISABLED_TOOLS` env var (comma-separated) is neither
offered to nor advertised to the agent. Read once at startup. This is an
operator-level switch that layers *under* each agent's `tool_allowlist`
(core.runtime.agent), which is the per-role tool-access mechanism."""
from __future__ import annotations

from core.graph._schema import _GLOBAL_DISABLED


def get_disabled_tools() -> set[str]:
    return set(_GLOBAL_DISABLED)
