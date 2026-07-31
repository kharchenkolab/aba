"""Cutover-completeness invariant — the substrate is the ONLY kernel transport,
and nothing can silently resurrect a legacy lane.

Why: the platform ran a legacy local kernel transport by default for months
after the substrate migration, invisible to every outcome-level test (outcome
parity between lanes is exactly what a migration produces). This suite pins the
end state STRUCTURALLY so a regression cannot be quiet:

  1. the legacy transport module is gone;
  2. the pool has exactly one transport seam (the substrate factory) and no
     lane-switching fallback;
  3. no exec-affecting setting gates the kernel transport (the opt-in flag is
     retired);
  4. the interactive exec record stamps `compute.substrate="weft"` for a
     substrate kernel session — the mechanism truth the transport oracle reads.

Run: python tests/test_cutover_invariant.py   (also pytest-collectable)
"""
from __future__ import annotations
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_BACKEND = _REPO / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))


def test_legacy_transport_module_is_gone():
    assert not (_BACKEND / "core" / "exec" / "kernels" / "jupyter.py").exists(), \
        "the legacy jupyter kernel transport must stay deleted"
    try:
        import core.exec.kernels.jupyter  # noqa: F401
        raise AssertionError("legacy transport importable")
    except ImportError:
        pass


def test_pool_has_single_transport_no_fallback():
    src = (_BACKEND / "core" / "exec" / "kernels" / "pool.py").read_text()
    assert "jupyter" not in src.lower(), "pool references the legacy transport"
    assert "for_pool" in src, "pool must route through the substrate factory"
    assert "falling back" not in src, "no silent lane-switch language in the pool"


def test_no_transport_gate_setting():
    import core.config as cfg
    assert not hasattr(cfg, "WEFT_KERNELS"), \
        "the kernel-transport opt-in flag must stay retired"


def test_interactive_exec_stamp_is_substrate():
    """The mechanism truth the transport oracle reads: a substrate kernel
    session yields compute.substrate='weft' in the interactive lane's stamp
    (pinned here at the expression level so a refactor can't quietly change
    the stamp the oracle depends on)."""
    src = (_REPO / "backend" / "content" / "bio" / "tools" / "run_exec.py").read_text()
    assert '"weft" if type(sess).__name__ == "WeftKernelSession"' in src, \
        "interactive compute stamp no longer keyed on the substrate session type"


_TESTS = [test_legacy_transport_module_is_gone,
          test_pool_has_single_transport_no_fallback,
          test_no_transport_gate_setting,
          test_interactive_exec_stamp_is_substrate]


def _standalone() -> int:
    import traceback
    rc = 0
    for t in _TESTS:
        try:
            t()
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
