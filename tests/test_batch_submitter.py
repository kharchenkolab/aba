"""ondemand.md P6 — the BatchSubmitter abstraction.

Phase 1: LocalSubmitter wraps today's in-process behavior; the factory selects by
``ABA_BATCH_SUBMITTER``. The Slurm submitter is exercised in test_slurm_submitter.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform


def test_factory_defaults_to_weft_local_lane(monkeypatch):
    monkeypatch.delenv("ABA_BATCH_SUBMITTER", raising=False)
    from core.jobs.submitter import get_submitter, submitter_name
    from core.jobs.weft_submitter import WeftSubmitter
    assert submitter_name() == "local"
    # the default lane is a weft task — no in-process fallback since the
    # cutover; a substrate outage surfaces as submit()'s typed error instead
    assert isinstance(get_submitter(), WeftSubmitter)


def test_factory_reads_env(monkeypatch):
    from core.jobs.submitter import submitter_name
    monkeypatch.setenv("ABA_BATCH_SUBMITTER", "slurm")
    assert submitter_name() == "slurm"


def test_local_submitter_submit_enqueues_and_info():
    from core.jobs.runner import LocalSubmitter, _QUEUE
    sub = LocalSubmitter()
    assert sub.name == "local"
    before = _QUEUE.qsize()
    sub.submit({"id": "job_t1", "params": {"project_id": "p1"}})
    assert _QUEUE.qsize() == before + 1
    assert sub.poll({"id": "job_t1"}) is None          # worker owns lifecycle
    assert sub.info({"id": "job_t1"})["submitter"] == "local"


def test_local_submitter_cancel_registers():
    from core.jobs.runner import LocalSubmitter, _CANCELLED
    from core.runtime import cancellation
    cancellation.acquire("job_c1")
    LocalSubmitter().cancel({"id": "job_c1", "params": {}})
    assert "job_c1" in _CANCELLED
    cancellation.release("job_c1")
