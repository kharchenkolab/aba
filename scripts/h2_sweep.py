"""Hypothesis #2 sweep — run P3 across 3 tool_result framings.

Bounces the live aba server with each ABA_OPENAI_TOOL_RESULT_FRAMING
value, runs P3, prints pass/fail counts. The "none" baseline is the
1/7 hermes run we already have on record.

Run only with the user's explicit go-ahead. Each variant bounces the
server (stops then starts uvicorn) — visible by design.
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

CONFIG_ENV = Path("/Users/peter.kharchenko/.aba/config.env")
ABA_BIN    = "/Users/peter.kharchenko/.aba/bin/aba"
P3_RUN     = Path(__file__).resolve().parent / "p3_run.py"

FRAMING_KEY = "ABA_OPENAI_TOOL_RESULT_FRAMING"
VARIANTS    = ["v1_suffix", "v2_observation", "v3_followup"]


def _patch_config_env(value: str | None) -> None:
    """Set or remove the framing export in config.env."""
    text = CONFIG_ENV.read_text()
    line_re = re.compile(rf"(?m)^export {FRAMING_KEY}=.*\n?")
    text = line_re.sub("", text)
    if value is not None:
        if not text.endswith("\n"):
            text += "\n"
        text += f"export {FRAMING_KEY}={value}\n"
    CONFIG_ENV.write_text(text)


def _bounce() -> None:
    subprocess.run([ABA_BIN, "stop"], check=True)
    subprocess.run([ABA_BIN, "up"],   check=True)
    # Wait for the server to accept connections (up to ~10s).
    import urllib.request
    for _ in range(40):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:8000/api/health", timeout=0.5)
            return
        except Exception:
            time.sleep(0.25)
    print("WARNING: server didn't come up cleanly within 10s")


def _run_p3() -> tuple[str, int, int]:
    """Run P3, return (full_stdout_tail, passed, total)."""
    r = subprocess.run(
        [sys.executable, str(P3_RUN)],
        capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    # Final line shape: "Summary:\n  1/7 scenarios passed (...)"
    m = re.search(r"(\d+)/(\d+) scenarios passed", out)
    if not m:
        return (out[-2000:], -1, -1)
    return (out[-2000:], int(m.group(1)), int(m.group(2)))


def main() -> int:
    results: list[tuple[str, int, int]] = []
    try:
        for variant in VARIANTS:
            print(f"\n{'='*70}\n=== H2 variant: {variant}\n{'='*70}\n",
                  flush=True)
            _patch_config_env(variant)
            _bounce()
            tail, p, tot = _run_p3()
            print(tail)
            print(f">>> {variant}: {p}/{tot}", flush=True)
            results.append((variant, p, tot))
    finally:
        # Always restore: drop the framing line.
        _patch_config_env(None)
        _bounce()

    print(f"\n{'='*70}\n=== H2 SWEEP SUMMARY (baseline = 1/7 hermes)\n"
          f"{'='*70}")
    for v, p, tot in results:
        print(f"  {v:18s}  {p}/{tot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
