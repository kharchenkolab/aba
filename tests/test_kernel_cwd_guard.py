"""Weft kernels cannot be LEFT in a different working directory.

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
2. `cwd_drift_diagnosis` — the death message. A refusal was built and REMOVED
   (see the section comment below); what remains makes the failure legible so an
   agent corrects itself instead of looping. `chdir_offense` survives only to
   SHARPEN that message, so its false-positive ceilings still matter: a wrong
   "this line did it" misdirects the fix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.exec.kernels.cwd_guard import (  # noqa: E402
    chdir_offense, cwd_drift_diagnosis, is_weft_kernel)


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


def test_clean_code_is_not_flagged():
    assert chdir_offense('saveRDS(obj, file.path(work_dir, "o.rds"))', "r") is None
    assert chdir_offense("import os\np = os.path.join(w, 'x.png')", "python") is None
    assert chdir_offense("", "r") is None
    assert chdir_offense("print(getwd())", "r") is None


# ── false-positive ceilings (they keep the DIAGNOSIS from misdirecting) ─────

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
    both miss offenses and finger innocent lines."""
    assert chdir_offense("setwd('/tmp')", "python") is None
    assert chdir_offense("os.chdir('/tmp')", "r") is None


def test_kwarg_named_chdir_is_not_a_call():
    """WIDE — the degenerate shape: `chdir=True` as an argument is not a chdir."""
    assert chdir_offense("subprocess.run(cmd, chdir=True)", "python") is None


# ── the diagnosis (replaces the removed refusal) ─────────────────────────────
#
# A refusal based on scanning submitted code was built and REMOVED: a chdir
# inside a library function is invisible to it (no protection where the user has
# no control), and enforcing it would have disabled an ordinary idiom across all
# kernel work including local sessions. The fatal pattern is also narrower than
# "calls chdir" — a chdir RESTORED before the block ends is harmless — which no
# static scan can tell apart. So aba's job is to make the death legible.

_DEATH = ("Error in file(con, \"w\") : cannot open the connection\n"
          "In addition: Warning message:\n"
          "In file(con, \"w\") : cannot open file 'blocks/0002.rc.tmp': "
          "No such file or directory\nExecution halted")


def test_diagnosis_fires_on_the_driver_write_signature():
    out = cwd_drift_diagnosis(_DEATH, "", "setwd(work_dir)", "r")
    assert out and "working directory moved" in out
    assert "setwd(work_dir)" in out            # names the visible offender
    assert "file.path(" in out                 # the R way out
    assert "substrate limitation" in out       # not the user's fault


def test_diagnosis_is_honest_when_the_source_is_clean():
    """The case the refusal could never handle: a package chdir'd internally.
    The death signature is still present, so the explanation must still fire —
    and must NOT claim the block did it."""
    out = cwd_drift_diagnosis(_DEATH, "", "library(somepkg)\nrun_it()", "r")
    assert out and "library call likely did" in out
    assert "setwd(" not in out.split("To avoid")[0].replace("setwd()", "")


def test_no_diagnosis_for_unrelated_deaths():
    """CEILING: a wrong explanation is worse than none. OOM / walltime / kill
    must not be labelled a cwd problem."""
    for other in ("Killed (out of memory)",
                  "slurm: JOB 123 CANCELLED DUE TO TIME LIMIT",
                  "kernel stopped", ""):
        assert cwd_drift_diagnosis(other, "", "setwd('/x')", "r") is None


def test_diagnosis_language_appropriate():
    out = cwd_drift_diagnosis(_DEATH, "", "os.chdir('/tmp')", "python")
    assert "os.path.join" in out and "contextlib.chdir" in out
    assert "file.path(" not in out


def test_offense_detection_still_used_only_to_sharpen():
    """chdir_offense survives as a DIAGNOSTIC aid, with its false-positive
    ceilings intact — a wrong "this line did it" would misdirect the fix."""
    assert chdir_offense("# setwd('/tmp')", "r") is None
    assert chdir_offense('message("call setwd(x)")', "r") is None
    assert chdir_offense("subprocess.run(cmd, chdir=True)", "python") is None
    assert chdir_offense('lbl <- "col#1"; setwd("/tmp")', "r")
    assert chdir_offense("setwd('/tmp')", "python") is None   # language scoping


# ── the kernel jobdir is shared with the driver's machinery ──────────────────

def test_driver_machinery_is_never_harvested_as_an_output():
    """A weft kernel's sandbox holds BOTH the user's bare relative writes and
    the driver's own bookkeeping, so the harvester sees them together.

    Live (thr_a1f7f687): an ssh timeout killed a block; `current_block` — the
    driver's 3-byte HEARTBEAT, written at block start and removed at block end —
    was left behind with a fresh mtime and became the Run card's only recorded
    output. The filter covered `blocks/` and `kernel.*` only, so ten other
    jobdir entries were equally eligible."""
    from content.bio.tools.run_exec import _is_kernel_machinery
    # the real jobdir listing from the live incident
    for rel in ("current_block", "activate.sh", "cmd.sh", "runner.sh", "log",
                "node", "pid", "pid.epoch", "pid.real", "rusage",
                "driver.R", "driver.py", "blocks/0002.rc", "kernel.json"):
        assert _is_kernel_machinery(rel), rel


def test_real_outputs_are_still_harvested():
    """CEILING: bare relative writes ARE the documented way to produce a Run's
    outputs — over-filtering would silently drop them."""
    from content.bio.tools.run_exec import _is_kernel_machinery
    for rel in ("umap_by_sample.png", "results.csv", "obj.rds",
                "figs/scatter.png", "store.zarr/zarr.json",
                # names that merely resemble machinery must survive
                "pidgin_counts.csv", "logfile_summary.txt", "nodes.tsv",
                "driver_metrics.csv", "current_blocks_summary.tsv"):
        assert not _is_kernel_machinery(rel), rel
