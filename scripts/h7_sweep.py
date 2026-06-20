"""H7 sweep — run P3 across system-prompt and framing combos.

Each variant is a dict of env-vars. The sweep patches them into
config.env, bounces the server, runs P3, captures pass count, and
restores config.env at the end.
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

# Knobs we control:
#   ABA_OPENAI_TOOL_RESULT_FRAMING            — v1_suffix / v2_observation
#                                               / v3_followup / v4_combo / none
#   ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE      — set/unset (V5)
#   ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS — set/unset (V6)

VARIANTS: list[tuple[str, dict[str, str]]] = [
    ("V1_alone",         {"ABA_OPENAI_TOOL_RESULT_FRAMING": "v1_suffix"}),
    ("V4_combo",         {"ABA_OPENAI_TOOL_RESULT_FRAMING": "v4_combo"}),
    ("V5_system_only",   {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1"}),
    ("V6_doc_only",      {"ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS": "1"}),
    ("V5+V6",            {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
                           "ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS": "1"}),
    ("V5+V6+V1",         {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
                           "ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS": "1",
                           "ABA_OPENAI_TOOL_RESULT_FRAMING": "v1_suffix"}),
    ("V5+V6+V4",         {"ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
                           "ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS": "1",
                           "ABA_OPENAI_TOOL_RESULT_FRAMING": "v4_combo"}),
]

KNOWN_KEYS = (
    "ABA_OPENAI_TOOL_RESULT_FRAMING",
    "ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE",
    "ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS",
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
    subprocess.run([ABA_BIN, "up"],   check=True)
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

    print(f"\n{'='*70}\n=== H7 SWEEP SUMMARY (baseline none = 1/7)\n"
          f"{'='*70}")
    for lab, p, tot, env in results:
        print(f"  {lab:18s}  {p}/{tot}   envs={env}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
