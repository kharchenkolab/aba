"""aba doctor — the weft compute-substrate surface (`_compute_surface`).

`_compute_surface` runs a snippet in the install's backend python and parses its
`ABA_JSON=` line into `{status, sites}` (item 4d: doctor prints
core.compute.status() + the declared weft sites). Here we inject a fake
`_run_in_backend` result (no real backend needed) and pin the parse + the
graceful-None degradation paths.
"""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace

from aba_installer import cli


def _fake_proc(stdout: str, returncode: int = 0):
    return SimpleNamespace(stdout=stdout, stderr="", returncode=returncode)


def test_compute_surface_parses_status_and_sites(monkeypatch):
    payload = {"status": {"ok": True, "detail": "weft workspace /x (pixi: /p)"},
               "sites": [{"name": "local", "kind": "local", "host": None},
                         {"name": "cluster", "kind": "slurm", "host": None}]}
    # a real snippet prints noise (compute/startup logs) before the ABA_JSON line
    out = "[compute] registered site 'cluster'\nABA_JSON=" + json.dumps(payload) + "\n"
    monkeypatch.setattr(cli, "_run_in_backend",
                        lambda home, script, timeout=30: _fake_proc(out))
    got = cli._compute_surface(Path("/nonexistent"))
    assert got == payload
    assert got["status"]["ok"] is True
    assert {s["name"] for s in got["sites"]} == {"local", "cluster"}


def test_compute_surface_degraded_status_still_parses(monkeypatch):
    payload = {"status": {"ok": False, "detail": "pixi binary not found — weft substrate offline"},
               "sites": [{"name": "local", "kind": "local", "host": None}]}
    monkeypatch.setattr(cli, "_run_in_backend",
                        lambda home, script, timeout=30: _fake_proc("ABA_JSON=" + json.dumps(payload)))
    got = cli._compute_surface(Path("/x"))
    assert got["status"]["ok"] is False and "pixi" in got["status"]["detail"]
    assert got["sites"] == [{"name": "local", "kind": "local", "host": None}]


def test_compute_surface_none_when_backend_unresolvable(monkeypatch):
    # _run_in_backend returns None when the env python / backend dir don't exist
    monkeypatch.setattr(cli, "_run_in_backend", lambda home, script, timeout=30: None)
    assert cli._compute_surface(Path("/nonexistent")) is None


def test_compute_surface_none_on_nonzero_or_garbage(monkeypatch):
    monkeypatch.setattr(cli, "_run_in_backend",
                        lambda home, script, timeout=30: _fake_proc("boom", returncode=1))
    assert cli._compute_surface(Path("/x")) is None
    # returncode 0 but no ABA_JSON line → None (not a crash)
    monkeypatch.setattr(cli, "_run_in_backend",
                        lambda home, script, timeout=30: _fake_proc("no json here\n"))
    assert cli._compute_surface(Path("/x")) is None
