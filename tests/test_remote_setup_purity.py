"""No CONTROLLER path may appear in a REMOTE kernel's setup block.

This is a bug CLASS, not a bug. The setup code aba injects into a kernel is
assembled from several independent pieces, each of which decides on its own
whether to be remote-aware — so every new piece is a fresh chance to embed a
path that exists only on the controller. It has happened three times:

  * DATA_DIR / ARTIFACTS_DIR — got a `remote` branch (correct today).
  * RETICULATE_PYTHON — added later (2026-07-21) with no `remote` branch, so
    every REMOTE R kernel was pinned to `/Users/…/.aba/env/bin/python`, a path
    that cannot exist on a Linux node. Verified live on mendel.
  * `_ensure_kernel_cwd` — sent `setwd(<controller run dir>)` to remote kernels
    (a different injection point, same mistake).

So this guard checks the ASSEMBLED block rather than any one contributor: it
asserts no controller-only absolute path survives into the remote form. A new
setup contributor that forgets `remote` fails here without anyone remembering
to test it — which is the only way a class-level bug stays fixed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

from core.exec.kernels.weft import _reticulate_pin_r, _weft_setup_code  # noqa: E402


def _controller_roots() -> list[str]:
    """Absolute prefixes that are meaningful ONLY on this controller."""
    import sys as _s
    from core.config import DATA_DIR, ARTIFACTS_DIR
    roots = {str(Path(_s.executable).parent.parent), str(DATA_DIR),
             str(ARTIFACTS_DIR), str(Path.home() / ".aba")}
    return [r for r in roots if r and r not in ("/", "")]


@pytest.mark.parametrize("lang", ["r", "python"])
def test_remote_setup_block_carries_no_controller_path(lang):
    """THE class guard, over the assembled block."""
    block = _weft_setup_code(lang, remote=True)
    for root in _controller_roots():
        assert root not in block, (
            f"remote {lang} setup embeds the controller path {root!r} — it does "
            f"not exist on the site. Offending block:\n{block}")


@pytest.mark.parametrize("lang", ["r", "python"])
def test_remote_setup_binds_dirs_to_the_kernel_sandbox(lang):
    """ARMED: prove the block is real and remote-shaped, so the purity test
    above cannot pass vacuously on an empty or degenerate block."""
    block = _weft_setup_code(lang, remote=True)
    assert len(block) > 40, block
    assert ("getwd()" in block) if lang == "r" else ("getcwd()" in block)
    assert "DATA_DIR" in block and "WORK_DIR" in block


def test_reticulate_pin_is_local_only():
    """The specific instance: a remote R kernel gets no pin at all, because
    every candidate interpreter is a controller path."""
    assert _reticulate_pin_r(remote=True) == ""
    local = _reticulate_pin_r(remote=False)
    # local may legitimately be empty (no interpreter resolvable), but when it
    # pins, it must pin something concrete
    if local:
        assert "RETICULATE_PYTHON" in local


def test_local_setup_still_uses_project_dirs():
    """CEILING: the local block must keep binding the project's real dirs —
    over-applying the remote form would strip a working feature."""
    block = _weft_setup_code("python", remote=False)
    from core.config import DATA_DIR
    assert str(DATA_DIR) in block or "DATA_DIR" in block
    assert "getcwd()" not in block.split("WORK_DIR")[0], \
        "local DATA_DIR must be the project dir, not the cwd"
