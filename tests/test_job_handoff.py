"""A background job and the session that submitted it need a directory they can
both name — and when they don't use one, the failure must explain itself.

2026-08-27: an agent wrote data.csv in a foreground turn and read it in a
background job. A weft kernel cannot chdir, so its files land in an ephemeral
sandbox; the job runs in its own directory on another machine. Result: a raw
`FileNotFoundError` that is correct and tells the reader nothing. The single
most ordinary request there is — "make a file, then process it in a job".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from core.jobs.detached_entry import _handoff_hint  # noqa: E402

TRACE = ("Traceback (most recent call last):\n"
         "  File \"._aba_wrapped.py\", line 5, in <module>\n"
         "FileNotFoundError: [Errno 2] No such file or directory: 'data.csv'\n")


def test_explains_a_bare_filename_miss(tmp_path):
    hint = _handoff_hint(TRACE, {"data_dir": str(tmp_path)})
    assert hint, "the defining case produced no hint"
    assert "DATA_DIR" in hint and str(tmp_path) in hint, "must name the shared dir"
    assert "interactive" in hint.lower(), "must say WHERE the file actually is"


def test_silent_when_there_is_no_shared_dir_to_recommend(tmp_path):
    """A genuinely detached site shares no filesystem. Recommending a directory
    that is not there turns one confusing failure into two."""
    assert _handoff_hint(TRACE, {}) == ""
    assert _handoff_hint(TRACE, {"data_dir": str(tmp_path / "nope")}) == ""


def test_silent_for_an_absolute_path(tmp_path):
    """A missing ABSOLUTE path is a different problem — the hint would be
    confidently wrong, which is worse than absent."""
    tr = TRACE.replace("'data.csv'", "'/refs/genome/idx.fa'")
    assert _handoff_hint(tr, {"data_dir": str(tmp_path)}) == ""


def test_silent_for_a_relative_path_with_a_directory(tmp_path):
    tr = TRACE.replace("'data.csv'", "'subdir/data.csv'")
    assert _handoff_hint(tr, {"data_dir": str(tmp_path)}) == ""


def test_silent_for_other_failures(tmp_path):
    """ABSENT is the common shape: most job failures are not this."""
    for tail in ("", "ValueError: bad input",
                 "Traceback (most recent call last):\nKeyError: 'x'\n",
                 "PermissionError: [Errno 13] Permission denied: 'data.csv'"):
        assert _handoff_hint(tail, {"data_dir": str(tmp_path)}) == "", tail[:40]


def test_a_truncated_trace_does_not_produce_a_half_hint(tmp_path):
    """stdout_tail is truncated to a byte budget, so the exception line can be
    cut mid-way. A hint built from a partial match would name the wrong file."""
    assert _handoff_hint("FileNotFoundError: [Errno 2] No such file or direc",
                         {"data_dir": str(tmp_path)}) == ""
