"""Workspace-orientation preamble on a FRESH kernel.

Live friction (prj_8143327c thr_80190faf, 2026-06-16): a backend
restart killed the R kernel; the respawned kernel had no _aba_cwd,
so _ensure_kernel_cwd's just-switched flag stayed unset and the
existing 'Files from prior runs' preamble never fired. The agent
then:

  - tried to use `obj` (kernel state gone) → German R error
  - guessed wrong paths for reloading seurat_integrated.rds → file
    not found
  - went through several rounds of `find`-and-retry

Each of these would have been a non-issue if the agent had seen a
'Fresh kernel — workspace orientation' block on the first call:
WORK_DIR named, files-on-disk listed by absolute path, an explicit
'in-memory state is gone' header.

This test exercises:

  1. _ensure_kernel_cwd marks the FIRST cwd set on a fresh sess with
     the '__FRESH__' sentinel (regression guard — the prior code only
     marked genuine cwd switches).
  2. _prior_run_files_preamble(fresh_kernel=True) renders the
     dedicated header AND lists files in the current cwd (which the
     non-fresh preamble path skips).
  3. _prior_run_files_preamble(fresh_kernel=False) continues to render
     the original 'cwd just shifted' header.

Run: .venv/bin/python tests/test_fresh_kernel_preamble.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_fresh_kernel_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "fk.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]        = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]    = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db  # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


class FakeSess:
    """Minimal stand-in for the kernel session: only the attrs
    _ensure_kernel_cwd touches and a record of executed snippets."""
    def __init__(self):
        self.executed: list[str] = []
    def execute(self, code: str, timeout_s: int = 15):
        self.executed.append(code)
        class _R: returncode = 0
        return _R()


def test_first_cwd_set_marks_fresh_kernel():
    print("[1] fresh kernel's first cwd set → just_switched == '__FRESH__'")
    from content.bio.tools.run_exec import _ensure_kernel_cwd
    sess = FakeSess()
    cwd = str(Path(_tmp) / "work" / "ana_X")
    Path(cwd).mkdir(parents=True, exist_ok=True)
    _ensure_kernel_cwd(sess, "python", cwd)
    check("_aba_cwd is set",
          getattr(sess, "_aba_cwd", None) == cwd,
          f"got {getattr(sess, '_aba_cwd', None)!r}")
    check("just_switched == '__FRESH__' (was None pre-fix)",
          getattr(sess, "_aba_cwd_just_switched", None) == "__FRESH__",
          f"got {getattr(sess, '_aba_cwd_just_switched', None)!r}")


def test_second_call_same_cwd_is_a_noop():
    print("\n[2] same cwd repeat → no extra mark")
    from content.bio.tools.run_exec import _ensure_kernel_cwd
    sess = FakeSess()
    cwd = str(Path(_tmp) / "work" / "ana_X2")
    Path(cwd).mkdir(parents=True, exist_ok=True)
    _ensure_kernel_cwd(sess, "python", cwd)
    sess._aba_cwd_just_switched = None  # consume the fresh mark
    _ensure_kernel_cwd(sess, "python", cwd)
    check("repeat call doesn't re-fire the marker",
          getattr(sess, "_aba_cwd_just_switched", None) is None)


def test_genuine_cwd_switch_marks_prev_cwd():
    print("\n[3] genuine cwd switch → marker == previous cwd (regression guard)")
    from content.bio.tools.run_exec import _ensure_kernel_cwd
    sess = FakeSess()
    cwd_a = str(Path(_tmp) / "work" / "ana_A")
    cwd_b = str(Path(_tmp) / "work" / "ana_B")
    Path(cwd_a).mkdir(parents=True, exist_ok=True)
    Path(cwd_b).mkdir(parents=True, exist_ok=True)
    _ensure_kernel_cwd(sess, "python", cwd_a)
    sess._aba_cwd_just_switched = None      # consume fresh mark
    _ensure_kernel_cwd(sess, "python", cwd_b)
    check("just_switched holds the previous cwd, not the sentinel",
          getattr(sess, "_aba_cwd_just_switched", None) == cwd_a,
          f"got {getattr(sess, '_aba_cwd_just_switched', None)!r}")


def test_fresh_kernel_preamble_says_so_and_lists_cwd_files():
    print("\n[4] preamble(fresh_kernel=True) lists files in cwd + 'in-memory GONE' header")
    from content.bio.tools.run_exec import _prior_run_files_preamble
    cwd = str(Path(_tmp) / "work" / "ana_with_files")
    Path(cwd).mkdir(parents=True, exist_ok=True)
    # Simulate files saved earlier this Run that the fresh kernel can
    # reload — the exact case the live bug hit.
    (Path(cwd) / "seurat_integrated.rds").write_text("x")
    (Path(cwd) / "umap_annotated.png").write_text("y")
    text = _prior_run_files_preamble(
        project_id="prj_test", thread_id="thr_test",
        current_run_id=None, cwd=cwd, fresh_kernel=True,
    )
    check("dedicated fresh-kernel header present",
          "Fresh kernel" in text, f"text=\n{text}")
    check("'in-memory state is GONE' wording present",
          "In-memory state" in text and "GONE" in text,
          f"text=\n{text}")
    check("CWD path surfaced",
          cwd in text, f"text=\n{text}")
    check("cwd's seurat_integrated.rds listed by absolute path",
          "seurat_integrated.rds" in text and cwd in text,
          f"text=\n{text}")
    check("cwd's umap_annotated.png listed",
          "umap_annotated.png" in text, f"text=\n{text}")
    check("'Files already in the current cwd' header present",
          "Files already in the current cwd" in text,
          f"text=\n{text}")


def test_non_fresh_preamble_keeps_original_header():
    print("\n[5] preamble(fresh_kernel=False) = the pre-2026-06-16 'cwd just shifted' shape")
    from content.bio.tools.run_exec import _prior_run_files_preamble
    from core.data.workspace import scratch_dir
    # Seed something in the thread shared scratch so the non-fresh
    # path has content to render (pre-fix behavior: returns "" with
    # nothing to surface).
    sp = scratch_dir("prj_test_5", "thread-thr_test_5")
    (sp / "interim.csv").write_text("a,b\n")
    cwd = str(Path(_tmp) / "work" / "ana_normal")
    Path(cwd).mkdir(parents=True, exist_ok=True)
    text = _prior_run_files_preamble(
        project_id="prj_test_5", thread_id="thr_test_5",
        current_run_id=None, cwd=cwd, fresh_kernel=False,
    )
    check("original header present",
          "cwd just shifted" in text, f"text=\n{text}")
    check("no 'Fresh kernel' header",
          "Fresh kernel" not in text, f"text=\n{text}")
    check("no 'In-memory state' nag on the normal path",
          "In-memory state" not in text, f"text=\n{text}")
    check("no 'Files already in the current cwd' on non-fresh path",
          "Files already in the current cwd" not in text, f"text=\n{text}")


def test_fresh_kernel_with_no_files_still_renders_header():
    print("\n[6] fresh kernel + empty cwd → preamble still warns about state loss")
    from content.bio.tools.run_exec import _prior_run_files_preamble
    cwd = str(Path(_tmp) / "work" / "ana_empty")
    Path(cwd).mkdir(parents=True, exist_ok=True)
    text = _prior_run_files_preamble(
        project_id="prj_test", thread_id="thr_test_3",
        current_run_id=None, cwd=cwd, fresh_kernel=True,
    )
    # Even when there's nothing to list, the agent needs to know the
    # kernel is fresh — saved objects are gone, reload pattern applies.
    check("fresh-kernel header still rendered",
          "Fresh kernel" in text, f"text=\n{text}")


def main() -> int:
    test_first_cwd_set_marks_fresh_kernel()
    test_second_call_same_cwd_is_a_noop()
    test_genuine_cwd_switch_marks_prev_cwd()
    test_fresh_kernel_preamble_says_so_and_lists_cwd_files()
    test_non_fresh_preamble_keeps_original_header()
    test_fresh_kernel_with_no_files_still_renders_header()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL FRESH-KERNEL PREAMBLE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def test_r_banner_marks_the_tool_namespace_boundary():
    """Live thr_ee54c469 (2026-07-23): the R kernel banner advertised
    `ensure_capability(name)` in bare call syntax while the agent was
    composing R — it inlined it into the R cell (rc=1). The two namespaces
    collide exactly there: in the R lane the banner must NAME the boundary
    (it is a platform tool, never an R function); bare `tool(name)` syntax
    with no marker is forbidden in the R banner."""
    import inspect
    from content.bio.tools import run_exec
    src = inspect.getsource(run_exec)
    # find the R-lane banner line mentioning ensure_capability
    banner = next((ln for ln in src.splitlines()
                   if "ensure_capability" in ln and "Need a" in ln), "")
    assert banner, "the R capability banner is gone — update this guard"
    assert "tool" in banner.lower(), (
        f"the R banner does not name the tool boundary: {banner!r}")
    assert "ensure_capability(name)" not in banner, (
        "bare call syntax in the R banner reads as an R function")
    assert "not" in banner.lower() and "R" in banner, (
        f"the banner must say it is NOT callable from R code: {banner!r}")


def test_cwd_banner_states_ephemerality_and_the_keep_lane():
    """Live x4: agents wrote 'kept' files into the sandbox and durably kept
    nothing (or minted a Dataset for a scratch file). The nudge belongs at
    the moment of writing — the cwd line — and must route to the RETENTION
    lane, naming the dataset boundary."""
    import inspect
    from content.bio.tools import run_exec
    src = inspect.getsource(run_exec)
    ln = next((l for l in src.splitlines() if '"cwd: ' in l or "f\"cwd: " in l), "")
    blk = src[src.index("cwd: {cwd}"):src.index("cwd: {cwd}") + 500]
    assert "EPHEMERAL" in blk and "keep_outputs" in blk, blk[:200]
    assert "register_dataset is only" in blk, (
        "the dataset boundary must be stated, or keeps mint Datasets again")
