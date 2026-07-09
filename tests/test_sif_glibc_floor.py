"""Guard the SIF base-image glibc-floor rule.

Two behavioral guards for the base-OS-mismatch fix:
  1. install/sif/glibc-floor.sh — the single-source comparison (build.sh + the OOD
     preflight both defer to this rule) flags a base whose glibc is NEWER than the
     target's (the debian:12-on-EL7 bug) and stays quiet otherwise.
  2. aba_preflight.py surfaces ABA_PF_GLIBC_WARN (set by preflight.sh on an overshoot)
     into status.yaml warnings, so a mis-based image is visible on the OOD session card.

Standalone-runnable (no bio content / conftest needed):  python tests/test_sif_glibc_floor.py
"""
from __future__ import annotations
import os
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    import pytest
    pytestmark = pytest.mark.platform
except ImportError:                     # standalone run (base env has no pytest)
    pytest = None

ROOT = Path(__file__).resolve().parents[1]
FLOOR = ROOT / "install" / "sif" / "glibc-floor.sh"
PREFLIGHT = ROOT / "install" / "ood" / "aba_preflight.py"


def _overshoot(base: str, target: str) -> bool:
    """glibc-floor.sh exits 0 iff base > target (INCOMPATIBLE → caller warns)."""
    return subprocess.run(["bash", str(FLOOR), base, target]).returncode == 0


def test_glibc_floor_truth_table():
    # base NEWER than target → overshoot (this is exactly the debian:12 / EL7 bug)
    assert _overshoot("2.36", "2.17")
    assert _overshoot("glibc 2.36", "glibc 2.17")   # tolerates the raw getconf format
    assert _overshoot("2.28", "2.17")               # EL8 base on EL7 nodes
    # base <= target → OK (older-built runs on newer glibc)
    assert not _overshoot("2.17", "2.36")
    assert not _overshoot("2.17", "2.17")
    assert not _overshoot("2.17", "2.34")           # EL7 base on EL9 nodes
    # unknown on either side → never cry wolf
    assert not _overshoot("", "2.17")
    assert not _overshoot("2.17", "")


def test_preflight_surfaces_glibc_warn():
    import yaml
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        site = tdp / "site.yaml"
        site.write_text(
            "site: {name: t}\n"
            f"scopes:\n  user:\n    state_dir: {tdp}/state\n"
            "credentials: {order: [], on_missing: demo_mode}\n")
        warn = "GLIBC_TEST_WARN base 2.36 exceeds node 2.17"
        env = {**os.environ,
               "ABA_SITE_CONFIG": str(site), "ABA_PF_STAGED": str(tdp),
               "ABA_PF_USER": "u", "ABA_PF_HOME": str(tdp), "ABA_PF_GROUP": "",
               "ABA_PF_GLIBC_WARN": warn}
        r = subprocess.run([sys.executable, str(PREFLIGHT)], env=env,
                           capture_output=True, text=True)
        status = yaml.safe_load((tdp / "status.yaml").read_text())
        assert any(warn in w for w in (status.get("warnings") or [])), (r.stdout, r.stderr, status)


def test_preflight_emits_module_config():
    """A site.yaml `modules:` block → aba-env.sh exports ABA_MODULE_INIT/BINDS/LIBS
    (space-joined), which script.sh.erb consumes on the node to bind the host Lmod +
    modulefiles so in-session `module load` works inside the SIF."""
    import yaml
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        (tdp / "site.yaml").write_text(
            "site: {name: t}\n"
            f"scopes:\n  user:\n    state_dir: {tdp}/state\n"
            "credentials: {order: [], on_missing: demo_mode}\n"
            "modules:\n  enabled: true\n  init: /etc/profile.d/lmod.sh\n"
            "  binds: [/opt/ohpc, /software]\n  libs: [libtcl8.5.so]\n")
        env = {**os.environ, "ABA_SITE_CONFIG": str(tdp / "site.yaml"),
               "ABA_PF_STAGED": str(tdp), "ABA_PF_USER": "u", "ABA_PF_HOME": str(tdp), "ABA_PF_GROUP": ""}
        subprocess.run([sys.executable, str(PREFLIGHT)], env=env, capture_output=True, text=True)
        envsh = (tdp / "aba-env.sh").read_text()
        assert "export ABA_MODULE_INIT='/etc/profile.d/lmod.sh'" in envsh, envsh
        assert "export ABA_MODULE_BINDS='/opt/ohpc /software'" in envsh, envsh
        assert "export ABA_MODULE_LIBS='libtcl8.5.so'" in envsh, envsh
        # disabled / absent → no emission
        (tdp / "site2.yaml").write_text(
            "site: {name: t}\n"
            f"scopes:\n  user:\n    state_dir: {tdp}/state2\n"
            "credentials: {order: [], on_missing: demo_mode}\n"
            "modules: {enabled: false, init: /x, binds: [/y]}\n")
        env["ABA_SITE_CONFIG"] = str(tdp / "site2.yaml")
        subprocess.run([sys.executable, str(PREFLIGHT)], env=env, capture_output=True, text=True)
        assert "ABA_MODULE_" not in (tdp / "aba-env.sh").read_text()


if __name__ == "__main__":
    test_glibc_floor_truth_table(); print("glibc_floor truth table: PASS")
    test_preflight_surfaces_glibc_warn(); print("preflight surfaces warn: PASS")
    test_preflight_emits_module_config(); print("preflight emits module config: PASS")
