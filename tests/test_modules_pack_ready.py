"""Guard: a mount-adopted base pack reads as READY (not 'pending').

Regression (live 2026-07-21): python-bio/r-bio showed 'pending / not installed'
in Settings→Modules on the SIF/pack deployment even though the packs were adopted
by squashfs mount and fully functional (scanpy imported, pipelines ran). Cause:
_pack_ready matched weft `list_envs` by `name == pack`, but an ADOPTED env leaves
no local spec row, so its name is empty and the match never hit. Fix: resolve the
EnvID via the catalog adoption path, and accept an adopted RO realization
(state=ready read_only=1) as ready."""
from __future__ import annotations
import pytest

from core.modules import manager

pytestmark = pytest.mark.platform


class _FakeCompute:
    def __init__(self, status_map, list_envs=None):
        self._s, self._l = status_map, list_envs or []

    def sync_call(self, name, *a):
        if name == "env_status":
            return self._s.get(a[0], {"realizations": []})
        if name == "list_envs":
            return {"envs": self._l}
        return {}


def _wire(monkeypatch, *, adopt_eid, status_map, list_envs=None):
    import core.compute as cc
    monkeypatch.setattr(cc.seeding, "adopt_env_id", lambda pack: adopt_eid)
    monkeypatch.setattr(cc, "get_compute", lambda: _FakeCompute(status_map, list_envs))


def test_adopted_mount_reads_ready(monkeypatch):
    # the exact live shape: adopted squashfs RO mount, state=ready read_only=1
    _wire(monkeypatch, adopt_eid="env:x",
          status_map={"env:x": {"realizations": [
              {"site": "local", "strategy": "squashfs", "state": "ready", "read_only": "1"}]}})
    assert manager._pack_ready("python-bio") is True


def test_adopted_ro_state_without_ready_still_counts(monkeypatch):
    _wire(monkeypatch, adopt_eid="env:x",
          status_map={"env:x": {"realizations": [
              {"site": "local", "state": "adopted-ro", "read_only": True}]}})
    assert manager._pack_ready("python-bio") is True


def test_not_yet_realized_reads_not_ready(monkeypatch):
    _wire(monkeypatch, adopt_eid="env:x",
          status_map={"env:x": {"realizations": [
              {"site": "local", "state": "building", "read_only": 0}]}})
    assert manager._pack_ready("python-bio") is False


def test_locally_solved_base_matches_by_name(monkeypatch):
    # no catalog adoption (writable deploy) → fall back to spec-name match
    _wire(monkeypatch, adopt_eid=None,
          status_map={"env:y": {"realizations": [
              {"site": "local", "state": "ready", "read_only": 0}]}},
          list_envs=[{"name": "python-bio", "env_id": "env:y"}])
    assert manager._pack_ready("python-bio") is True


def test_unknown_pack_reads_not_ready(monkeypatch):
    _wire(monkeypatch, adopt_eid=None, status_map={}, list_envs=[])
    assert manager._pack_ready("python-bio") is False
