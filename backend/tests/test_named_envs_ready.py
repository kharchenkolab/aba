"""Guard #31 (strategy-blind readiness). Two realization strategies coexist:
directory (a materialized `<prefix>/bin/python`) and SQUASHFS (a read-only image
mounted only inside a weft task/kernel — NO raw prefix at rest). The env-execution
seam must tell 'is it built?' (strategy-blind) apart from 'give me a raw prefix'
(directory-only), or a squashfs cluster falsely reports envs as realize-failed.
See core.compute.named_envs {_realization_ready, ensure_ready, ensure_realized}.
"""
import pytest

from core.compute import named_envs
from core.compute.errors import ComputeError


def _wire(monkeypatch, *, prefix, status):
    """Point _ready_prefix at `prefix` and env_status (via _sync) at `status`."""
    monkeypatch.setattr(named_envs, "_ready_prefix", lambda eid: prefix)
    monkeypatch.setattr(named_envs._adapter, "get_compute",
                        lambda: type("C", (), {"env_status": lambda self, e: None})())
    monkeypatch.setattr(named_envs, "_sync", lambda coro: status)


def test_ready_directory_strategy(monkeypatch):
    _wire(monkeypatch, prefix="/site/envs/x", status={})
    assert named_envs._realization_ready("env:v1:x") is True


def test_ready_squashfs_via_weft_status(monkeypatch):
    # No on-disk prefix, but weft reports a ready local realization (squashfs).
    _wire(monkeypatch, prefix=None,
          status={"realizations": [{"site": "local", "state": "ready",
                                    "strategy": "squashfs"}]})
    assert named_envs._realization_ready("env:v1:x") is True


def test_not_ready_when_no_prefix_and_no_ready_realization(monkeypatch):
    _wire(monkeypatch, prefix=None,
          status={"realizations": [{"site": "local", "state": "missing"}]})
    assert named_envs._realization_ready("env:v1:x") is False


def test_ensure_ready_returns_when_ready_no_realize(monkeypatch):
    _wire(monkeypatch, prefix=None,
          status={"realizations": [{"site": "local", "state": "ready"}]})
    # must NOT raise and must NOT run a realize task (already ready)
    monkeypatch.setattr(named_envs, "_run_realize_task",
                        lambda *a, **k: pytest.fail("should not realize when ready"))
    assert named_envs.ensure_ready("env:v1:x") is None


def _stub_compute(monkeypatch):
    monkeypatch.setattr(named_envs._adapter, "get_compute",
                        lambda: type("C", (), {"env_status": lambda self, e: None})())


def test_ensure_realized_squashfs_raises_no_raw_prefix(monkeypatch):
    # Realized+ready per weft, but never a raw prefix → the clear, actionable
    # error (run through weft), NOT the misleading "lock may be unbuildable".
    _stub_compute(monkeypatch)
    monkeypatch.setattr(named_envs, "_ready_prefix", lambda eid: None)
    monkeypatch.setattr(named_envs, "_run_realize_task", lambda *a, **k: "DONE")
    monkeypatch.setattr(named_envs, "_realization_ready", lambda eid: True)
    with pytest.raises(ComputeError) as ei:
        named_envs.ensure_realized("env:v1:x")
    assert ei.value.code == "env.no_raw_prefix"


def test_ensure_realized_true_failure_still_realize_failed(monkeypatch):
    # No prefix AND not ready → the genuine failure code is preserved.
    _stub_compute(monkeypatch)
    monkeypatch.setattr(named_envs, "_ready_prefix", lambda eid: None)
    monkeypatch.setattr(named_envs, "_run_realize_task", lambda *a, **k: "FAILED")
    monkeypatch.setattr(named_envs, "_realization_ready", lambda eid: False)
    with pytest.raises(ComputeError) as ei:
        named_envs.ensure_realized("env:v1:x")
    assert ei.value.code == "env.realize_failed"
