"""ensure_capability must honor the r-bio module (misc/modules.md).

The agent provisions R packages via ensure_capability's prov['r'] branch — which
historically bypassed the module system, so an OFF r-bio still got installed. It must
now refuse (status='blocked') and tell the agent to ask the user to enable it.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.modules.state as mstate               # noqa: E402
import core.modules.manager as mgr                # noqa: E402
from content.bio.tools import discovery           # noqa: E402


def test_block_helper_off_not_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)
    mstate.set_desired("r-bio", "off")
    blk = discovery._r_module_block()
    assert blk and blk["status"] == "blocked" and blk["module"] == "r-bio"
    assert "turned off" in blk["note"].lower() and "ask the user" in blk["note"].lower()


def test_block_helper_first_use_proceeds(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)
    mstate.set_desired("r-bio", "first_use")        # auto-install allowed → no block
    assert discovery._r_module_block() is None


def test_block_helper_off_but_ready_proceeds(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    monkeypatch.setattr(mgr, "probe_ready", lambda s: True)   # already present
    mstate.set_desired("r-bio", "off")
    assert discovery._r_module_block() is None      # nothing to gate — it's installed


