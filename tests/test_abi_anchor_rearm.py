"""ABI anchor must reflect the numpy the base ACTUALLY ships (regression 2026-07-12).

Two defects caused an overlay install (GEOparse) to fail trying to upgrade numpy in
the read-only base:
  A. environment.yml left numpy unpinned → the solve picked numpy 2.5.x, which numba
     (transitive via scanpy/scvi-tools) can't use (needs <2.5) → the base shipped broken.
  B. the startup self-heal downgraded numpy to satisfy numba but left the ABI anchor
     file naming the pre-repair numpy → overlay installs pinned transitive numpy to a
     version the base no longer had, forcing a read-only-base upgrade that fails.

This guards both: the manifests pin numpy<2.5, and self_heal_base re-arms the anchor
(force=True) after any repair.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.exec.env_integrity as ei          # noqa: E402


def test_manifests_pin_numpy_below_2_5():
    for name in ("environment.yml", "environment-boot.yml"):
        txt = (ROOT / "install" / "core" / name).read_text()
        m = re.search(r"^\s*-\s*numpy(\S*)\s*(#.*)?$", txt, re.MULTILINE)
        assert m, f"{name} has no top-level numpy entry"
        spec = m.group(1)
        assert spec, f"{name} pins numpy but the pin is empty ({m.group(0)!r})"
        assert "<2.5" in spec or "=2.4" in spec, \
            f"{name} must cap numpy below 2.5 for numba; got {m.group(0)!r}"


def _run_self_heal(monkeypatch, *, broken: bool):
    """Drive self_heal_base past its guards with a base that is (broken→repaired) or ok,
    recording whether the ABI anchor is re-armed with force=True."""
    calls = {"force": []}
    monkeypatch.setattr(ei, "_base_site_dir", lambda: ROOT)            # exists
    monkeypatch.setattr(ei, "env_selfcheck",
                        lambda **k: {"ok": True, "checks": {"abi_anchor_armed": {"ok": True, "detail": "numpy==2.5.1"}}})
    monkeypatch.setattr(ei, "base_stage", lambda: "ready")
    monkeypatch.setattr(ei, "base_is_readonly_fs", lambda: False)
    monkeypatch.setattr(ei, "base_fingerprint", lambda: "fp")
    monkeypatch.setattr(ei, "_verified_stamp_matches", lambda fp: False)
    # broken on the first deep check, healthy after repair; or healthy throughout.
    seq = iter([{"ok": False, "problems": ["numba needs numpy<2.5"]},
                {"ok": True, "problems": []}] if broken
               else [{"ok": True, "problems": []}])
    monkeypatch.setattr(ei, "base_health", lambda **k: next(seq))
    monkeypatch.setattr(ei, "repair_base", lambda **k: {"repaired": True, "installed": "lock-closure"})
    monkeypatch.setattr(ei, "set_base_writable", lambda v: True)
    monkeypatch.setattr(ei, "_write_verified_stamp", lambda fp: None)

    class _P:
        def read_text(self, *a, **k): return "numpy==2.4.6\n"
    def _anchor(**k):
        calls["force"].append(bool(k.get("force")))
        return _P()
    monkeypatch.setattr(ei, "abi_anchor_constraints", _anchor)
    ei.self_heal_base(log=lambda *_: None)
    return calls


def test_anchor_rearmed_force_after_repair(monkeypatch):
    calls = _run_self_heal(monkeypatch, broken=True)
    assert True in calls["force"], "a repair must re-arm the ABI anchor with force=True"


def test_anchor_not_rearmed_when_base_healthy(monkeypatch):
    calls = _run_self_heal(monkeypatch, broken=False)
    # no repair → no forced re-arm (env_selfcheck's normal arm is stubbed out here)
    assert True not in calls["force"]
