"""Tool-admission ratchet (tool_library Phase 6b).

The JSON tool catalog must not grow without a decision. Per the admission rule
(misc/tool_library.md), a capability earns a `@mcp.tool` ONLY if it needs a boundary
the in-kernel `aba.*` library cannot provide — i.e. it must either

  (1) SUSPEND the agent's reasoning loop for a human (plan / approval / clarification), or
  (2) CONSTRUCT the model's perceptual context (a vision block, live cluster state).

Everything else — reads, graph writes, lookups — should be an `aba.*` verb the agent
scripts in run_python, NOT a new tool. This ratchet counts `@mcp.tool` handlers in the
aba_core tool modules and fails if the count exceeds BASELINE, forcing that decision.

To add a tool: confirm it meets (1) or (2), then bump BASELINE with a one-line
justification in the commit message. (The number should trend DOWN as reads/writes
flip to the library by default — not up.) Stdlib-only, CI-friendly.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = ROOT / "backend" / "content" / "bio" / "mcp_servers" / "aba_core" / "tools"

# Current catalog size (2026-07, tool_library branch). Trend this DOWN as the read/write
# flips become default; every INCREASE needs a boundary-test justification.
BASELINE = 85

_TOOL_RE = re.compile(r"@mcp\.tool\(")


def count_tools() -> int:
    return sum(len(_TOOL_RE.findall(f.read_text())) for f in TOOLS_DIR.glob("*.py"))


def main() -> int:
    n = count_tools()
    if n > BASELINE:
        print(f"TOOL-ADMISSION VIOLATION: catalog grew to {n} (baseline {BASELINE}).")
        print("A new @mcp.tool earns its place ONLY if it SUSPENDS the agent loop "
              "(plan/approval) or INJECTS model context (vision/live state).")
        print("Otherwise make it an aba.* verb (scripted in run_python). If genuinely "
              "justified, bump BASELINE in tests/check_tool_admission.py with a reason.")
        return 1
    print(f"tool-admission OK: {n} @mcp.tool handlers (<= baseline {BASELINE})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
