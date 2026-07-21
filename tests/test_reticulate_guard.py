"""Guards for the cross-language-bridge failure (live 2026-07-21).

An R/Seurat session needed a viewer store. The capability catalog described lstar
as a Python package, so the agent wrote `library(reticulate); import("lstar")`.
reticulate had no configured Python, started bootstrapping its own (downloading
`uv`, then an interpreter, then packages), and hung the turn for 3.7 minutes until
the user killed it.

Two independent defects, one guard each:
  - nothing told the agent that reaching across languages is a smell, so it never
    reconsidered and looked for the native route;
  - reticulate was unpinned, so the reflex turned into an unbounded network
    install instead of a fast, legible failure.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio

TOOLS = [{"name": "run_python"}, {"name": "run_r"},
         {"name": "register_dataset"}, {"name": "Skill"}]


@pytest.mark.parametrize("mode", ["full", "standard", "lean", "lean_small"])
def test_bridge_smell_rule_renders_in_every_tier(mode):
    """`behavior.md` is swapped for `behavior_slim.md` on the lean tiers, so a rule
    added to only one file reaches only half the deployments."""
    from content.bio.prompts.build import build_system
    stable, _ = build_system(TOOLS, role="primary", intent="analyze", ctx={}, mode=mode)
    assert "reticulate" in stable, (
        f"the cross-language-bridge rule is missing from the {mode} system prompt")


def test_bridge_rule_offers_the_alternative_not_just_a_prohibition():
    """A bare 'don't' leaves the agent stuck where it already was. The rule has to
    name the way out — the native route, or handing the object over as a file."""
    from content.bio.prompts.build import build_system
    stable, _ = build_system(TOOLS, role="primary", intent="analyze", ctx={}, mode="full")
    seg = stable[stable.index("reticulate"): stable.index("reticulate") + 1200]
    assert "FILE" in seg or "file" in seg, "rule must offer the file hand-off"
    assert "recipe" in seg or "native" in seg, "rule must point at the native route"


def test_r_kernel_pins_reticulate_python():
    """Every R kernel must start with RETICULATE_PYTHON bound to a real
    interpreter — that is what stops the managed-venv/uv bootstrap. Unset, an
    `import()` becomes an unbounded download inside a provisioned session."""
    from core.exec.kernels.weft import _weft_setup_code
    setup = _weft_setup_code("r")
    lines = [ln for ln in setup.splitlines() if "RETICULATE_PYTHON" in ln]
    assert lines, "R kernel setup does not pin RETICULATE_PYTHON"
    # it must name an actual path, not an empty string (an empty pin is no pin —
    # reticulate would fall straight back to bootstrapping)
    ln = lines[0]
    assert "''" not in ln and '""' not in ln, f"empty RETICULATE_PYTHON pin: {ln}"
    assert "/" in ln, f"RETICULATE_PYTHON is not a path: {ln}"


def test_python_kernel_is_untouched():
    """The pin is an R-side concern; the Python setup block must not grow it."""
    from core.exec.kernels.weft import _weft_setup_code
    assert "RETICULATE" not in _weft_setup_code("python")


def test_pin_survives_a_broken_project_env():
    """Kernel startup must never fail because the project's Python session can't be
    resolved (mount-scoped base, no project bound, substrate down) — the pin falls
    back to the controller interpreter, which always exists."""
    import core.compute.base_env as be
    from core.exec.kernels.weft import _weft_setup_code
    orig = be.active
    be.active = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("substrate down"))
    try:
        setup = _weft_setup_code("r")
    finally:
        be.active = orig
    assert "RETICULATE_PYTHON" in setup, "pin dropped when the project env is unresolvable"
