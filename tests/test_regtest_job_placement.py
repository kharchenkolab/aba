"""A `requires: slurm` scenario must PROVE the job ran on the cluster.

Live, 2026-08-25 (OOD, cbe-next). `ABA_BATCH_SUBMITTER=slurm` was set and no
slurm-kind weft site was declared, so `submitter._slurm_lane` printed one line
and returned the LOCAL lane. Every "cluster" job ran inside the user's session
container.

The regtest suite could not see this. `requires: slurm` gates on
`submitter_name()` — the SETTING — and `background_job` asserts the job ran
clean. Both are satisfied by a job that ran locally, so the sweep would have
reported the scheduler covered while nothing was ever scheduled. That is the
mechanical answer to "slurm was tested extensively — how did this slip by?":
the oracle asserted the OUTCOME and never the PLACEMENT.

The rule added here is automatic, not opt-in. A per-scenario `on_site:` key
would only be present in scenarios whose author already suspected the failure.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "regtest" / "harness"))


class _Client:
    """Serves one job row; `site` becomes params.weft_site (None = the row a
    locally-degraded lane writes)."""

    def __init__(self, site):
        self._site = site

    def get(self, path, **kw):
        if path.startswith("/api/jobs/"):
            params = {"weft_site": self._site} if self._site else {}
            body = {"status": "done", "params": params}
        else:                       # every other probe run_checks makes
            body = {}
        return type("R", (), {"json": lambda _s: body})()


def _fails(monkeypatch, tmp_path, *, site, requires):
    import runner
    # result.json on disk is the authoritative completion signal
    d = tmp_path / "proj" / "j1"
    d.mkdir(parents=True)
    (d / "result.json").write_text('{"returncode": 0, "stdout": "ok"}')
    monkeypatch.setattr(runner, "RUN", tmp_path)
    monkeypatch.setattr(runner, "REQUIRES", requires, raising=False)
    step = {"expect": {"background_job": {"ok": True}}}
    cap = {"text": "", "jobs": ["j1"], "tools": [], "tool_calls": []}
    return runner.run_checks(step, cap, {}, [], _Client(site), "p", "t", {}, [])


def test_slurm_scenario_fails_when_the_job_ran_locally(monkeypatch, tmp_path):
    """THE regression: clean job, wrong machine, and the scenario said green."""
    fails = _fails(monkeypatch, tmp_path, site=None, requires="slurm")
    assert any("placement" in f for f in fails), fails
    assert any("LOCAL" in f for f in fails), fails


def test_slurm_scenario_passes_when_the_job_ran_on_the_cluster(monkeypatch, tmp_path):
    """WIDE: a correctly routed job must not be flagged."""
    fails = _fails(monkeypatch, tmp_path, site="cluster", requires="slurm")
    assert not any("placement" in f for f in fails), fails


def test_placement_is_not_demanded_of_a_local_scenario(monkeypatch, tmp_path):
    """WIDE: most scenarios do not require slurm and must stay unaffected."""
    fails = _fails(monkeypatch, tmp_path, site=None, requires="")
    assert not any("placement" in f for f in fails), fails


def test_explicit_local_site_is_still_a_failure(monkeypatch, tmp_path):
    """`weft_site: local` is the same degrade, just recorded rather than absent."""
    fails = _fails(monkeypatch, tmp_path, site="local", requires="slurm")
    assert any("placement" in f for f in fails), fails
