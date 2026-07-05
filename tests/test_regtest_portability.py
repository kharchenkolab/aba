"""Guard: the regtest harness must stay portable — no hardcoded `/home/<user>` paths.

The scenario/sweep harness (dev/QA tool) is meant to run on a fresh checkout and
against other deployments (e.g. aba-vbc's VBC server). It once defaulted its venvs
to `/home/pkharchenko/...` (dead on any other box → every generator silently FAILed).
The fix made interpreters resolve via ABA_SCENARIO_VENV/ABA_RUNTIME_VENV (fail-loud
if unresolved); this guard stops a `/home/<user>` literal — in code OR a run-example
docstring — from creeping back in. Use `$ABA_SCENARIO_VENV` / `$ABA_RUNTIME_VENV`
(or `$TMPDIR`) instead.

Standalone-runnable (base env lacks pytest): `python tests/test_regtest_portability.py`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGTEST = ROOT / "regtest"
SELF = Path(__file__).name

# An absolute home path — `/home/<name>/…`. (A bare `/home` mention in prose is fine;
# we flag the path form that pins a specific user's tree.)
_PAT = re.compile(r"/home/[A-Za-z0-9_.\-]+/")


def _offenders() -> list[str]:
    out: list[str] = []
    for f in sorted(list(REGTEST.rglob("*.py")) + list(REGTEST.rglob("*.sh"))):
        if f.name == SELF:
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _PAT.search(line):
                out.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    return out


def test_no_home_hardcode_in_regtest():
    bad = _offenders()
    assert not bad, (
        "regtest harness pins a /home/<user> path — dead on any other box. Resolve the "
        "interpreter via ABA_SCENARIO_VENV/ABA_RUNTIME_VENV (or use $TMPDIR for scratch):\n  "
        + "\n  ".join(bad)
    )


if __name__ == "__main__":
    bad = _offenders()
    if bad:
        print("FAIL: /home/<user> hardcode(s) in regtest harness:")
        for b in bad:
            print("  " + b)
        sys.exit(1)
    print("ok  no /home/<user> hardcodes in regtest/")
