"""Live-driven chat scenarios.

Each scenario is driven through the aba HTTP `/api/chat` endpoint on
http://127.0.0.1:8000 (override via ABA_BASE). By construction this
exercises the same code path a real UI session does — same prompt
assembly, same MCP gateway, same tool dispatch.

Skip rules:
  - aba server not reachable → all skipped.
  - The Scenario's `tool_mocks` field is IGNORED here (we can't
    intercept tool dispatch via the live API). Set `use_real_tool` to
    "*" or rely on cheap real tools.

To run:
    ABA_BASE=http://127.0.0.1:8000 \\
    ~/.aba/env/bin/python3 -m pytest tests/test_chat_scenarios_live.py -v -s
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # so `tests.scenarios` resolves
sys.path.insert(0, str(ROOT / "backend"))

from live_chat_runner import (run_scenario_live,
                                     server_reachable,
                                     LiveRunReport)
from scenarios import SCENARIOS

pytestmark = pytest.mark.platform     # no in-process bio import


SKIP_NO_SERVER = pytest.mark.skipif(
    not server_reachable(),
    reason="aba server not running on /api — start with `~/.aba/bin/aba up`",
)


def _format_report(r: LiveRunReport,
                   assertion_results: list[dict]) -> str:
    lines = ["", f"SCENARIO {r.scenario} on {r.runtime}:"]
    lines.append(f"  project: {r.project_id}  thread: {r.thread_id}")
    lines.append(f"  elapsed: {r.elapsed_s}s")
    lines.append(f"  halted:  {r.halted} ({r.halt_reason})")
    lines.append(f"  tool calls ({len(r.calls)}):")
    for n, i in r.calls:
        lines.append(f"    🔧 {n}({json.dumps(i, default=str)[:160]})")
    lines.append(f"  assistant tail: {r.last_text[:300]!r}")
    lines.append("  assertions:")
    for ar in assertion_results:
        mark = "✓" if ar["ok"] else "✗"
        lines.append(f"    {mark} {ar['name']}: {ar['detail']}")
    return "\n".join(lines)


@SKIP_NO_SERVER
@pytest.mark.parametrize("scenario", SCENARIOS,
                          ids=lambda s: s.name)
def test_scenario_live(scenario):
    """Run one scenario end-to-end against the live aba server."""
    r = run_scenario_live(scenario)
    results = []
    for a in scenario.assertions:
        ok, detail = a.predicate(r.calls)
        results.append({"name": a.name, "ok": ok, "detail": detail})
    failed = [x for x in results if not x["ok"]]
    if failed or r.halted:
        pytest.fail(_format_report(r, results))
    # Pretty print on success too for `-s` mode.
    print(_format_report(r, results))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-s"]))
