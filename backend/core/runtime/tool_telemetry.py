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


def record_generation(
    *,
    run_id:        Optional[str],
    agent_spec:    str,
    gen_index:     int,
    n_tool_uses:   int,
    input_tokens:  int = 0,
    output_tokens: int = 0,
    cache_read:    int = 0,
    cache_write:   int = 0,
    stop_reason:   Optional[str] = None,
) -> None:
    """Write one row per LLM generation (round-trip). Best-effort — never raises.
    Round-trips per turn = COUNT rows for a run_id; parallelism = n_tool_uses;
    cache effectiveness = cache_read vs cache_write."""
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO llm_generations "
                "(run_id, agent_spec, gen_index, n_tool_uses, input_tokens, "
                " output_tokens, cache_read, cache_write, stop_reason, started_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, agent_spec, gen_index, n_tool_uses, input_tokens,
                 output_tokens, cache_read, cache_write, stop_reason, _now()),
            )
            c.commit()
    except Exception:  # noqa: BLE001
        pass    # telemetry must never block real work


def generation_stats(days: int = 30) -> dict:
    """Aggregate round-trip / parallelism / cache metrics over the last `days`.
    Returns per-run round-trip distribution + fleet averages — the numbers that
    tell us whether tool-use is efficient and whether the catalog is cached."""
    cutoff = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).isoformat()
    _empty = {"n_runs": 0, "avg_round_trips_per_run": 0, "avg_tool_uses_per_run": 0,
              "avg_parallelism": 0, "max_parallelism": 0, "cache_hit_frac": None,
              "cache_read_total": 0, "cache_write_total": 0, "fresh_input_total": 0}
    try:
        with _conn() as c:
            per_run = c.execute(
                "SELECT run_id, agent_spec, "
                "       COUNT(*) AS round_trips, "
                "       SUM(n_tool_uses) AS tool_uses, "
                "       MAX(n_tool_uses) AS max_parallel, "
                "       SUM(cache_read) AS cache_read, "
                "       SUM(cache_write) AS cache_write, "
                "       SUM(input_tokens) AS input_tokens "
                "FROM llm_generations WHERE started_at >= ? "
                "GROUP BY run_id",
                (cutoff,),
            ).fetchall()
    except Exception:  # noqa: BLE001 — e.g. a project DB not yet migrated with the table
        return _empty
    runs = [dict(r) for r in per_run]
    if not runs:
        return _empty
    n = len(runs) or 1
    tot_gen = sum(r["round_trips"] or 0 for r in runs)
    tot_tu = sum(r["tool_uses"] or 0 for r in runs)
    # generations that emitted tools (round_trips minus the final answer-only gen
    # per run) — parallelism is tool_uses / tool-emitting generations.
    tool_gens = max(1, tot_gen - len(runs))
    cr = sum(r["cache_read"] or 0 for r in runs)
    cw = sum(r["cache_write"] or 0 for r in runs)
    inp = sum(r["input_tokens"] or 0 for r in runs)
    return {
        "n_runs": len(runs),
        "avg_round_trips_per_run": round(tot_gen / n, 2),
        "avg_tool_uses_per_run": round(tot_tu / n, 2),
        "avg_parallelism": round(tot_tu / tool_gens, 2),   # tool_uses per tool-emitting gen
        "max_parallelism": max((r["max_parallel"] or 0 for r in runs), default=0),
        # cache_read / (cache_read + cache_write + fresh input) → fraction of prompt
        # tokens served from cache. High = the static catalog/system is being cached.
        "cache_hit_frac": round(cr / (cr + cw + inp), 3) if (cr + cw + inp) else None,
        "cache_read_total": cr, "cache_write_total": cw, "fresh_input_total": inp,
    }


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
