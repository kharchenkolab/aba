"""IP1 — in-place submission routing (resolve_submission_target + get_submitter_for).

The resolver decides inline (run in ABA's own allocation, no sbatch) vs slurm (sbatch a
dedicated allocation), from the requested execution + the job's heaviest task + ABA's
capacity. Pure logic → unit-tested here; the live capacity probe + inline nf run are
validated on-cluster.

Run: .venv/bin/python tests/test_inplace_submission.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_ip1_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db  # noqa: E402
init_db()
from core.jobs.runner import resolve_submission_target  # noqa: E402
from core.jobs.submitter import get_submitter_for  # noqa: E402


def _cap(submitter="slurm", inline_ok=True, cores=8, mem_gb=32.0, gpus=0):
    return {"submitter": submitter, "inline_ok": inline_ok, "cores": cores,
            "mem_gb": mem_gb, "gpus": gpus}


def test_local_submitter_always_inline():
    t, _ = resolve_submission_target("auto", None, _cap(submitter="local"))
    assert t == "inline"


def test_login_node_forces_slurm():
    t, r = resolve_submission_target("local", {"cpus": 2, "mem_gb": 6}, _cap(inline_ok=False))
    assert t == "slurm" and "login node" in r


def test_fits_runs_inline():
    t, _ = resolve_submission_target("local", {"cpus": 2, "mem_gb": 6}, _cap(cores=8, mem_gb=32))
    assert t == "inline"
    t2, _ = resolve_submission_target("auto", {"cpus": 4, "mem_gb": 16}, _cap(cores=4, mem_gb=16))
    assert t2 == "inline"                       # exactly-fits


def test_exceeds_cores_falls_back_to_slurm():
    t, r = resolve_submission_target("local", {"cpus": 16, "mem_gb": 6}, _cap(cores=8, mem_gb=32))
    assert t == "slurm" and "c >" in r


def test_exceeds_mem_falls_back_to_slurm():
    t, r = resolve_submission_target("auto", {"cpus": 2, "mem_gb": 128}, _cap(cores=8, mem_gb=32))
    assert t == "slurm" and "GB >" in r


def test_no_heaviest_estimate_runs_inline():
    t, _ = resolve_submission_target("local", None, _cap())
    assert t == "inline"                        # nothing known to exceed → in-place


def test_gpu_job_without_local_gpu_falls_back_to_slurm():
    """A GPU job (execution=local/auto) on a GPU-less allocation must route to Slurm —
    NOT run inline on CPU. The submitter then picks a GPU partition from estimate.gpu."""
    t, r = resolve_submission_target("local", {"cpus": 4, "mem_gb": 16, "gpu": True},
                                     _cap(cores=8, mem_gb=32, gpus=0))
    assert t == "slurm" and "GPU" in r
    t2, r2 = resolve_submission_target("auto", {"cpus": 4, "mem_gb": 16, "gpu": True},
                                       _cap(cores=8, mem_gb=32, gpus=0))
    assert t2 == "slurm" and "GPU" in r2


def test_gpu_job_with_local_gpu_runs_inline():
    """When ABA's allocation HAS a GPU, a GPU job runs in-place on it (the local-GPU
    fast path — no queue)."""
    t, _ = resolve_submission_target("local", {"cpus": 4, "mem_gb": 16, "gpu": True},
                                     _cap(cores=8, mem_gb=32, gpus=1))
    assert t == "inline"


def test_unknown_mem_capacity_ignored():
    # mem_gb None (scheduler didn't report it) → only the cores check applies
    t, _ = resolve_submission_target("local", {"cpus": 4, "mem_gb": 999}, _cap(cores=8, mem_gb=None))
    assert t == "inline"


def test_auto_ceiling_fans_out_large_jobs_even_if_they_fit():
    # IP3: a big allocation (256c/512GB) would 'fit' a 12c/128GB task, but AUTO should still
    # fan it out (inline is for small/quick work). Explicit local honors the user's ask.
    big = _cap(cores=256, mem_gb=512.0)
    t_auto, r = resolve_submission_target("auto", {"cpus": 12, "mem_gb": 128}, big)
    assert t_auto == "slurm" and "substantial" in r
    t_local, _ = resolve_submission_target("local", {"cpus": 12, "mem_gb": 128}, big)
    assert t_local == "inline"                  # explicit local bypasses the auto ceiling
    # a genuinely small job auto-inlines
    assert resolve_submission_target("auto", {"cpus": 2, "mem_gb": 6}, big)[0] == "inline"


def test_concurrency_guard_uses_free_cores():
    # IP3: cores already committed to running inline jobs are subtracted → a job that fits the
    # allocation but not what's FREE falls back to Slurm.
    cap = _cap(cores=8, mem_gb=32.0); cap["inline_used_cores"] = 6
    t, r = resolve_submission_target("local", {"cpus": 4, "mem_gb": 8}, cap)   # 4 > (8-6)=2 free
    assert t == "slurm" and "free" in r
    # fits within free cores → inline
    assert resolve_submission_target("local", {"cpus": 2, "mem_gb": 8}, cap)[0] == "inline"


def test_get_submitter_for_maps_target():
    assert get_submitter_for("inline").name == "local"
    assert get_submitter_for("slurm").name == "slurm"


def test_bg_submission_maps_execution_for_python_r():
    # IP2: the same knob generalizes to plain background jobs. Test env has no
    # ABA_BATCH_SUBMITTER → local submitter → local/auto inline; None/slurm → slurm.
    from core.jobs.runner import _bg_submission
    assert _bg_submission("local", {"cores": 2, "mem_gb": 4})[0] == "inline"
    assert _bg_submission("auto", None)[0] == "inline"
    assert _bg_submission(None, {"cores": 2})[0] == "slurm"      # unset → today's default (sbatch)
    assert _bg_submission("slurm", {"cores": 99})[0] == "slurm"


def test_submit_python_job_records_submission_target():
    # execution=local → inline (LocalSubmitter enqueue, no sbatch) + recorded in params.
    from core.jobs.runner import submit_python_job
    job = submit_python_job("print(1)", title="t", focus_entity_id=None,
                            execution="local", estimate={"cores": 2, "mem_gb": 4})
    assert (job.get("params") or {}).get("submission") == "inline"
    assert (job.get("params") or {}).get("execution") == "local"


def _with_default_submitter(name, fn):
    prev = os.environ.get("ABA_BATCH_SUBMITTER")
    os.environ["ABA_BATCH_SUBMITTER"] = name
    try:
        fn()
    finally:
        if prev is None:
            os.environ.pop("ABA_BATCH_SUBMITTER", None)
        else:
            os.environ["ABA_BATCH_SUBMITTER"] = prev


def test_cancel_routes_inline_to_local_even_on_slurm_deploy():
    # Regression: on a slurm-default deploy, an INLINE job's cancel must go to LocalSubmitter
    # (fire the CancelToken → killpg the head), NOT SlurmSubmitter.scancel — which would be a
    # no-op (the inline job has no slurm id) and leave the Nextflow head orphaned. cancel_job
    # routes via _submitter_for_job(job) on params.submission, not the deployment default.
    from core.jobs.runner import _submitter_for_job, LocalSubmitter
    from core.jobs.slurm_submitter import SlurmSubmitter

    def _check():
        assert isinstance(_submitter_for_job({"params": {"submission": "inline"}}), LocalSubmitter)
        assert isinstance(_submitter_for_job({"params": {"submission": "slurm"}}), SlurmSubmitter)
        # legacy row (pre-IP, no submission field) that reached Slurm → SlurmSubmitter
        assert isinstance(_submitter_for_job({"params": {"submitter": "slurm", "slurm_id": "42"}}),
                          SlurmSubmitter)
        # legacy row, no evidence at all → deployment default (slurm here)
        assert isinstance(_submitter_for_job({"params": {}}), SlurmSubmitter)
    _with_default_submitter("slurm", _check)


def test_cancel_routes_legacy_row_to_local_on_local_deploy():
    from core.jobs.runner import _submitter_for_job, LocalSubmitter
    # local-default deploy: a legacy row with no target resolves to LocalSubmitter (today's path)
    def _check():
        assert isinstance(_submitter_for_job({"params": {}}), LocalSubmitter)
    _with_default_submitter("local", _check)


def main() -> int:
    tests = [test_local_submitter_always_inline, test_login_node_forces_slurm,
             test_fits_runs_inline, test_exceeds_cores_falls_back_to_slurm,
             test_exceeds_mem_falls_back_to_slurm, test_no_heaviest_estimate_runs_inline,
             test_unknown_mem_capacity_ignored,
             test_auto_ceiling_fans_out_large_jobs_even_if_they_fit,
             test_concurrency_guard_uses_free_cores, test_get_submitter_for_maps_target,
             test_bg_submission_maps_execution_for_python_r,
             test_submit_python_job_records_submission_target,
             test_cancel_routes_inline_to_local_even_on_slurm_deploy,
             test_cancel_routes_legacy_row_to_local_on_local_deploy]
    failed = []
    for t in tests:
        try:
            t(); print(f"OK  {t.__name__}")
        except Exception as e:  # noqa: BLE001
            failed.append(t.__name__); print(f"FAIL {t.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
    print(f"\n{'all ' + str(len(tests)) + ' passed' if not failed else str(len(failed)) + ' failed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
