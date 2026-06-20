"""Phase 1 baseline scenario runner.

Runs every P1 scenario against the LIVE aba server at $ABA_BASE
(default http://127.0.0.1:8000) and reports pass/fail per scenario +
per assertion. Each scenario gets a fresh project + thread; no
mocking; the model dispatches against the real MCP gateway.

Usage:
    ~/.aba/env/bin/python3 scripts/p1_run.py
    ABA_BASE=http://otherhost:8000 ~/.aba/env/bin/python3 scripts/p1_run.py

Exit 0 if all pass, 1 if any fail, 2 on infrastructure error.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from tests.live_chat_runner import (run_scenario_live,
                                     server_reachable,
                                     LiveRunReport, BASE)
from tests.scenarios.p1 import P1_SCENARIOS


# ── ANSI helpers (cheap; degrade gracefully) ──────────────────────
_USE_COLOR = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _USE_COLOR else s


GREEN  = lambda s: _c("32", s)        # noqa: E731
RED    = lambda s: _c("31", s)        # noqa: E731
YELLOW = lambda s: _c("33", s)        # noqa: E731
DIM    = lambda s: _c("2",  s)        # noqa: E731
BOLD   = lambda s: _c("1",  s)        # noqa: E731


# ── Report ────────────────────────────────────────────────────────


def _format_calls(calls: list[tuple[str, dict]]) -> str:
    if not calls:
        return DIM("  (no tool calls)")
    lines = []
    for n, a in calls:
        args = json.dumps(a, default=str)
        if len(args) > 120:
            args = args[:117] + "..."
        lines.append(f"  🔧 {n}({args})")
    return "\n".join(lines)


def _evaluate(report: LiveRunReport, scenario) -> list[dict]:
    out = []
    for a in scenario.assertions:
        ok, detail = a.predicate(report.calls)
        out.append({"name": a.name, "ok": ok, "detail": detail})
    return out


def run_phase(label: str, scenarios) -> int:
    """Run a list of Scenarios and print a phase report."""
    if not server_reachable():
        print(RED(f"ERROR: aba server unreachable at {BASE}"))
        print(f"       start with `~/.aba/bin/aba up`")
        return 2

    print(BOLD(label))
    print(f"  server:    {BASE}")
    print(f"  scenarios: {len(scenarios)}")
    print()

    results = []
    t0 = time.time()
    for s in scenarios:
        t_start = time.time()
        sys.stdout.write(f"  {s.name:<40} ")
        sys.stdout.flush()
        try:
            report = run_scenario_live(s)
        except Exception as e:                                   # noqa: BLE001
            print(RED(f"INFRA-FAIL ({type(e).__name__}: {e})"))
            results.append({"name": s.name, "ok": False,
                             "report": None,
                             "assertions": [],
                             "error": str(e)})
            continue
        assertions = _evaluate(report, s)
        all_pass = all(a["ok"] for a in assertions) and not report.halted
        results.append({
            "name":       s.name,
            "ok":         all_pass,
            "report":     report,
            "assertions": assertions,
        })
        elapsed = time.time() - t_start
        mark = GREEN("PASS") if all_pass else RED("FAIL")
        print(f"{mark}  {DIM(f'({elapsed:.1f}s, {len(report.calls)} calls)')}")
    total_elapsed = time.time() - t0

    # ── Per-scenario detail (only failures get the full dump) ─────
    failures = [r for r in results if not r["ok"]]
    if failures:
        print()
        print(BOLD(f"  Failures ({len(failures)}/{len(results)}):"))
        for r in failures:
            print()
            print(f"  {RED('✗')} {BOLD(r['name'])}")
            rep = r.get("report")
            if rep is None:
                print(f"    infra error: {r.get('error')}")
                continue
            print(f"    user prompt: "
                  + DIM(repr(rep.scenario)[:80]))
            print(f"    runtime:     {rep.runtime}")
            print(f"    elapsed:     {rep.elapsed_s}s")
            print(f"    halted:      {rep.halted} "
                  f"({DIM(rep.halt_reason[:120]) if rep.halt_reason else ''})")
            print(f"    tool calls:")
            print(_format_calls(rep.calls))
            print(f"    assistant tail: "
                  + DIM(repr(rep.last_text[:200])))
            print(f"    assertions:")
            for a in r["assertions"]:
                mark = GREEN("✓") if a["ok"] else RED("✗")
                print(f"      {mark} {a['name']}: {a['detail']}")

    # ── Summary ────────────────────────────────────────────────────
    n_pass = sum(1 for r in results if r["ok"])
    print()
    print(BOLD("Summary:"))
    print(f"  {n_pass}/{len(results)} scenarios passed "
          f"({DIM(f'{total_elapsed:.1f}s total')})")

    return 0 if n_pass == len(results) else 1


def main() -> int:
    return run_phase("Phase 1 — baseline tool-call mechanics",
                     P1_SCENARIOS)


if __name__ == "__main__":
    sys.exit(main())
