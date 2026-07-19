"""P0/P1 behavioral guard — end-to-end Run reconciliation via the replay harness.

Drives the REAL turn flow (present_plan → Go → a plain follow-up re-run) on a
real weft kernel with a FakeStream fixture, then asserts the durability
invariants P1 must guarantee:

  1. CUMULATIVE keeper set — the retain selection after the re-run turn covers
     BOTH turns' file outputs, not just the last turn's (the delta data-loss
     bug: weft put_retained is INSERT OR REPLACE per target, so a delta retain
     drops earlier keeps at pin settlement).
  2. DIRECTORY store retained — a directory-shaped output (`*.zarr`) enters the
     keeper set (harvest lists files only, so a store dir is invisible to
     artifacts_for_run without the explicit jobdir scan P1 adds).
  3. SETTLEMENT — after the kernel stops, the pins settle to `done` and the
     retained tree physically contains every kept file (incl. the store's
     enumerated contents).

Opt-in (needs weft + a realized local python env): ABA_WEFT_KERNEL_IT=1.
Standalone: `ABA_WEFT_KERNEL_IT=1 python tests/test_replay_reconcile.py`.

Runs its own isolated runtime and cleans up after itself (harness teardown).
Before P1 lands this test FAILS (bug reproduction); after P1 it passes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
for p in (str(_ROOT), str(_ROOT / "backend")):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import pytest
    pytestmark = pytest.mark.platform
except ImportError:  # pragma: no cover
    pytest = None

_ENABLED = os.environ.get("ABA_WEFT_KERNEL_IT") == "1"
_FIXTURE = _ROOT / "tests" / "fixtures" / "replay_reconcile.jsonl"


def _skip(msg: str):
    if pytest is not None:
        pytest.skip(msg, allow_module_level=False)
    raise SystemExit(f"SKIP: {msg}")


def test_turn_end_reconciliation_is_cumulative_and_includes_dir_store():
    if not _ENABLED:
        _skip("set ABA_WEFT_KERNEL_IT=1 to run the replay reconciliation test")
    from regtest.harness.replay import ReplayHarness, SubstrateUnavailable
    try:
        h = ReplayHarness(name="reconcile", fixture=_FIXTURE)
    except SubstrateUnavailable as e:
        _skip(str(e))
    try:
        # msg1: present_plan → auto-Go → step 1 (file + directory store)
        h.drive("Please run the two-step export.")
        rid = h.active_run_id()
        assert rid, "no Run opened for the plan"
        assert h.targets(rid), "Run recorded no weft target (kernel path not taken?)"

        # msg2: a PLAIN follow-up re-run (no plan) → only on_stop fires
        h.drive("Also export the final summary.")

        # (1) + (2): cumulative keeper set incl. the directory store
        sel = h.selection_paths(rid)
        assert "summary_early.csv" in sel, f"step-1 file dropped from keeper set: {sel}"
        assert "summary_final.csv" in sel, (
            f"re-run file never retained — on_stop seam / cumulative fix missing: {sel}")
        assert "dataset_cube.zarr" in sel, (
            f"directory store never entered the keeper set: {sel}")

        # (3): settle pins at kernel stop, then the retained tree holds them all
        h.settle()
        rows = h.wait_for_state(rid, "done")
        assert any(r.get("state") == "done" for r in rows), f"pins never settled: {rows}"
        kept = h.kept_files(rid)
        assert "summary_early.csv" in kept, f"step-1 file not in retained tree: {kept}"
        assert "summary_final.csv" in kept, f"re-run file not in retained tree: {kept}"
        assert any(k.startswith("dataset_cube.zarr") for k in kept), (
            f"directory store contents not in retained tree: {kept}")
    finally:
        h.close()


if __name__ == "__main__":
    if not _ENABLED:
        print("SKIP: set ABA_WEFT_KERNEL_IT=1")
        raise SystemExit(0)
    try:
        test_turn_end_reconciliation_is_cumulative_and_includes_dir_store()
        print("[PASS] turn-end reconciliation cumulative + dir store")
    except SystemExit as e:
        print(e)
    except AssertionError as e:
        print(f"[FAIL] {e}")
        raise SystemExit(1)
