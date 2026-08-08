"""A language whose packages live outside the interpreter MUST be activated.

`direct_exec` in a runtime descriptor means one thing: the prefix is directly
execable. It does NOT mean the environment is complete without the activation.

For Python that distinction never mattered — the interpreter finds its own
site-packages, so `<prefix>/bin/python` is fully equipped. For R it is the whole
ballgame: a pack's `cran:` dependencies are solved into a SEPARATE layer
directory (`<env>/rlib`) and reach R only through `R_LIBS`, which the
activation exports. Exec'ing `<prefix>/bin/Rscript` directly silently dropped
the entire cran layer.

Measured 2026-08-08 on the r-bio pack: `lstar ==0.2.2` solved and installed
correctly into `rlib` (4 packages, DESCRIPTION says 0.2.2), and
`library(lstar)` failed through ABA's own `exec_argv`. Nothing raised. The
package was simply not on the path, so every R lane — the `.rds` viewer bridge,
R kernels, run_r — was blind to every cran dep the pack declared. The pack was
right; the door was wrong.

(The diagnosis took a wrong turn worth recording: probing with a bare `Rscript`
showed no lstar, which read as "the cran lane silently installed nothing" — a
much scarier and completely wrong conclusion. The package was there; the probe
was using the same broken door as the product.)

This guards the PROPERTY rather than the R case, because the next language with
an out-of-interpreter package path will land in the same trap.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.compute.project_env import (            # noqa: E402
    argv_for_runtime, _needs_activation, _ACTIVATION_REQUIRED)

ACTIVATION = "source /envs/x/activate.sh"
DIRECT = {"prefix": "/envs/x", "direct_exec": True, "activation": ACTIVATION}
NO_ACT = {"prefix": "/envs/x", "direct_exec": True}
MOUNTED = {"prefix": None, "direct_exec": False, "activation": ACTIVATION}


# ── THE property ─────────────────────────────────────────────────────────────

def test_R_goes_through_the_ACTIVATION_even_when_direct_exec(cap=None):
    """The bug. `direct_exec` is about the prefix being execable; R still needs
    R_LIBS, which only the activation sets."""
    argv = argv_for_runtime(DIRECT, "r", ["-e", "1"])
    assert argv[0] == "bash", argv
    assert ACTIVATION in argv[-1], argv
    assert "Rscript" in argv[-1], argv


def test_PYTHON_still_takes_the_direct_fast_path(cap=None):
    """CEILING. Python needs no activation to see its own site-packages, and
    routing it through bash would add a shell to every probe and launcher call
    for nothing."""
    argv = argv_for_runtime(DIRECT, "python", ["-c", "1"])
    assert argv == ["/envs/x/bin/python", "-c", "1"], argv


def test_the_rule_is_a_LANGUAGE_SET_not_an_R_special_case():
    """A property, not an instance fix: the next language whose packages live
    outside the interpreter is added to one set, not to a new branch."""
    assert "r" in _ACTIVATION_REQUIRED
    assert _needs_activation("R", DIRECT) is True          # case-insensitive
    assert _needs_activation("python", DIRECT) is False


# ── degenerate shapes ────────────────────────────────────────────────────────

def test_R_falls_back_to_direct_exec_when_there_is_NO_activation():
    """WIDE. You cannot route through an activation that does not exist; a
    served-base / pack-less deploy has none, and R must still run."""
    argv = argv_for_runtime(NO_ACT, "r", ["-e", "1"])
    assert argv == ["/envs/x/bin/Rscript", "-e", "1"], argv


def test_a_MOUNTED_runtime_is_unchanged_for_both_languages():
    """CEILING: the non-direct topology already activated; this change must not
    touch it."""
    for lang, exe in (("r", "Rscript"), ("python", "python")):
        argv = argv_for_runtime(MOUNTED, lang, ["-x"])
        assert argv[0] == "bash" and ACTIVATION in argv[-1] and exe in argv[-1]


def test_the_pre_wrapper_survives_in_the_ACTIVATED_shape():
    """`pre` carries things like `stdbuf -oL`; dropping it when R switched
    shapes would silently un-line-buffer every R stream."""
    argv = argv_for_runtime(DIRECT, "r", ["-e", "1"], pre=["stdbuf", "-oL"])
    assert "stdbuf -oL Rscript" in argv[-1], argv[-1]


def test_the_pre_wrapper_survives_in_the_DIRECT_shape():
    argv = argv_for_runtime(DIRECT, "python", ["-c", "1"], pre=["stdbuf", "-oL"])
    assert argv[:2] == ["stdbuf", "-oL"], argv


def test_ns_wrap_still_wraps_when_R_takes_the_activated_path():
    """A squashfs base needs the mount namespace; R now reaches this branch on
    topologies it previously skipped, so the wrap must apply there too."""
    rt = {**DIRECT, "ns_wrap": True}
    argv = argv_for_runtime(rt, "r", ["-e", "1"])
    assert argv[0] == "bash" and "unshare -rm" in argv[-1], argv


def test_arguments_are_QUOTED_through_the_shell_shape():
    """The activated path builds a shell string; an R expression with spaces or
    quotes must survive it intact."""
    expr = 'cat("a b"); q("no")'
    argv = argv_for_runtime(DIRECT, "r", ["-e", expr])
    import shlex
    inner = argv[-1].split("&& exec ", 1)[1]
    assert shlex.split(inner)[-1] == expr, inner


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
