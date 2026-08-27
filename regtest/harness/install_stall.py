"""Does an install in one thread stall a trivial call in another?

THE ASYMMETRIC SHAPE. `regtest/live/workflows.py --concurrent N` runs N lanes
doing the SAME short work, so it measures overlap between peers and cannot see
the case users actually hit: one thread installs a package for two minutes
while another wants to run three lines of Python. Live 2026-08-27: a fetch
script recorded 128.9 s of which 0.587 s was execution, finishing 1.8 s after a
concurrent `scrublet` install ended.

WHY IT MUST RUN INSIDE THE IMAGE. The install verb ABA uses
(`project_env.ensure_ranked` → weft `ensure_available`) exists only in the
PINNED weft. A developer checkout with an older weft silently falls back to a
different code path, so probes there exercise code production does not run and
report "not reproduced". Run this with `apptainer exec` against the staged SIF.

WHAT IT MEASURES. Thread B walks the real `run_python` prologue one phase at a
time — `base_env.require`, `project_env.ensure`, `KernelPool.get_or_start`,
`session.execute` — so the answer is WHICH phase blocked, not merely that
something did.

  apptainer exec --containall \\
    --bind $SHARE --bind $WS --bind $ENVS \\
    --env ABA_HOME=$WS --env ABA_SITE_CONFIG=$SHARE/site.yaml \\
    --env ABA_WEFT_PUBLISH_TREE=$ENVS \\
    $SIF /opt/aba-venv/bin/python /opt/aba/regtest/harness/install_stall.py
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time

# The package thread A installs. Small, pure-python, and not in the base pack —
# the point is a REAL install taking tens of seconds, not a heavy one.
INSTALL_PKGS = os.environ.get("STALL_PKGS", "soupsieve,inflection").split(",")
# How long into the install thread B starts asking for a kernel.
B_DELAY_S = float(os.environ.get("STALL_B_DELAY", "5"))
# A trivial call blocked longer than this is the defect, not scheduling.
STALL_S = float(os.environ.get("STALL_BUDGET", "10"))


def _phase(out: dict, name: str, fn):
    t = time.time()
    try:
        fn()
        out[name] = round(time.time() - t, 2)
    except Exception as e:  # noqa: BLE001 — a failing phase is a RESULT
        out[name] = round(time.time() - t, 2)
        out.setdefault("errors", {})[name] = f"{type(e).__name__}: {str(e)[:120]}"


def run(pid: str | None = None) -> dict:
    n = INSTALL_PKGS[0]
    # A REAL project. The install tool writes capability entities into the
    # graph, so a bare ABA_HOME with no project fails with "no such table:
    # entities" long before it can contend with anything — and the probe would
    # have reported "independent" off a run in which nothing installed.
    from core import projects as _proj
    if pid is None:
        pid = str(_proj.create_project("stall-probe")["id"])
    _proj.set_current(pid)

    from core.compute import adapter as _ad
    _ad.configure()
    from core.compute import base_env, project_env
    from core.data.workspace import scratch_dir
    from core.exec.kernels import get_pool

    # Warm the project env + one kernel BEFORE the install, so the probe
    # measures contention rather than first-use cost.
    project_env.ensure(pid, "python")
    warm = get_pool().get_or_start("warm", "python",
                                   cwd=str(scratch_dir(pid, "warm")))

    res: dict = {"pkgs": INSTALL_PKGS, "b_delay_s": B_DELAY_S, "project": pid}
    marks: dict = {}

    def installer():
        # The REAL tool, not ensure_ranked directly. Which lanes a package
        # goes through is ABA's decision (cap_request → classify_language →
        # ranked chain); guessing `lanes=["pypi"]` here made the probe fail
        # with "no ranked lane could provide" for a package the product
        # installs fine. Test the request the user makes, not a reconstruction
        # of it.
        t = time.time()
        try:
            from content.bio.tools.discovery import (ensure_capability,
                                                     propose_capability_tool)
            _proj.set_current(pid)      # the worker thread needs the binding
            _ctx = {"thread_id": "thr_installer", "project_id": pid}
            out = ensure_capability({"name": n}, _ctx)
            if out.get("status") == "candidates":
                # The tool's own two-step path (its note says so): on a
                # deployment whose catalog lacks the name, an agent proposes
                # the library and re-ensures. Walking it here keeps the probe
                # equal to the request a user's agent makes — without it, a
                # catalog-less bench measures NOTHING and the armed
                # precondition (below) correctly refuses the run.
                res["proposed"] = (propose_capability_tool({"name": n})
                                   or {}).get("status")
                out = ensure_capability({"name": n}, _ctx)
            res["install_result"] = {k: out.get(k) for k in
                                     ("status", "ready", "error", "note")
                                     if k in out}
            res["install_ok"] = out.get("status") != "error" and not out.get("error")
        except Exception as e:  # noqa: BLE001
            res["install_ok"] = False
            res["install_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        marks["install_s"] = round(time.time() - t, 2)

    install_done = threading.Event()

    def installer_wrapped():
        try:
            installer()
        finally:
            install_done.set()

    def trivial():
        # Walk the phases in a LOOP until the install finishes. A single walk
        # at a fixed delay measured NOTHING twice over: the install tool's
        # preamble (classify → propose → catalog) runs for seconds before the
        # weft verb opens its window, so one early walk completes before there
        # is anything to contend with and reads as "independent" — exactly the
        # false exoneration this probe exists to prevent (live 2026-08-27: the
        # single walk said independent while the same trivial call through the
        # server blocked 93 s body-time beside a 92 s install). Per-iteration
        # timings are kept, so the answer is WHICH phase blocked and WHEN.
        _proj.set_current(pid)
        time.sleep(B_DELAY_S)
        iters: list = []
        i = 0
        while not install_done.is_set() and i < 60:
            i += 1
            b: dict = {"at_s": round(time.time() - t_start, 1)}
            t0 = time.time()
            _phase(b, "base_env.require", lambda: base_env.require("python"))
            _phase(b, "project_env.ensure",
                   lambda: project_env.ensure(pid, "python"))
            cold_box: list = []
            _phase(b, f"get_or_start(cold{i})",
                   lambda: cold_box.append(
                       get_pool().get_or_start(f"cold{i}", "python",
                                               cwd=str(scratch_dir(pid, f"cold{i}")))))
            # The FIRST BLOCK on a kernel born mid-install is its own phase:
            # the start call can return promptly while the kernel waits on the
            # session's realization to run anything — which is exactly where
            # the live server path blocked (93 s beside a 92 s install) while
            # a start-only walk read independent.
            if cold_box:
                _phase(b, f"execute(cold{i} first block)",
                       lambda: cold_box[0].execute("print(1+1)", timeout_s=600))
            _phase(b, "execute(warm kernel)",
                   lambda: warm.execute("print(1+1)", timeout_s=600))
            b["total_s"] = round(time.time() - t0, 2)
            iters.append(b)
            install_done.wait(2.0)
        res["thread_b_iters"] = iters

    t_start = time.time()
    ts = [threading.Thread(target=installer_wrapped),
          threading.Thread(target=trivial)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    res.update(marks)

    blocked: dict = {}
    overlapped = 0
    for b in res.get("thread_b_iters") or []:
        started_during = b.get("at_s", 1e9) <= (marks.get("install_s") or 0)
        if started_during:
            overlapped += 1
        for k, v in b.items():
            if k in ("total_s", "errors", "at_s") or not isinstance(v, (int, float)):
                continue
            if v >= STALL_S:
                key = ("get_or_start(cold)" if k.startswith("get_or_start")
                       else "execute(cold first block)"
                       if k.startswith("execute(cold") else k)
                blocked[key] = max(blocked.get(key, 0), v)
    res["iters_during_install"] = overlapped
    res["stalled_phases"] = blocked
    res["verdict"] = ("STALLED: " + ", ".join(f"{k}={v}s" for k, v in blocked.items())
                      if blocked else "independent")
    return res


def checks(res: dict) -> list[tuple[str, bool]]:
    """ARMED FIRST: an install that never ran, or phase walks that never
    overlapped the install window, prove nothing about contention."""
    inst = res.get("install_s") or 0
    if not res.get("install_ok"):
        return [(f"PRECONDITION: the install ran "
                 f"({res.get('install_error', 'no result')})", False)]
    if not res.get("iters_during_install"):
        return [(f"PRECONDITION: no phase walk started inside the install "
                 f"window ({inst}s) — this run says NOTHING about stalling",
                 False)]
    n = len(res.get("thread_b_iters") or [])
    return [(f"a trivial call is not blocked by a concurrent install "
             f"(install={inst}s, {n} walks, "
             f"{res['iters_during_install']} in-window, {res['verdict']})",
             not res["stalled_phases"])]


if __name__ == "__main__":
    out = run()
    print(json.dumps(out, indent=2))
    ok = all(v for _n, v in checks(out))
    for n, v in checks(out):
        print(("   ok  " if v else "  FAIL ") + n)
    sys.exit(0 if ok else 1)
