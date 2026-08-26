"""The frontend must BUILD, not merely type-check.

2026-08-26: a `continue` statement placed outside any loop passed
`tsc --noEmit`, passed all 271 vitest tests, and passed source-grep guards
written specifically for that change — and broke the production bundle:

    [PARSE_ERROR] Illegal continue statement: no surrounding iteration
    statement   src/useChat.ts:439

Nothing in the suite ever built or imported `useChat.ts`. The guards greped it.
A grep cannot catch a syntax error, and a test that exercises an extracted COPY
of the logic cannot either.

The image was then labelled with a commit whose code it did not contain,
because the build failure was masked (`./build.sh 2>&1 | tail -2 && ...` reads
tail's exit status). Two independent holes, one shipped artifact.

This is the cheap end of the fix: the bundle has to build.
"""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

FE = Path(__file__).resolve().parents[1] / "frontend"


def test_the_production_bundle_builds():
    vite = FE / "node_modules" / ".bin" / "vite"
    if not vite.exists():
        pytest.skip("frontend deps not installed")
    node_bin = Path.home() / ".aba" / "env" / "bin"
    env = dict(os.environ)
    if node_bin.exists():
        env["PATH"] = f"{node_bin}:{env.get('PATH','')}"
    if not shutil.which("node", path=env["PATH"]):
        pytest.skip("no node on PATH")
    r = subprocess.run([str(vite), "build"], cwd=FE, env=env,
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, (
        "the production bundle does not build — tsc and vitest can both be "
        f"green while this fails:\n{(r.stderr or r.stdout)[-2500:]}")
