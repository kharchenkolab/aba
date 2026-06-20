"""H8 block-ablation sweep — does any individual block in the lean
spec interfere with Qwen3's discovery flow?

Strategy: pin V5 ON / V6 OFF (5/7 baseline — leaves headroom for
improvement). Drop one block at a time. If any single ablation moves
5/7 → 7/7, that block was the culprit and V6 is just compensating.
Also runs one ablation with NO directives to see if a block fix alone
beats baseline (1/7).
"""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

CONFIG_ENV = Path("/Users/peter.kharchenko/.aba/config.env")
ABA_BIN    = "/Users/peter.kharchenko/.aba/bin/aba"
P3_RUN     = Path("/Users/peter.kharchenko/aba/aba/scripts/p3_run.py")

VARIANTS: list[tuple[str, dict[str, str]]] = [
    # Each variant ablates one block (or pair) with V5 ON, V6 OFF.
    ("V5_minus_skills_core",
        {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
         "ABA_EXPERIMENTAL_ABLATE_BLOCKS": "skills_core"}),
    ("V5_minus_plan_first",
        {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
         "ABA_EXPERIMENTAL_ABLATE_BLOCKS": "plan_first"}),
    ("V5_minus_figures",
        {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
         "ABA_EXPERIMENTAL_ABLATE_BLOCKS": "figures"}),
    ("V5_minus_recipes",
        {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
         "ABA_EXPERIMENTAL_ABLATE_BLOCKS": "recipes"}),
    ("V5_minus_conventions",
        {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
         "ABA_EXPERIMENTAL_ABLATE_BLOCKS": "conventions"}),
    ("V5_minus_skills_core_AND_plan_first",
        {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
         "ABA_EXPERIMENTAL_ABLATE_BLOCKS": "skills_core,plan_first"}),
    # Sanity probe — no directive at all, just drop skills_core.
    # Tells us if removing a single block beats raw baseline (1/7).
    ("baseline_minus_skills_core",
        {"ABA_EXPERIMENTAL_ABLATE_BLOCKS": "skills_core"}),
]

KNOWN_KEYS = (
    "ABA_OPENAI_TOOL_RESULT_FRAMING",
    "ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE",
    "ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS",
    "ABA_EXPERIMENTAL_ABLATE_BLOCKS",
)


def _patch_config_env(env: dict[str, str]) -> None:
    text = CONFIG_ENV.read_text()
    for key in KNOWN_KEYS:
        text = re.sub(rf"(?m)^export {key}=.*\n?", "", text)
    if not text.endswith("\n"):
        text += "\n"
    for k, v in env.items():
        text += f"export {k}={v}\n"
    CONFIG_ENV.write_text(text)


def _bounce() -> None:
    subprocess.run([ABA_BIN, "stop"], check=True)
    # Brief pause so the launcher's `pgrep` sees the process is gone
    # before `aba up` checks "already running".
    time.sleep(0.5)
    subprocess.run([ABA_BIN, "up"], check=True)
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
    r = subprocess.run(
        [sys.executable, str(P3_RUN)],
        capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    m = re.search(r"(\d+)/(\d+) scenarios passed", out)
    if not m:
        return (out[-2000:], -1, -1)
    return (out[-2000:], int(m.group(1)), int(m.group(2)))


def main() -> int:
    results: list[tuple[str, int, int, dict[str, str]]] = []
    try:
        for label, env in VARIANTS:
            print(f"\n{'='*70}\n=== {label}  envs={env}\n{'='*70}\n",
                  flush=True)
            _patch_config_env(env)
            _bounce()
            tail, p, tot = _run_p3()
            print(tail)
            print(f">>> {label}: {p}/{tot}", flush=True)
            results.append((label, p, tot, env))
    finally:
        _patch_config_env({})
        _bounce()

    print(f"\n{'='*70}\n=== H8 ABLATION SUMMARY\n"
          f"=== References: baseline none=1/7  V5_only=5/7  V5+V6=7/7\n"
          f"{'='*70}")
    for lab, p, tot, env in results:
        print(f"  {lab:45s}  {p}/{tot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
