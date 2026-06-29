"""Gap 1 (Run attribution): a background job must execute UNDER the Run captured
at submit (active_run_id), so its outputs land in the Run's work dir + attach to
it — not in a job-scoped dir the agent then has to re-render. And the output
manifest refresh on job-completion must target THAT Run, even if the active Run
has since changed.
"""
import os
import sys
import tempfile

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_attr_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


def test_job_paths_execute_under_captured_run():
    """Both the Slurm and local paths resolve the exec run_id as
    `params.run_id or job_id` — the Run first, the job id only as fallback."""
    import core.jobs.slurm_submitter as S
    import core.jobs.runner as R
    import core.jobs.slurm_entry as E
    s_src = open(S.__file__).read()
    r_src = open(R.__file__).read()
    e_src = open(E.__file__).read()
    assert 'params.get("run_id") or job["id"]' in s_src      # Slurm spec
    assert 'params.get("run_id") or job_id' in r_src         # local worker
    # live job.log: unbuffered python + slurm_entry streaming
    assert "-u -m core.jobs.slurm_entry" in s_src
    assert "stream=True" in e_src


def test_sentinel_repolls_when_result_json_lags(tmp_path):
    """`done` present but result.json not yet visible (NFS lag) on a clean exit
    must RE-POLL (return None), not return the empty fallback that would drop the
    job's stdout + artifacts."""
    import os
    import time
    from core.jobs.slurm_submitter import SlurmSubmitter
    sub = SlurmSubmitter()

    (tmp_path / "done").write_text("0\n")              # clean exit, result.json lagging
    assert sub._result_from_sentinel(tmp_path) is None   # re-poll, don't discard

    (tmp_path / "result.json").write_text('{"returncode":0,"plots":[{"original_name":"x.png"}]}')
    r = sub._result_from_sentinel(tmp_path)
    assert r and r.get("plots"), r                       # full result once visible

    # a real failure (rc!=0, no result.json) returns the error fallback immediately
    j2 = tmp_path / "j2"; j2.mkdir(); (j2 / "done").write_text("1\n")
    r2 = sub._result_from_sentinel(j2)
    assert r2 and "error" in r2, r2

    # overdue (done long ago) + still no result.json → give up with the fallback
    j3 = tmp_path / "j3"; j3.mkdir(); (j3 / "done").write_text("0\n")
    os.utime(j3 / "done", (time.time() - 1000, time.time() - 1000))
    assert sub._result_from_sentinel(j3) == {"returncode": 0, "stdout": "", "stderr": ""}


def test_manifest_refresh_targets_captured_run_not_active():
    """register_artifacts_from_tool_result must refresh the Run from analysis_ctx
    (the captured Run), preferring it over the thread's currently-open Run."""
    import content.bio.lifecycle.runs as runs
    from content.bio.lifecycle import registry

    seen = {}
    saved = {k: getattr(runs, k) for k in ("active_run_id", "refresh_output_manifest", "append_run_code")}
    runs.active_run_id = lambda tid: "ana_DIFFERENT_now_open"
    runs.refresh_output_manifest = lambda rid, **kw: seen.__setitem__("refresh", rid)
    runs.append_run_code = lambda rid, code: seen.__setitem__("append", rid)
    try:
        registry.register_artifacts_from_tool_result(
            tool_name="run_r",
            tool_input={"code": "ggsave('u.png')"},
            result_obj={"plots": [{"original_name": "u.png", "url": "/artifacts/p/u.png"}],
                        "execution_mode": "stateless_r"},
            focused_entity_id=None,
            analysis_ctx={"analysis_id": "ana_CAPTURED"},
            thread_id="thr_test",
        )
    finally:
        for k, v in saved.items():
            setattr(runs, k, v)
    assert seen.get("refresh") == "ana_CAPTURED", seen
    assert seen.get("append") == "ana_CAPTURED", seen
