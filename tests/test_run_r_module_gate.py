"""run_r (_ensure_r_kernelspec) gates on the r-bio module (misc/modules.md):
  • OFF → refuse (ask the user), don't install.
  • first_use / on + not ready → install INLINE via install_and_wait (progress), then
    proceed — NOT a fire-and-forget 'retry later' back-off.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import pytest                                        # noqa: E402
import core.modules.manager as mgr                   # noqa: E402
import core.modules.state as mstate                  # noqa: E402
import core.modules.reconciler as rec                # noqa: E402
import core.exec.kernels.jupyter as jk               # noqa: E402
import core.exec.materialize as materialize          # noqa: E402


def test_run_r_installs_inline_on_first_use(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    jk._r_spec_ready = False
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)     # r-bio not built
    mstate.set_desired("r-bio", "first_use")
    calls = []
    monkeypatch.setattr(rec, "install_and_wait", lambda mid, **k: (calls.append(mid) or (True, None)))
    monkeypatch.setattr(materialize, "tools_env", lambda: tmp_path / "tools")
    monkeypatch.setattr(jk, "_r_spec_points_into", lambda name, tenv: True)   # spec good post-install

    out = jk._ensure_r_kernelspec()
    assert out == jk._R_SPEC_NAME
    assert calls == ["r-bio"]                                    # installed INLINE, not deferred


def test_run_r_refuses_when_off(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    jk._r_spec_ready = False
    monkeypatch.setattr(mgr, "probe_ready", lambda s: False)
    mstate.set_desired("r-bio", "off")
    called = []
    monkeypatch.setattr(rec, "install_and_wait", lambda mid, **k: called.append(mid))
    with pytest.raises(RuntimeError, match="turned OFF"):
        jk._ensure_r_kernelspec()
    assert called == []                                          # OFF → no install


def test_run_r_proceeds_when_ready(monkeypatch, tmp_path):
    monkeypatch.setenv("ABA_HOME", str(tmp_path))
    jk._r_spec_ready = False
    monkeypatch.setattr(mgr, "probe_ready", lambda s: True)       # r-bio ready
    called = []
    monkeypatch.setattr(rec, "install_and_wait", lambda mid, **k: called.append(mid))
    monkeypatch.setattr(materialize, "tools_env", lambda: tmp_path / "tools")
    monkeypatch.setattr(jk, "_r_spec_points_into", lambda name, tenv: True)
    assert jk._ensure_r_kernelspec() == jk._R_SPEC_NAME
    assert called == []                                          # already ready → no install
