"""W2 (weft rewrite): the local background lane as bare weft tasks.

WeftSubmitter turns run_python(background=True) into a weft task running the
SAME node entry as the Slurm lane (core.jobs.slurm_entry), so result.json /
harvest / exec records stay identical — and the exec record gains the
weft-sourced `compute` block (job identity + placement, §4d).

Live end-to-end (real weft, bare task, no solve — fast) + selection/fallback
logic with the substrate stubbed offline.
"""
from __future__ import annotations
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_weftbg_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_PROJECTS_DIR"] = str(Path(_tmp) / "projects")
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")   # NOT the box's ~/.aba (slurm profile)
os.environ.pop("ABA_DB_PATH", None)
os.environ["ABA_BATCH_SUBMITTER"] = "local"
sys.path.insert(0, str(ROOT / "backend"))
pytestmark = pytest.mark.platform

from core import projects                                    # noqa: E402

projects.init()

# Static probe only (skipif evaluates at collection; configuring at import
# would race other modules — pytest imports every file before running any).
weft_ok = False
try:
    from core.compute import adapter as _ad
    weft_ok = _ad.resolve_pixi() is not None
except Exception:  # noqa: BLE001
    pass


@pytest.fixture(scope="module", autouse=True)
def _substrate():
    """(Re)configure the process-wide adapter for THIS module's workspace, and
    leave it torn down + offline for later modules (tests written against the
    in-process worker must not silently ride the weft lane)."""
    if weft_ok:
        _ad.shutdown()
        _ad._adapter = None
        st = _ad.configure()
        assert st["ok"], st["detail"]
    yield
    try:
        _ad.shutdown()
        _ad._status = {"ok": False, "severity": "info", "detail": "torn down by test"}
    except Exception:  # noqa: BLE001
        pass


# ── selection / fallback (no substrate needed) ───────────────────────────────

def test_offline_substrate_falls_back_to_worker(monkeypatch):
    import core.compute.adapter as ad
    from core.jobs.submitter import get_submitter_for
    monkeypatch.setattr(ad, "_status", {"ok": False, "severity": "warning",
                                        "detail": "down"})
    sub = get_submitter_for("inline")
    assert type(sub).__name__ == "LocalSubmitter"


def test_worker_escape_hatch(monkeypatch):
    monkeypatch.setenv("ABA_BATCH_SUBMITTER", "worker")
    from core.jobs.submitter import get_submitter
    assert type(get_submitter()).__name__ == "LocalSubmitter"


@pytest.mark.skipif(not weft_ok, reason="weft substrate unavailable")
def test_online_substrate_selects_weft():
    from core.jobs.submitter import get_submitter_for
    assert type(get_submitter_for("inline")).__name__ == "WeftSubmitter"


def test_cancel_routes_by_hard_evidence(monkeypatch):
    from core.jobs.runner import _submitter_for_job
    j = {"params": {"submitter": "weft", "weft_id": "jb_x", "submission": "inline"}}
    assert type(_submitter_for_job(j)).__name__ == "WeftSubmitter"
    import core.compute.adapter as ad
    monkeypatch.setattr(ad, "_status", {"ok": False, "severity": "warning",
                                        "detail": "down"})
    j2 = {"params": {"submission": "inline"}}   # ran in-process, no weft marker
    assert type(_submitter_for_job(j2)).__name__ == "LocalSubmitter"


def test_reconcile_leaves_weft_jobs_alone():
    from core.jobs.runner import _is_slurm_params
    assert _is_slurm_params(json.dumps({"submitter": "weft"})) is True
    assert _is_slurm_params(json.dumps({"submitter": "slurm"})) is True
    assert _is_slurm_params(json.dumps({"submission": "inline"})) is False


@pytest.mark.skipif(not weft_ok, reason="weft substrate unavailable")
def test_slurm_site_maps_estimate_to_weft_resources(monkeypatch):
    """The weft SLURM lane maps a job estimate → the weft task's `resources`
    (cpus/mem_gb/gpus, plus a walltime ceiling for a SIZED job — the
    sized-only rule; weft's placement then picks the partition). This is the
    coverage that replaces SlurmSubmitter's sbatch resource directives, so
    it's pinned here."""
    import core.jobs.weft_submitter as ws
    import core.graph.jobs as gjobs
    pid = projects.create_project("wslurmres")["id"]
    projects.set_current(pid)
    captured = {}

    class _Cap:
        def sync_call(self, name, task, *a, **k):
            captured["task"] = task
            return {"job_id": "jb_res"}
    monkeypatch.setattr(ws, "_adapter", lambda: _Cap())
    monkeypatch.setattr(gjobs, "update_job", lambda *a, **k: None)
    # this test pins the SHARED-FS lane's mapping; 'hpc' is not in the test
    # deployment's yaml, and an undeclared site now routes DETACHED
    monkeypatch.setattr(ws, "site_contract", lambda s: "shared-fs")

    job = {"id": "job_res1", "kind": "run_python", "title": "res map",
           "params": {"code": "print(1)", "env": "x",   # env set → skip default-env resolution
                      "run_id": "run_res1", "project_id": pid,
                      "estimate": {"cores": 4, "mem_gb": 16, "gpu": True,
                                   "runtime_min": 30},   # SIZED → asks walltime
                      "timeout_s": 3600}}
    # scheduler site → cpus/mem/gpus + the sized job's walltime ceiling
    ws.WeftSubmitter(site="hpc").submit(job)
    r = captured["task"]["resources"]
    assert r["cpus"] == 4 and r["mem_gb"] == 16 and r["gpus"] == 1, r
    assert r.get("walltime") == "01:05:00", r          # 3600s + 300 grace
    assert captured["task"]["site"] == "hpc"
    # local site → same cpu/mem/gpu but NO walltime (no scheduler to bound it)
    captured.clear()
    ws.WeftSubmitter(site="local").submit(job)
    assert captured["task"]["resources"].get("cpus") == 4
    assert "walltime" not in captured["task"]["resources"], captured["task"]["resources"]


# ── live end-to-end (bare task; needs weft+pixi, no solve → fast) ────────────

@pytest.mark.skipif(not weft_ok, reason="weft substrate unavailable")
def test_background_job_runs_as_weft_task_end_to_end():
    from core.graph.jobs import get_job
    from core.jobs.submit import submit_python_job
    from core.jobs.weft_submitter import WeftSubmitter
    pid = projects.create_project("weftbg")["id"]
    projects.set_current(pid)
    code = "print('WEFT_BG_OK'); open('bg_out.csv','w').write('a\\n1\\n')"
    job = submit_python_job(code, "weft bg smoke", None, project_id=pid,
                            thread_id="t1")
    row = get_job(job["id"], project_id=pid)
    assert (row["params"] or {}).get("submitter") == "weft", row["params"]
    assert row["params"].get("weft_id", "").startswith("jb_")

    sub = WeftSubmitter()
    result = None
    deadline = time.time() + 180
    while time.time() < deadline:
        row = get_job(job["id"], project_id=pid)
        row["project_id"] = pid
        result = sub.poll(row)
        if result is not None:
            break
        time.sleep(1)
    assert result is not None, "weft task did not terminate in time"
    assert result.get("returncode") == 0, result
    assert "WEFT_BG_OK" in (result.get("stdout") or "")
    # the entry harvested from the run cwd (shim intact)
    assert any("bg_out" in str(f) for f in
               (result.get("files") or []) + (result.get("tables") or [])), result
    # §4d: the weft-sourced compute block rides the result into the exec record
    comp = result.get("compute")
    assert comp and comp["substrate"] == "weft" and comp["job_id"] == \
        row["params"]["weft_id"]
    assert comp.get("node"), comp
    assert (comp.get("placement") or {}).get("node"), comp


@pytest.mark.skipif(not weft_ok, reason="weft substrate unavailable")
def test_finalize_writes_compute_block_into_exec_record():
    from core.jobs.runner import _write_exec_record_for_job
    from core.graph import exec_records as er
    pid = projects.create_project("weftrec")["id"]
    projects.set_current(pid)
    from core.exec.run import run_python_code
    res = run_python_code("print('x')", project_id=pid, run_id="rw1", timeout_s=60)
    assert res.get("returncode") == 0
    res["compute"] = {"substrate": "weft", "job_id": "jb_test",
                      "placement": {"site": "local", "node": "testnode"}}
    job = {"id": "job_w", "kind": "run_python", "focus_entity_id": None,
           "params": {"code": "print('x')", "thread_id": "t1", "run_id": "rw1",
                      "project_id": pid}}
    _write_exec_record_for_job(job, res, pid, pid)
    rec = er.get(res["exec_id"])
    assert rec["compute"]["placement"]["node"] == "testnode"
    assert rec["compute"]["substrate"] == "weft"
