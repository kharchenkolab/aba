"""job_hpc_info routes to the submitter the job RECORDS it ran under — a weft-lane
job reads WeftSubmitter().info (the default cluster path now), not the wrong
'local'. Guards the W3.x weft-lane repoint of the Jobs-tab scheduler info."""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_jhi_"))
sys.path.insert(0, str(ROOT / "backend"))

from core.exec.hpc_session import job_hpc_info  # noqa: E402


def test_weft_job_reads_weft_info(monkeypatch):
    import core.jobs.weft_submitter as ws
    monkeypatch.setattr(ws.WeftSubmitter, "info",
                        lambda self, job: {"scheduler": "weft", "state": "RUNNING", "node": "c1"})
    out = job_hpc_info({"params": {"submitter": "weft", "weft_id": "jb_1"}})
    assert out["scheduler"] == "weft" and out["state"] == "RUNNING"


def test_legacy_slurm_record_reads_local():
    # The sbatch lane is retired: an OLD job recorded submitter=='slurm' no longer
    # has a SlurmSubmitter to query, so job_hpc_info reads it as a local job.
    out = job_hpc_info({"params": {"submitter": "slurm", "slurm_id": "42"}})
    assert out == {"submitter": "local"}


def test_local_job_reports_local():
    assert job_hpc_info({"params": {"submitter": "local"}}) == {"submitter": "local"}
    assert job_hpc_info({"params": {}}) == {"submitter": "local"}


TESTS = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
if __name__ == "__main__":
    for fn in TESTS:
        try:
            import inspect
            fn() if not inspect.signature(fn).parameters else print(f"  (skip {fn.__name__}: needs monkeypatch)")
            print(f"  ok {fn.__name__}")
        except Exception as e:
            print(f"  FAIL {fn.__name__}: {e!r}")
