"""Guard: no test may pin a runtime dir env var to a `/workspace` literal.

Regression guard for the `/workspace`-hardcoded-tests finding: 21 tests set
`os.environ["ABA_ENVS_DIR"] = "/workspace/aba-runtime/envs"` at import, which
raises `PermissionError: /workspace` on any box without `/workspace` (this CLIP
cluster) — `core.config` mkdir's `ENVS_DIR/jupyter` at import, so the module can't
even load, standalone OR under pytest. The fix tmp-izes them; this guard stops the
copy-paste template from silently reintroducing the hardcode (fix the tool, not the
21 instances).

Standalone-runnable (base env lacks pytest): `python tests/test_no_workspace_hardcode.py`.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"
SELF = Path(__file__).name

# Runtime dir env vars whose values must be writable on the host (config.py mkdir's
# them at import). Pinning any to /workspace breaks import on a box without it.
_DIR_VARS = ("ABA_RUNTIME_DIR", "ABA_ENVS_DIR", "DATA_DIR", "ARTIFACTS_DIR",
             "ABA_WORK_DIR", "ABA_REFS_DIR", "ABA_PROJECTS_DIR", "ABA_DB_PATH")
_VARS = "|".join(_DIR_VARS)
# `os.environ["VAR"] = "/workspace…`  OR  `os.environ.setdefault("VAR", "/workspace…`
# (specific to an env ASSIGNMENT, so fixture data / tracebacks that merely mention
# /workspace don't false-positive).
_PAT = re.compile(
    r'os\.environ\[\s*["\'](?:' + _VARS + r')["\']\s*\]\s*=\s*["\']/workspace'
    r'|os\.environ\.setdefault\(\s*["\'](?:' + _VARS + r')["\']\s*,\s*["\']/workspace'
)


def _offenders() -> list[str]:
    out: list[str] = []
    for py in sorted(TESTS.rglob("*.py")):
        if py.name == SELF:
            continue
        try:
            text = py.read_text(errors="replace")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if _PAT.search(line):
                out.append(f"{py.relative_to(ROOT)}:{i}: {line.strip()}")
    return out


def test_no_workspace_dir_hardcode():
    bad = _offenders()
    assert not bad, (
        "test(s) pin a runtime dir env var to /workspace — this raises "
        "PermissionError at import on any box without /workspace (e.g. the cluster). "
        "Use a tempfile.mkdtemp() path (str(Path(_tmp) / 'envs')) like the sibling dirs:\n  "
        + "\n  ".join(bad)
    )


if __name__ == "__main__":
    bad = _offenders()
    if bad:
        print("FAIL: /workspace-hardcoded dir env var(s) in tests/:")
        for b in bad:
            print("  " + b)
        sys.exit(1)
    print("ok  no /workspace-hardcoded dir env vars in tests/")
