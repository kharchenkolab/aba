"""L2 variance pass — measure the noise band on P3.

For each config, bounce server + run P3 three times. Compare
average pass rate and per-run jitter.

Configs:
  - lean_baseline:      ABA_PRIMARY_SPEC=lean_guide, no overrides
  - lean_qwen_spec:     ABA_PRIMARY_SPEC=lean_qwen_guide (new mode)
  - lean_layered_v5v6:  ABA_PRIMARY_SPEC=lean_guide + the two env knobs
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

RUNS_PER = 3

CONFIGS: list[tuple[str, dict[str, str]]] = [
    ("lean_baseline",
        {"ABA_PRIMARY_SPEC": "lean_guide"}),
    ("lean_qwen_spec",
        {"ABA_PRIMARY_SPEC": "lean_qwen_guide"}),
    ("lean_layered_v5v6",
        {"ABA_PRIMARY_SPEC": "lean_guide",
         "ABA_EXPERIMENTAL_DISCOVERY_DIRECTIVE": "1",
         "ABA_EXPERIMENTAL_PRESCRIPTIVE_SEARCH_SKILLS": "1"}),
]

KNOWN_KEYS = (
    "ABA_PRIMARY_SPEC",
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
    # The original config.env had ABA_PRIMARY_SPEC=lean_guide already;
    # we always overwrite that key explicitly so the result is clean.
    for k, v in env.items():
        text += f"export {k}={v}\n"
    CONFIG_ENV.write_text(text)


def _bounce() -> None:
    subprocess.run([ABA_BIN, "stop"], check=True)
    time.sleep(0.7)
    subprocess.run([ABA_BIN, "up"], check=True)
    import urllib.request
    for _ in range(40):
        try:
            urllib.request.urlopen(
                "http://127.0.0.1:8000/api/health", timeout=0.5)
            return
        except Exception:
            time.sleep(0.25)


def _run_p3() -> tuple[int, int]:
    r = subprocess.run(
        [sys.executable, str(P3_RUN)],
        capture_output=True, text=True, timeout=600)
    out = r.stdout + r.stderr
    m = re.search(r"(\d+)/(\d+) scenarios passed", out)
    if not m:
        return (-1, -1)
    return (int(m.group(1)), int(m.group(2)))


def main() -> int:
    results: list[tuple[str, list[int], int]] = []
    try:
        for label, env in CONFIGS:
            _patch_config_env(env)
            _bounce()
            passes: list[int] = []
            tot = 7
            for r_i in range(RUNS_PER):
                p, t = _run_p3()
                tot = t if t > 0 else tot
                passes.append(p)
                print(f"  {label} run {r_i+1}: {p}/{tot}", flush=True)
            results.append((label, passes, tot))
    finally:
        _patch_config_env({"ABA_PRIMARY_SPEC": "lean_guide"})
        _bounce()

    print(f"\n{'='*70}\n=== L2 VARIANCE SUMMARY ({RUNS_PER} runs each)\n"
          f"{'='*70}")
    for lab, passes, tot in results:
        clean = [p for p in passes if p >= 0]
        if not clean:
            print(f"  {lab:24s}  all runs failed")
            continue
        mean = sum(clean) / len(clean)
        print(f"  {lab:24s}  runs={passes}  mean={mean:.2f}/{tot}  "
              f"range={min(clean)}-{max(clean)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
