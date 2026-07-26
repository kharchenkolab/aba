"""Weft kernels cannot chdir — the predicate and the refusal.

A weft kernel's driver writes its own per-block files (`blocks/NNNN.*`) RELATIVE
to the process cwd, so the first block that changes the working directory
orphans the driver: its next write fails and the interpreter exits, taking every
object in memory with it. Live (mendel, 2026-07-26): `dir.create(w); setwd(w)`
in ordinary analysis code killed two kernels in a row, each time reported as
`Error in file(con, "w")` — naming the driver's write, never the chdir.

Two guarded things:

1. `is_weft_kernel` — the identity predicate. It must NOT be spelled
   `getattr(sess, "work_dir", None)`: that field is set only for a LOCAL weft
   kernel, so the work_dir spelling reads every REMOTE weft kernel as
   chdir-able. That exact mistake shipped a `setwd(<controller path>)` into
   remote kernels.
2. `chdir_offense` / `chdir_refusal` — the stopgap refusal. Its FALSE-POSITIVE
   ceilings matter as much as its detection: refusing legitimate code is the way
   this guard would do more harm than the bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.exec.kernels.cwd_guard import (  # noqa: E402
    chdir_offense, chdir_refusal, is_weft_kernel)


# ── the identity predicate ───────────────────────────────────────────────────

class _Weft:
    """A weft kernel session. NOTE: no work_dir — that is the remote shape, and
    the shape the old `work_dir` spelling got wrong."""
    def __init__(self, kernel_id="krn_1", work_dir=None):
        self.kernel_id = kernel_id
        self.work_dir = work_dir


class _Jupyter:
    """A local jupyter session: no kernel_id, and it CAN chdir safely."""
    pass


def test_remote_weft_kernel_is_recognized_without_work_dir():
    """THE regression: work_dir is None for a remote weft kernel."""
    assert is_weft_kernel(_Weft(work_dir=None)) is True
    assert is_weft_kernel(_Weft(work_dir="/local/kernels/krn_1")) is True


def test_work_dir_alone_still_counts():
    """work_dir was never WRONG, just not necessary — it stays sufficient, so a
    session known only by its work dir is still treated as un-chdir-able."""
    only_wd = type("S", (), {"work_dir": "/local/kernels/krn_9"})()
    assert is_weft_kernel(only_wd) is True


def test_jupyter_session_is_not_a_weft_kernel():
    """CEILING: a jupyter session must stay chdir-able, or the local lane loses
    its cwd handling and its probe."""
    assert is_weft_kernel(_Jupyter()) is False


def test_class_name_alone_is_enough():
    """Belt and braces: a WeftKernelSession before kernel_start assigns an id."""
    cls = type("WeftKernelSession", (), {})
    assert is_weft_kernel(cls()) is True


# ── detection ────────────────────────────────────────────────────────────────

def test_detects_the_live_killing_idiom_in_r():
    code = ('work_dir <- "/home/u/w"\n'
            'dir.create(work_dir, showWarnings = FALSE, recursive = TRUE)\n'
            'setwd(work_dir)\n')
    assert chdir_offense(code, "r") == "setwd(work_dir)"


def test_detects_python_chdir_spellings():
    assert chdir_offense("import os\nos.chdir('/tmp')\n", "python")
    assert chdir_offense("os . chdir('/tmp')", "python")
    assert chdir_offense("from os import chdir\nchdir('/tmp')", "python")
    # semicolon-joined statement still counts
    assert chdir_offense("x = 1; os.chdir('/tmp')", "python")


def test_clean_code_is_not_refused():
    assert chdir_offense('saveRDS(obj, file.path(work_dir, "o.rds"))', "r") is None
    assert chdir_offense("import os\np = os.path.join(w, 'x.png')", "python") is None
    assert chdir_offense("", "r") is None
    assert chdir_offense("print(getwd())", "r") is None


# ── false-positive ceilings (the part that keeps a refusal safe) ─────────────

def test_commented_out_chdir_is_ignored():
    assert chdir_offense("# setwd('/tmp')\nx <- 1", "r") is None
    assert chdir_offense("x = 1  # os.chdir('/tmp') would break it", "python") is None


def test_chdir_mentioned_inside_a_string_is_ignored():
    assert chdir_offense('message("do not call setwd(x) here")', "r") is None
    assert chdir_offense("""print("use os.chdir() never")""", "python") is None


def test_a_hash_inside_a_string_does_not_hide_a_real_offense():
    """The comment stripper must respect quotes, or a `#` in a string would
    truncate the line and mask a genuine chdir after it."""
    code = 'lbl <- "col#1"; setwd("/tmp")'
    assert chdir_offense(code, "r") == code


def test_language_scoping():
    """R's setwd is not Python's, and vice versa — a mismatched pattern would
    both miss offenses and refuse innocent code."""
    assert chdir_offense("setwd('/tmp')", "python") is None
    assert chdir_offense("os.chdir('/tmp')", "r") is None


def test_kwarg_named_chdir_is_not_a_call():
    """WIDE — the degenerate shape: `chdir=True` as an argument is not a chdir."""
    assert chdir_offense("subprocess.run(cmd, chdir=True)", "python") is None


# ── the refusal payload ──────────────────────────────────────────────────────

def test_refusal_names_the_cause_the_cost_and_the_way_out():
    out = chdir_refusal("setwd(work_dir)", "r")
    assert out["status"] == "error"
    assert out["error"] == "kernel.chdir_forbidden"
    assert out["offending_line"] == "setwd(work_dir)"
    note = out["note"]
    assert "Nothing was run" in note          # no partial execution to reason about
    assert "file.path(" in note                # the R way out, not the python one
    assert "os.path.join" not in note
    assert "blocks/" in note                   # says WHY, not just "forbidden"


def test_refusal_is_language_appropriate_for_python():
    note = chdir_refusal("os.chdir('/tmp')", "python")["note"]
    assert "os.chdir()" in note and "os.path.join" in note
    assert "file.path(" not in note
