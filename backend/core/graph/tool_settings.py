"""Per-tool enable/disable bits. Domain-neutral."""
from __future__ import annotations
import sqlite3

from core.graph._schema import _conn, _GLOBAL_DISABLED


def get_disabled_tools() -> set[str]:
    with _conn() as c:
        try:
            rows = c.execute("SELECT name FROM tool_settings WHERE enabled=0").fetchall()
        except sqlite3.OperationalError:
            return set(_GLOBAL_DISABLED)
    return {r["name"] for r in rows} | _GLOBAL_DISABLED


def set_tool_enabled(name: str, enabled: bool) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO tool_settings (name, enabled) VALUES (?, ?) "
            "ON CONFLICT(name) DO UPDATE SET enabled=excluded.enabled",
            (name, 1 if enabled else 0),
        )
        c.commit()
