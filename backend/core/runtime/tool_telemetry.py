"""Per-tool-invocation telemetry (P3 #6).

Every dispatch through execute_tool produces one row in
tool_invocations. Aggregated by /api/admin/tool_stats so we can see
what's actually used as the catalog grows (and especially after the
first MCP server brings in 20+ external tools).
"""
from __future__ import annotations
import datetime as _dt
import json as _json
from typing import Any, Optional

from core.graph._schema import _conn


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def record(
    *,
    run_id:        Optional[str],
    agent_spec:    str,
    tool_name:     str,
    input_:        Any,
    started_at:    str,
    ended_at:      str,
    duration_ms:   int,
    status:        str,        # ok | error | rejected | deferred
    error_summary: Optional[str] = None,
) -> None:
    """Write one row. Best-effort — never raises into the caller."""
    try:
        from core.runtime.mcp import is_mcp_tool
        source = "mcp:" + tool_name.split(":", 1)[0] if is_mcp_tool(tool_name) else "bio"
    except Exception:
        source = "bio"

    summary = _summarize_input(input_)
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO tool_invocations "
                "(run_id, agent_spec, tool_name, source, status, input_summary, "
                " duration_ms, error_summary, started_at, ended_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, agent_spec, tool_name, source, status, summary,
                 duration_ms, error_summary, started_at, ended_at),
            )
            c.commit()
    except Exception:  # noqa: BLE001
        pass    # telemetry must never block real work


def _summarize_input(input_: Any, max_len: int = 200) -> str:
    try:
        s = _json.dumps(input_, default=str)
    except Exception:
        s = str(input_)
    return s if len(s) <= max_len else (s[:max_len - 1] + "…")


def stats(days: int = 30) -> list[dict]:
    """Per-tool aggregates over the last `days` days."""
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()
    with _conn() as c:
        rows = c.execute(
            "SELECT tool_name, source, "
            "       COUNT(*) AS n, "
            "       SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS n_ok, "
            "       SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS n_error, "
            "       SUM(CASE WHEN status='rejected' THEN 1 ELSE 0 END) AS n_rejected, "
            "       SUM(CASE WHEN status='deferred' THEN 1 ELSE 0 END) AS n_deferred, "
            "       AVG(duration_ms) AS avg_ms, "
            "       MAX(duration_ms) AS max_ms "
            "FROM tool_invocations "
            "WHERE started_at >= ? "
            "GROUP BY tool_name, source "
            "ORDER BY n DESC",
            (cutoff,),
        ).fetchall()
    return [
        {
            "tool_name":  r["tool_name"],
            "source":     r["source"],
            "n":          r["n"] or 0,
            "n_ok":       r["n_ok"] or 0,
            "n_error":    r["n_error"] or 0,
            "n_rejected": r["n_rejected"] or 0,
            "n_deferred": r["n_deferred"] or 0,
            "avg_ms":     int(r["avg_ms"]) if r["avg_ms"] is not None else None,
            "max_ms":     r["max_ms"],
        }
        for r in rows
    ]


def recent_invocations(limit: int = 50, tool_name: Optional[str] = None) -> list[dict]:
    """Raw recent rows for debugging."""
    q = "SELECT * FROM tool_invocations"
    args: list = []
    if tool_name:
        q += " WHERE tool_name = ?"
        args.append(tool_name)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = c.execute(q, args).fetchall()
    return [dict(r) for r in rows]
