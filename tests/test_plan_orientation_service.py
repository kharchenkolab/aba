"""Plan-orientation content service (modularity_audit3 Item 1, Phase 2a).

guide used to compute the present_plan workspace-orientation block by lazily
importing content privates (`_prior_run_files_preamble`, `_run_scratch_cwd`,
`active_run_id`). It now asks through the `core/services` seam. These guards are
BEHAVIORAL, not structural: (1) importing the bio pack registers the service;
(2) the service threads the SAME arguments guide threaded inline — orientation
is behavior-preserving, not just relocated; (3) it stays best-effort ("" on
failure), matching guide's old try/except.
"""
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="aba_orient_")
os.environ.setdefault("ABA_DB_PATH", str(Path(_tmp) / "t.db"))
os.environ.setdefault("ABA_RUNTIME_DIR", _tmp)

from core import services  # noqa: E402


def test_importing_bio_pack_registers_the_service():
    services._SERVICES.pop("plan_orientation_preamble", None)
    import content.bio  # noqa: F401 — importing the pack must register bio's services
    assert services.get_service("plan_orientation_preamble") is not None, \
        "importing content.bio must register plan_orientation_preamble"


def test_service_threads_same_args_guide_threaded_inline(monkeypatch=None):
    """The refactor is behavior-preserving: the service must call
    _prior_run_files_preamble with current_run_id=active_run_id(tid) and
    cwd=_run_scratch_cwd(pid, tid) — exactly guide's old inline composition."""
    import content.bio  # noqa: F401 — ensure registered
    from content.bio.tools import run_exec
    from content.bio.lifecycle import runs

    calls = {}

    def fake_active_run_id(tid):
        calls["active_run_id"] = tid
        return "run_SENTINEL"

    def fake_scratch_cwd(pid, tid):
        calls["scratch_cwd"] = (pid, tid)
        return "/cwd/SENTINEL"

    def fake_preamble(pid, tid, *, current_run_id, cwd):
        calls["preamble"] = dict(pid=pid, tid=tid, current_run_id=current_run_id, cwd=cwd)
        return "ORIENT_BLOCK"

    orig = (runs.active_run_id, run_exec._run_scratch_cwd, run_exec._prior_run_files_preamble)
    runs.active_run_id = fake_active_run_id
    run_exec._run_scratch_cwd = fake_scratch_cwd
    run_exec._prior_run_files_preamble = fake_preamble
    try:
        out = services.call_service("plan_orientation_preamble", "prj_X", "thr_Y", default="")
    finally:
        runs.active_run_id, run_exec._run_scratch_cwd, run_exec._prior_run_files_preamble = orig

    assert out == "ORIENT_BLOCK"
    assert calls["active_run_id"] == "thr_Y"
    assert calls["scratch_cwd"] == ("prj_X", "thr_Y")
    assert calls["preamble"] == dict(pid="prj_X", tid="thr_Y",
                                     current_run_id="run_SENTINEL", cwd="/cwd/SENTINEL")


def test_service_is_best_effort_on_failure():
    """A raising provider must yield the default ("" as guide passes), never blow
    up the present_plan turn."""
    import content.bio  # noqa: F401
    from content.bio.lifecycle import runs
    orig = runs.active_run_id

    def boom(_tid):
        raise RuntimeError("orientation exploded")

    runs.active_run_id = boom
    try:
        out = services.call_service("plan_orientation_preamble", "p", "t", default="")
    finally:
        runs.active_run_id = orig
    assert out == "", "orientation must be best-effort — default on failure"


if __name__ == "__main__":
    for fn in [test_importing_bio_pack_registers_the_service,
               test_service_threads_same_args_guide_threaded_inline,
               test_service_is_best_effort_on_failure]:
        fn()
        print("PASS", fn.__name__)
    print("all passed")
