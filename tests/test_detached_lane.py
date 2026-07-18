"""Detached compute lane — fast behavioral tests (misc/detached_compute.md S1).

The transport contract, mocked at the adapter/retention seams:
  * submit ships code AS DATA (payload ref), carries NO controller paths and
    NO ABA_* env; the spec carries the job-id memo nonce
  * env platform mismatch triggers ONE lazy re-lock and a retry
  * poll fetches result.json + small outputs over the data plane and grades
    env-less runs honestly
  * site validation names the real sites

Run: python tests/test_detached_lane.py   (or via pytest)
"""
from __future__ import annotations
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

_RT = tempfile.mkdtemp(prefix="aba_det_")
os.environ.setdefault("ABA_RUNTIME_DIR", _RT)
os.environ.setdefault("ABA_DB_PATH", os.path.join(_RT, "d.db"))
os.environ.setdefault("ABA_WORK_DIR", os.path.join(_RT, "work"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.graph._schema import init_db  # noqa: E402
init_db()
# conftest's import chain resolves the config registry before this module can
# set ABA_DB_PATH, leaving SINGLE undetected — force single-DB routing so job
# rows land in the one initialized DB (the deployment-equivalent behavior).
import core.projects as _projects  # noqa: E402
_projects.SINGLE = True
from core.graph.jobs import create_job, get_job  # noqa: E402
import core.jobs.weft_submitter as ws  # noqa: E402
import core.compute.retention as retmod  # noqa: E402
from core.compute.errors import ComputeError  # noqa: E402

_SITES = [{"name": "far", "kind": "ssh", "config": {"host": "far.example"}},
          {"name": "hpc", "kind": "slurm",
           "config": {"host": "127.0.0.1", "port": 22}}]


class _FakeComp:
    def __init__(self, fail_platform_once: bool = False):
        self.calls = []
        self._fail = fail_platform_once
        self.registered: dict[str, str] = {}

    def sync_call(self, name, *a, **kw):
        self.calls.append((name, a, kw))
        if name == "sites_list":
            return _SITES
        if name == "data_register":
            # snapshot at register time — the submitter deletes the staging
            # dir right after (it must never linger as a Run "output")
            self.registered = {p.name: p.read_text()
                               for p in Path(a[0]).iterdir()}
            return {"ref": "dref:payload123"}
        if name == "task_submit":
            if self._fail:
                self._fail = False
                raise ComputeError("env.platform_mismatch",
                                   "env is locked for ['osx-arm64'] but site far is linux-aarch64")
            return {"job_id": "wj_1"}
        if name == "task_status":
            return [{"state": "DONE"}]
        if name == "task_result":
            return {"node": "far-node", "env_id": None, "wall_s": 1}
        if name == "task_cancel":
            return {"ok": True}
        if name == "provenance":
            return {}
        raise AssertionError(f"unexpected call {name}")

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


def _job(jid="job_det1", site="far", env=None):
    return create_job(job_id=jid, kind="run_python", title="t",
                      focus_entity_id=None, project_id="default",
                      params={"code": "print('x')", "timeout_s": 60,
                              "project_id": "default", "run_id": None,
                              "estimate": {"cores": 2}, "env": env,
                              "site": site})


def test_detached_submit_ships_code_as_data(monkeypatch):
    comp = _FakeComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    job = _job("job_det_a")
    ws.WeftSubmitter(site="far").submit(job)
    (name, a, kw), = [c for c in comp.calls if c[0] == "task_submit"]
    task = a[0]
    assert task["command"] == "python3 payload/aba_entry.py"
    assert sys.executable not in task["command"]          # no controller paths
    assert "env_vars" not in task                          # no ABA_*/PYTHONPATH
    assert task["inputs"] == [{"ref": "dref:payload123", "mount_as": "payload"}]
    assert task["resources"]["cpus"] == 2
    assert "walltime" not in task["resources"]             # ssh site: no scheduler ask
    # payload traveled AS DATA (snapshotted by the fake at register time):
    # harness + code + nonce + timeout ceiling
    assert "aba_entry.py" in comp.registered
    assert comp.registered["user_code.py"] == "print('x')"
    spec = json.loads(comp.registered["spec.json"])
    assert spec["job_id"] == "job_det_a" and spec["interpreter"] == "python3"
    assert spec["timeout_s"] == 60      # enforced by the harness on the node
    # …and the staging dir is GONE: it lives inside the run dir, which the
    # harvest sweep (*.json) and the Files panel read — spec.json leaked as a
    # spurious Run output when it lingered (review D1)
    sub = ws.WeftSubmitter(site="far")
    assert not (sub._run_dir(job) / "payload").exists()
    # params updated: detached + honest env grade (no env resolvable here)
    row = get_job("job_det_a", project_id="default")
    p = row["params"]
    assert p["detached"] is True and p["weft_id"] == "wj_1"
    assert p.get("env_grade") == "node-system"


def test_harness_enforces_timeout(_mp=None):
    """The node-side harness kills the script at spec.timeout_s and writes an
    error result — the ONLY wall enforcement on ssh-kind sites (no scheduler
    walltime there): without it a runaway background job runs forever."""
    import shutil as _sh
    import subprocess as _sp
    import time as _t
    wd = Path(tempfile.mkdtemp(prefix="aba_harness_"))
    payload = wd / "payload"
    payload.mkdir()
    _sh.copyfile(Path(_BACKEND) / "core" / "jobs" / "detached_entry.py",
                 payload / "aba_entry.py")
    (payload / "user_code.py").write_text(
        "import time\nprint('started', flush=True)\ntime.sleep(60)\n")
    (payload / "spec.json").write_text(json.dumps(
        {"interpreter": "python3", "script": "user_code.py",
         "job_id": "job_hto", "timeout_s": 1}))
    t0 = _t.time()
    p = _sp.run([sys.executable, "payload/aba_entry.py"], cwd=wd,
                capture_output=True, text=True, timeout=30)
    assert _t.time() - t0 < 20                      # killed at ~1s, not 60
    res = json.loads((wd / "result.json").read_text())
    assert res["status"] == "error" and p.returncode == 1
    assert "timed out after 1s" in res["error"] and res["returncode"] == 124
    assert "started" in res["stdout_tail"]          # partial output preserved


def test_detached_slurm_walltime_only_when_sized(monkeypatch):
    """A SIZED job (agent gave an estimate) asks an explicit walltime; an
    unsized one must NOT — an inflated default pends forever on sites whose
    partition cap is below it (PartitionTimeLimit, verified live)."""
    comp = _FakeComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    ws.WeftSubmitter(site="hpc").submit(_job("job_det_w", site="hpc"))
    task = [c for c in comp.calls if c[0] == "task_submit"][0][1][0]
    assert "walltime" not in task["resources"]          # unsized → site default
    comp2 = _FakeComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp2)
    job = create_job(job_id="job_det_w2", kind="run_python", title="t",
                     focus_entity_id=None, project_id="default",
                     params={"code": "x=1", "timeout_s": 600,
                             "project_id": "default",
                             "estimate": {"cores": 1, "runtime_min": 5},
                             "site": "hpc"})
    ws.WeftSubmitter(site="hpc").submit(job)
    task2 = [c for c in comp2.calls if c[0] == "task_submit"][0][1][0]
    assert task2["resources"]["walltime"] == "00:15:00"   # 600s + 300 grace


def test_platform_mismatch_relocks_once_and_retries(monkeypatch):
    comp = _FakeComp(fail_platform_once=True)
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    import core.compute.named_envs as ne
    relocks = []
    monkeypatch.setattr(ne, "resolve",
                        lambda pid, name: {"env_id": "env:old", "language": "python",
                                           "packages": ["click"]})
    monkeypatch.setattr(ne, "ensure_platform",
                        lambda pid, name, plat: relocks.append((name, plat))
                        or {"env_id": "env:new"})
    ws.WeftSubmitter(site="far").submit(_job("job_det_p", env="myenv"))
    assert relocks == [("myenv", "linux-aarch64")]
    submits = [c for c in comp.calls if c[0] == "task_submit"]
    assert len(submits) == 2
    assert submits[1][1][0]["env"] == "env:new"
    assert get_job("job_det_p", project_id="default")["params"]["env_id"] == "env:new"


def test_detached_poll_fetches_results_over_data_plane(monkeypatch):
    comp = _FakeComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    node_result = {"status": "ok", "returncode": 0, "stdout_tail": "final 42",
                   "outputs": ["out/answer.csv"], "runtime": "Python 3.11"}

    def _fread(target, rel, max_bytes=1 << 20):
        if rel == "result.json":
            data = json.dumps(node_result).encode()
        elif rel == "out/answer.csv":
            data = b"k,v\nanswer,42\n"
        else:
            raise RuntimeError("no such file")
        return {"bytes_b64": base64.b64encode(data).decode(), "truncated": False}
    monkeypatch.setattr(retmod, "file_read", _fread)
    monkeypatch.setattr(retmod, "file_stat",
                        lambda t, rel: {"exists": True, "bytes": 14})
    import core.exec.run as execrun
    monkeypatch.setattr(execrun, "harvest_artifacts",
                        lambda *a, **k: ([], [{"name": "answer.csv"}], [], []))
    job = create_job(job_id="job_det_r", kind="run_python", title="t",
                     focus_entity_id=None, project_id="default",
                     params={"code": "", "project_id": "default",
                             "detached": True, "weft_id": "wj_1"})
    res = ws.WeftSubmitter(site="far").poll(job)
    assert res["status"] == "ok" and res["returncode"] == 0
    assert res["stdout"] == "final 42"
    assert res["tables"] == [{"name": "answer.csv"}]
    local = ws.WeftSubmitter(site="far")._run_dir(job) / "out/answer.csv"
    assert local.read_bytes() == b"k,v\nanswer,42\n"
    assert res["compute"]["env_grade"] == "node-system"
    assert res["compute"]["runtime"] == "Python 3.11"


def test_poll_side_platform_relock_resubmits(monkeypatch):
    """This weft surfaces env.platform_mismatch at REALIZE (async): the poll
    must re-lock the named env for the site's platform, resubmit the task
    transparently (poll returns None → keep polling), and do it ONCE."""

    class _MismatchComp(_FakeComp):
        def sync_call(self, name, *a, **kw):
            if name == "task_status":
                self.calls.append((name, a, kw))
                return [{"state": "FAILED", "error": {
                    "error": "env.platform_mismatch",
                    "detail": "env is locked for ['osx-arm64'] but site far is linux-aarch64",
                    "hints": {"site_platform": "linux-aarch64"}}}]
            return super().sync_call(name, *a, **kw)
    comp = _MismatchComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    import core.compute.named_envs as ne
    relocks = []
    monkeypatch.setattr(ne, "ensure_platform",
                        lambda pid, name, plat: relocks.append((name, plat))
                        or {"env_id": "env:relocked"})
    job = create_job(job_id="job_det_pl", kind="run_python", title="t",
                     focus_entity_id=None, project_id="default",
                     params={"code": "x=1", "project_id": "default",
                             "detached": True, "weft_id": "wj_old",
                             "weft_site": "far",
                             "env": "myenv", "estimate": {}})
    # the REAL poll loop uses a generic WeftSubmitter() (site='local') — the
    # resubmit must go to the JOB's recorded site, never self's (a re-locked
    # job once bounced to 'local' this way — found live)
    out = ws.WeftSubmitter().poll(get_job("job_det_pl", project_id="default"))
    assert out is None                                  # transparent resubmit
    assert relocks == [("myenv", "linux-aarch64")]
    resub = [c for c in comp.calls if c[0] == "task_submit"][-1][1][0]
    assert resub["site"] == "far"
    p = get_job("job_det_pl", project_id="default")["params"]
    assert p["platform_relocked"] is True and p["weft_id"] == "wj_1"
    assert p["env_id"] == "env:relocked"
    # second mismatch (relock already spent) → hard failure, named cause
    out2 = ws.WeftSubmitter().poll(get_job("job_det_pl", project_id="default"))
    assert out2 is not None and "platform" in out2["error"]


def test_poll_relock_covers_default_env_via_base_pack(monkeypatch):
    """The DEFAULT project env (pack snapshot) must re-lock too — the study
    found every default-env job dying on env.platform_mismatch with no
    recovery. The base pack's spec re-solves with the site platform, and the
    job records that session extras don't travel."""

    class _MismatchComp(_FakeComp):
        def sync_call(self, name, *a, **kw):
            if name == "task_status":
                self.calls.append((name, a, kw))
                return [{"state": "FAILED", "error": {
                    "error": "env.platform_mismatch",
                    "detail": "env is locked for ['linux-64'] but site far is linux-aarch64",
                    "hints": {"site_platform": "linux-aarch64"}}}]
            return super().sync_call(name, *a, **kw)
    comp = _MismatchComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    import core.compute.base_env as be
    relocks = []
    monkeypatch.setattr(be, "ensure_platform",
                        lambda lang, plat: relocks.append((lang, plat))
                        or {"env_id": "env:packrelock", "platforms": [plat]})
    job = create_job(job_id="job_det_dp", kind="run_python", title="t",
                     focus_entity_id=None, project_id="default",
                     params={"code": "x=1", "project_id": "default",
                             "detached": True, "weft_id": "wj_old",
                             "weft_site": "far",
                             "env": None, "env_id": "env:snapshot",
                             "estimate": {}})
    out = ws.WeftSubmitter().poll(get_job("job_det_dp", project_id="default"))
    assert out is None and relocks == [("python", "linux-aarch64")]
    p = get_job("job_det_dp", project_id="default")["params"]
    assert p["env_id"] == "env:packrelock" and p["platform_relocked"] is True
    assert "extras" in p.get("env_note", "")


def test_active_weft_jobs_seen_in_single_db_mode():
    """SINGLE-DB mode: the poll loop must see weft jobs in the ONE workspace
    DB — the study found jobs never finalizing (agent watched a terminal
    task as 'queued' forever) because only PROJECTS_DIR/*.db was scanned."""
    create_job(job_id="job_single_scan", kind="run_python", title="t",
               focus_entity_id=None, project_id="default",
               params={"code": "", "submitter": "weft", "weft_id": "wj_s"})
    from core.jobs.runner import _active_weft_jobs
    jobs = {j["id"] for j in _active_weft_jobs()}
    assert "job_single_scan" in jobs


def test_sync_remote_runs_in_tool(monkeypatch):
    """site= WITHOUT background: synchronous — submit, wait in-tool, return a
    NORMAL tool result (placement is orthogonal to duration; the polling
    pathology the live study exposed does not arise: there is nothing to
    poll). The job row is marked `sync` so the weft poll loop leaves it."""
    comp = _FakeComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    node_result = {"status": "ok", "returncode": 0, "stdout_tail": "S 42",
                   "outputs": [], "runtime": "Python 3.10"}

    def _fread(target, rel, max_bytes=1 << 20):
        if rel == "result.json":
            return {"bytes_b64": base64.b64encode(
                json.dumps(node_result).encode()).decode(), "truncated": False}
        raise RuntimeError("no such file")
    monkeypatch.setattr(retmod, "file_read", _fread)
    from content.bio.tools.run_exec import _run_remote_sync
    out = _run_remote_sync({"code": "print('S', 42)", "site": "far",
                            "timeout_s": 60},
                           {"thread_id": "t"}, "default", "t", "run_python")
    assert out["status"] == "ok" and out["stdout"] == "S 42"
    assert out["execution_mode"] == "remote-sync" and "far" in out["note"]
    from core.jobs.runner import _active_weft_jobs
    # terminal + sync → invisible to the poll loop
    assert all("sync" not in (j.get("params") or {}) for j in _active_weft_jobs())


def test_sync_remote_writes_exec_record_so_output_is_pinnable(monkeypatch):
    """The live study found a sync-remote FIGURE couldn't be pinned to a
    Result: no exec record → no artifact_id → nothing to pin (the agent
    visibly flailed). The sync path must write an exec record and inject
    `exec_id` (what pin_cell reads), carrying the placement block for
    provenance."""
    comp = _FakeComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    # the node produced a figure; poll harvests it into plots
    node_result = {"status": "ok", "returncode": 0, "stdout_tail": "made a plot",
                   "outputs": ["fig.png"], "runtime": "Python 3.10"}

    def _fread(target, rel, max_bytes=1 << 20):
        if rel == "result.json":
            data = json.dumps(node_result).encode()
        elif rel == "fig.png":
            data = b"\x89PNG\r\n\x1a\n" + b"0" * 64
        else:
            raise RuntimeError("no such file")
        return {"bytes_b64": base64.b64encode(data).decode(), "truncated": False}
    monkeypatch.setattr(retmod, "file_read", _fread)
    monkeypatch.setattr(retmod, "file_stat", lambda t, rel: {"exists": True, "bytes": 72})
    import core.exec.run as execrun
    monkeypatch.setattr(execrun, "harvest_artifacts",
                        lambda *a, **k: ([{"url": "/artifacts/single/fig.png",
                                           "original_name": "fig.png"}], [], [], []))
    from content.bio.tools.run_exec import _run_remote_sync
    out = _run_remote_sync({"code": "plot()", "site": "far", "timeout_s": 60},
                           {"thread_id": "t"}, "default", "t", "run_python")
    assert out["status"] == "ok"
    # the pin path (pin_cell) keys on result["exec_id"] — must be present
    assert out.get("exec_id"), "no exec_id → figure not pinnable (the study bug)"
    # and the exec record carries the placement block "ran on <site>"
    from core.graph.exec_records import get as _get_exec
    rec = _get_exec(out["exec_id"]) or {}
    comp_block = rec.get("compute") or {}
    assert comp_block  # placement provenance recorded


def test_sync_substrate_cancel_not_reported_as_success(monkeypatch):
    """A task cancelled ON THE SUBSTRATE returns {status: cancelled} with no
    error/returncode — the sync loop must NOT read that as success (review
    Defect 2)."""
    class _CancelComp(_FakeComp):
        def sync_call(self, name, *a, **kw):
            if name == "task_status":
                return [{"state": "CANCELLED"}]
            return super().sync_call(name, *a, **kw)
    comp = _CancelComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    from content.bio.tools.run_exec import _run_remote_sync
    out = _run_remote_sync({"code": "x=1", "site": "far", "timeout_s": 30},
                           {"thread_id": "t"}, "default", "t", "run_python")
    assert out["status"] == "cancelled" and "cancelled" in out["note"].lower()


def test_sync_cancel_uses_fresh_row_with_weft_id(monkeypatch):
    """Token-cancel must call sub.cancel on a row carrying weft_id — the stale
    submit-return dict has none, so cancelling it orphans the remote task
    (review Defect 1). We assert task_cancel actually reaches the substrate."""
    class _SlowComp(_FakeComp):
        def sync_call(self, name, *a, **kw):
            if name == "task_status":
                return [{"state": "RUNNING"}]      # never terminal → loop spins
            return super().sync_call(name, *a, **kw)
    comp = _SlowComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)

    class _Tok:
        cancelled = True                            # already cancelled on entry
    from content.bio.tools.run_exec import _run_remote_sync
    out = _run_remote_sync({"code": "x=1", "site": "far", "timeout_s": 30},
                           {"thread_id": "t", "cancel_token": _Tok()},
                           "default", "t", "run_python")
    assert out["status"] == "cancelled"
    # task_cancel reached the substrate with the real weft id (not a no-op)
    cancels = [c for c in comp.calls if c[0] == "task_cancel"]
    assert cancels and cancels[0][1][0] == "wj_1"


def test_site_validation_names_real_sites(monkeypatch):
    comp = _FakeComp()
    monkeypatch.setattr(ws, "_adapter", lambda: comp)
    from core.jobs.submit import _site_submitter
    try:
        _site_submitter("nowhere")
        raise AssertionError("expected ValueError")
    except ValueError as e:
        assert "far" in str(e) and "hpc" in str(e)


def _standalone() -> int:
    import traceback

    class _MP:
        def __init__(self): self._u = []
        def setattr(self, t, n, v):
            self._u.append((t, n, getattr(t, n))); setattr(t, n, v)
        def undo(self):
            for t, n, o in reversed(self._u):
                setattr(t, n, o)
            self._u.clear()

    rc = 0
    for t in (test_detached_submit_ships_code_as_data,
              test_harness_enforces_timeout,
              test_detached_slurm_walltime_only_when_sized,
              test_platform_mismatch_relocks_once_and_retries,
              test_detached_poll_fetches_results_over_data_plane,
              test_poll_side_platform_relock_resubmits,
              test_poll_relock_covers_default_env_via_base_pack,
              test_active_weft_jobs_seen_in_single_db_mode,
              test_sync_remote_runs_in_tool,
              test_sync_remote_writes_exec_record_so_output_is_pinnable,
              test_sync_substrate_cancel_not_reported_as_success,
              test_sync_cancel_uses_fresh_row_with_weft_id,
              test_site_validation_names_real_sites):
        mp = _MP()
        try:
            t(mp)
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}")
            rc = 1
        finally:
            mp.undo()
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
