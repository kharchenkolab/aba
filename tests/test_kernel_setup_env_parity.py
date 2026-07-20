"""Guard: the interactive weft kernel seeds DATA_DIR / ARTIFACTS_DIR / WORK_DIR as
BOTH a variable and an env var — python and R, local and remote — matching the
one-shot lane (core/exec/run.py).

Regression this locks (live 2026-07-21): run_python code did
`os.environ['DATA_DIR']` and KeyError'd because the kernel set only the *variable*;
the agent fell back to a hardcoded path. ARTIFACTS_DIR was absent entirely.

Behavioral, not structural: for python we EXEC the real setup code and assert the
name resolves as a variable AND that os.environ carries the same value; for R
(no interpreter in unit-test env) we assert the variable + Sys.setenv parity in the
generated code."""
from __future__ import annotations
import os
import re

import pytest

from core.exec.kernels.weft import _weft_setup_code

pytestmark = pytest.mark.platform

NAMES = ("DATA_DIR", "ARTIFACTS_DIR", "WORK_DIR")


@pytest.mark.parametrize("remote", [False, True])
def test_python_kernel_seeds_var_and_env(remote):
    code = _weft_setup_code("python", remote=remote)
    saved = {n: os.environ.get(n) for n in NAMES}
    try:
        ns: dict = {}
        exec(compile(code, "<weft-setup>", "exec"), ns)   # run the REAL setup block
        for n in NAMES:
            assert n in ns and ns[n], f"{n} not defined as a variable"
            assert os.environ.get(n) == ns[n], (
                f"{n} variable/env split — os.environ[{n!r}]={os.environ.get(n)!r} "
                f"!= variable {ns[n]!r} (this is exactly what KeyError'd live)")
    finally:                                              # don't leak into other tests
        for n, v in saved.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


def test_r_kernel_seeds_var_and_env():
    for remote in (False, True):
        code = _weft_setup_code("r", remote=remote)
        for n in NAMES:
            assert re.search(rf"\b{n}\s*<-", code), f"R ({remote=}): {n} variable missing"
        assert "Sys.setenv(" in code and all(n in code for n in NAMES), \
            f"R ({remote=}): Sys.setenv env-var parity missing for {NAMES}"


def test_remote_binds_data_and_artifacts_to_sandbox():
    """Remote kernels must NOT point DATA_DIR/ARTIFACTS_DIR at the controller's
    project path (it doesn't exist on the remote machine) — bind to the sandbox."""
    py = _weft_setup_code("python", remote=True)
    assert "DATA_DIR = ARTIFACTS_DIR = _os.getcwd()" in py
    r = _weft_setup_code("r", remote=True)
    assert "DATA_DIR <- getwd(); ARTIFACTS_DIR <- getwd()" in r
