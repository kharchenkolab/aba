#!/usr/bin/env python3
"""Claude Code usage analyzer — per-session, per-day, with cost estimates.

Walks ~/.claude/projects/*/<session>.jsonl files (the CC session transcripts)
and aggregates token usage. Unlike ccuse's `--since`, this only counts usage
that actually happened in the requested window (not cumulative session totals).

Default: list all sessions active in the last 7 days, grouped by project, with
per-session total tokens + cost.

Common invocations:
    ccuse.py                       # last 7 days, per session
    ccuse.py --monthly             # last 30 days, per session
    ccuse.py --since 2026-06-01    # custom window
    ccuse.py --project aba         # filter to sessions in -workspace-aba/
    ccuse.py --session 0aa68f98    # day-by-day for one session (prefix match OK)
    ccuse.py --all                 # all sessions, no time filter

Pricing is approximate Anthropic API public rates (June 2026). Adjust the
PRICING table at the top to match your billing if needed. CC subscription
users don't pay these rates directly — this is the API-equivalent cost.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Per-million-token USD rates (approximate June 2026 API list prices).
# cache_read = 1/10 of base input; cache_write = 1.25 × base input.
PRICING = {
    # Opus 4.x — most expensive
    "claude-opus-4-7":  {"input": 15.00, "output": 75.00, "cache_read": 1.50,  "cache_write": 18.75},
    "claude-opus-4-8":  {"input": 15.00, "output": 75.00, "cache_read": 1.50,  "cache_write": 18.75},
    # Sonnet 4.x
    "claude-sonnet-4-6":{"input":  3.00, "output": 15.00, "cache_read": 0.30,  "cache_write":  3.75},
    # Haiku 4.x
    "claude-haiku-4-5":         {"input": 0.25, "output": 1.25, "cache_read": 0.025, "cache_write": 0.3125},
    "claude-haiku-4-5-20251001":{"input": 0.25, "output": 1.25, "cache_read": 0.025, "cache_write": 0.3125},
}
DEFAULT_PRICING = {"input": 15.00, "output": 75.00, "cache_read": 1.50, "cache_write": 18.75}

PROJECTS_DIR = Path.home() / ".claude" / "projects"


def slug_to_path(slug):
    # CC project slugs are paths with '/' replaced by '-' and a leading '-'.
    # e.g. '-workspace-aba' → '/workspace/aba'
    if slug.startswith("-"):
        slug = slug[1:]
    return "/" + slug.replace("-", "/")


def cost_for(model, usage):
    p = PRICING.get(model, DEFAULT_PRICING)
    return (
        usage["input"]       * p["input"]       / 1_000_000
        + usage["output"]    * p["output"]      / 1_000_000
        + usage["cache_read"]  * p["cache_read"]  / 1_000_000
        + usage["cache_write"] * p["cache_write"] / 1_000_000
    )


def parse_session(path, since_date, until_date):
    """Stream-parse one session jsonl. Returns
    {date: {model: {input, output, cache_read, cache_write, count}}}.
    Both since_date/until_date are date objects (inclusive)."""
    daily = defaultdict(lambda: defaultdict(lambda: {
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "count": 0,
    }))
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if r.get("type") != "assistant":
                    continue
                msg = r.get("message") or {}
                u = msg.get("usage") or {}
                if not u:
                    continue
                ts = r.get("timestamp", "")[:10]
                if not ts:
                    continue
                try:
                    d = datetime.strptime(ts, "%Y-%m-%d").date()
                except ValueError:
                    continue
                if since_date and d < since_date:
                    continue
                if until_date and d > until_date:
                    continue
                model = msg.get("model") or "unknown"
                slot = daily[ts][model]
                slot["input"]       += u.get("input_tokens", 0) or 0
                slot["output"]      += u.get("output_tokens", 0) or 0
                slot["cache_read"]  += u.get("cache_read_input_tokens", 0) or 0
                slot["cache_write"] += u.get("cache_creation_input_tokens", 0) or 0
                slot["count"]       += 1
    except OSError:
        pass
    return daily


def discover_sessions(project_filter=None, session_filter=None):
    """Yield (project_slug, session_id, jsonl_path)."""
    if not PROJECTS_DIR.is_dir():
        return
    for proj in sorted(PROJECTS_DIR.iterdir()):
        if not proj.is_dir():
            continue
        if project_filter and project_filter.lower() not in proj.name.lower():
            continue
        for jsonl in sorted(proj.glob("*.jsonl")):
            sid = jsonl.stem
            if session_filter:
                if not (sid == session_filter or sid.startswith(session_filter)):
                    continue
            yield proj.name, sid, jsonl


def aggregate(daily_by_day_model):
    """Sum across all dates and models into a single dict."""
    total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "count": 0}
    cost = 0.0
    for day, by_model in daily_by_day_model.items():
        for model, u in by_model.items():
            for k in total: total[k] += u[k]
            cost += cost_for(model, u)
    return total, cost


def fmt_int(n):
    return f"{n:>13,}" if n else f"{'·':>13}"


def fmt_cost(c):
    return f"${c:>9,.2f}" if c >= 0.01 else f"{'·':>10}"


def fmt_tokens(t):
    if t >= 1_000_000_000: return f"{t/1_000_000_000:.2f}B"
    if t >= 1_000_000:     return f"{t/1_000_000:.1f}M"
    if t >= 1_000:         return f"{t/1_000:.0f}K"
    return f"{t}"


def report_sessions(args, since_date, until_date):
    """Default + --weekly + --monthly: per-session totals in window."""
    rows = []
    for proj_slug, sid, jsonl in discover_sessions(args.project, args.session):
        daily = parse_session(jsonl, since_date, until_date)
        if not daily:
            continue
        total, cost = aggregate(daily)
        if total["count"] == 0:
            continue
        days = sorted(daily.keys())
        models = set()
        for by_model in daily.values():
            models.update(by_model.keys())
        rows.append({
            "project": slug_to_path(proj_slug),
            "session": sid,
            "first_day": days[0],
            "last_day": days[-1],
            "n_days": len(days),
            "models": sorted(models),
            "total": total,
            "cost": cost,
        })
    rows.sort(key=lambda r: -r["cost"])

    if not rows:
        print(f"No session activity in {since_date} .. {until_date}.")
        return

    print(f"\nCC session usage  {since_date} .. {until_date}  ({len(rows)} sessions)")
    print("─" * 144)
    print(f"  {'project':<30} {'session':<12} {'days':>5} {'output':>9} {'cache_r':>9} {'cache_w':>9} {'in/out':>10} {'msgs':>6} {'cost':>11}  models")
    print("─" * 144)
    grand_total = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "count": 0}
    grand_cost = 0.0
    for r in rows:
        t = r["total"]
        for k in grand_total: grand_total[k] += t[k]
        grand_cost += r["cost"]
        # first_day / last_day are date-strings "YYYY-MM-DD" (we store ts[:10]).
        days_str = f"{r['n_days']}d" if r['n_days'] > 1 else r['first_day'][5:]   # MM-DD
        models = ",".join(m.replace("claude-", "").replace("-20251001", "")[:8] for m in r["models"])
        print(f"  {r['project']:<30} {r['session'][:10]:<12} {days_str:>5} "
              f"{fmt_tokens(t['output']):>9} {fmt_tokens(t['cache_read']):>9} {fmt_tokens(t['cache_write']):>9} "
              f"{fmt_tokens(t['input']):>10} {t['count']:>6,} {fmt_cost(r['cost']):>11}  {models}")
    print("─" * 144)
    print(f"  {'TOTAL':<30} {'':<12} {'':>5} "
          f"{fmt_tokens(grand_total['output']):>9} {fmt_tokens(grand_total['cache_read']):>9} {fmt_tokens(grand_total['cache_write']):>9} "
          f"{fmt_tokens(grand_total['input']):>10} {grand_total['count']:>6,} {fmt_cost(grand_cost):>11}")


def report_session_detail(args, since_date, until_date):
    """--session SID: day-by-day breakdown for one session."""
    matched = list(discover_sessions(args.project, args.session))
    if not matched:
        print(f"No session matched '{args.session}'.")
        return
    if len(matched) > 1:
        print(f"Ambiguous session prefix '{args.session}' — {len(matched)} matches:")
        for proj, sid, _ in matched:
            print(f"  {sid}  ({slug_to_path(proj)})")
        return
    proj_slug, sid, jsonl = matched[0]
    daily = parse_session(jsonl, since_date, until_date)
    if not daily:
        print(f"Session {sid} ({slug_to_path(proj_slug)}): no activity in {since_date} .. {until_date}")
        return

    print(f"\nSession {sid}")
    print(f"Project: {slug_to_path(proj_slug)}")
    print(f"Window:  {since_date} .. {until_date}")
    print("─" * 110)
    print(f"  {'date':<12} {'model':<22} {'msgs':>6} {'input':>9} {'output':>9} {'cache_r':>9} {'cache_w':>9} {'cost':>10}")
    print("─" * 110)
    grand = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "count": 0}
    grand_cost = 0.0
    for day in sorted(daily.keys()):
        for model in sorted(daily[day].keys()):
            u = daily[day][model]
            c = cost_for(model, u)
            print(f"  {day:<12} {model:<22} {u['count']:>6,} "
                  f"{fmt_tokens(u['input']):>9} {fmt_tokens(u['output']):>9} "
                  f"{fmt_tokens(u['cache_read']):>9} {fmt_tokens(u['cache_write']):>9} {fmt_cost(c):>10}")
            for k in grand: grand[k] += u[k]
            grand_cost += c
    print("─" * 110)
    print(f"  {'TOTAL':<12} {'':<22} {grand['count']:>6,} "
          f"{fmt_tokens(grand['input']):>9} {fmt_tokens(grand['output']):>9} "
          f"{fmt_tokens(grand['cache_read']):>9} {fmt_tokens(grand['cache_write']):>9} {fmt_cost(grand_cost):>10}")


def report_project_detail(args, since_date, until_date):
    """--project NAME without --session: list sessions in that project,
    AND show per-day rollup across all of them."""
    sessions = list(discover_sessions(args.project, None))
    if not sessions:
        print(f"No project matched '{args.project}'.")
        return

    # Per-day across all matching sessions
    rolled = defaultdict(lambda: defaultdict(lambda: {
        "input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "count": 0,
    }))
    n_active_sessions = 0
    for proj_slug, sid, jsonl in sessions:
        daily = parse_session(jsonl, since_date, until_date)
        if not daily: continue
        n_active_sessions += 1
        for day, by_model in daily.items():
            for model, u in by_model.items():
                for k in u: rolled[day][model][k] += u[k]
    if not rolled:
        print(f"Project '{args.project}': no activity in {since_date} .. {until_date}")
        return

    print(f"\nProject '{args.project}' ({n_active_sessions} active sessions)  daily rollup  {since_date} .. {until_date}")
    print("─" * 110)
    print(f"  {'date':<12} {'model':<22} {'msgs':>6} {'input':>9} {'output':>9} {'cache_r':>9} {'cache_w':>9} {'cost':>10}")
    print("─" * 110)
    grand = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "count": 0}
    grand_cost = 0.0
    for day in sorted(rolled.keys()):
        for model in sorted(rolled[day].keys()):
            u = rolled[day][model]
            c = cost_for(model, u)
            print(f"  {day:<12} {model:<22} {u['count']:>6,} "
                  f"{fmt_tokens(u['input']):>9} {fmt_tokens(u['output']):>9} "
                  f"{fmt_tokens(u['cache_read']):>9} {fmt_tokens(u['cache_write']):>9} {fmt_cost(c):>10}")
            for k in grand: grand[k] += u[k]
            grand_cost += c
    print("─" * 110)
    print(f"  {'TOTAL':<12} {'':<22} {grand['count']:>6,} "
          f"{fmt_tokens(grand['input']):>9} {fmt_tokens(grand['output']):>9} "
          f"{fmt_tokens(grand['cache_read']):>9} {fmt_tokens(grand['cache_write']):>9} {fmt_cost(grand_cost):>10}")
    # Also show per-session breakdown below
    print()
    report_sessions(args, since_date, until_date)


def main():
    ap = argparse.ArgumentParser(description="Claude Code per-session usage analyzer")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--weekly",  action="store_true", help="Last 7 days (default)")
    g.add_argument("--monthly", action="store_true", help="Last 30 days")
    g.add_argument("--all",     action="store_true", help="All time, no window")
    g.add_argument("--since",   metavar="DATE", help="ISO date (YYYY-MM-DD)")
    ap.add_argument("--until",  metavar="DATE", help="ISO date (YYYY-MM-DD)")
    ap.add_argument("--project", help="Filter to this project (substring match on slug)")
    ap.add_argument("--session", help="Filter to one session (prefix match OK); day-by-day breakdown")
    args = ap.parse_args()

    today = datetime.now(timezone.utc).date()
    if args.all:
        since_date, until_date = None, None
    elif args.since:
        since_date = datetime.strptime(args.since, "%Y-%m-%d").date()
        until_date = datetime.strptime(args.until, "%Y-%m-%d").date() if args.until else today
    elif args.monthly:
        since_date = today - timedelta(days=30)
        until_date = today
    else:  # default or --weekly
        since_date = today - timedelta(days=7)
        until_date = today

    if args.session:
        report_session_detail(args, since_date, until_date)
    elif args.project:
        report_project_detail(args, since_date, until_date)
    else:
        report_sessions(args, since_date, until_date)


if __name__ == "__main__":
    main()
