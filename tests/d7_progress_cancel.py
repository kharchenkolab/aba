"""
Tool progress streaming (#1) + cancellable installs (#2).

  1. progress sink: emit/drain; no-op without a sink; execute_tool binds the
     sink so deep tool code streams phase lines.
  2. cancellable subprocess: a long install is killed promptly via the cancel
     token (Stop); ensure_capability accepts ctx + threads the token.

Deterministic (no real installs). Run:
    .venv/bin/python tests/d7_progress_cancel.py
"""
from __future__ import annotations
import os
import sys
import time
import queue
import threading
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d7_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d7.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                 # noqa: E402
import content.bio  # noqa: E402,F401
from core.runtime import progress                       # noqa: E402
from core.exec.proc import run_cancellable              # noqa: E402
import content.bio.tools as T                           # noqa: E402
import inspect                                          # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_progress_sink():
    print("progress sink")
    q: queue.Queue = queue.Queue()
    progress.set_sink(q)
    progress.emit("hello", phase="x")
    progress.clear_sink()
    ev = q.get_nowait()
    check("emit reaches the bound sink", ev["message"] == "hello" and ev["phase"] == "x", str(ev))
    progress.emit("after-clear")  # should be a no-op
    check("emit is a no-op with no sink", q.empty())


def test_execute_tool_streams():
    print("execute_tool binds the sink for deep tool code")

    def _emitter(input_):
        from core.runtime import progress as p
        p.emit("phase one"); p.emit("phase two")
        return {"status": "ok"}

    T.EXECUTORS["_test_emit"] = _emitter
    try:
        q: queue.Queue = queue.Queue()
        out = T.execute_tool("_test_emit", {}, {"progress_q": q})
        msgs = []
        while not q.empty():
            msgs.append(q.get_nowait()["message"])
        check("tool result returned", '"status": "ok"' in out, out)
        check("both phase lines streamed", msgs == ["phase one", "phase two"], str(msgs))
        # sink cleared after dispatch → later emits are no-ops
        progress.emit("leaked")
        check("sink cleared after dispatch", q.empty())
    finally:
        T.EXECUTORS.pop("_test_emit", None)


def test_ensure_capability_emits_and_takes_ctx():
    print("ensure_capability streams a phase + accepts ctx (cancel/progress)")
    check("ensure_capability has ctx param", "ctx" in inspect.signature(T.ensure_capability).parameters)
    from core.exec import MaterializingExecutor
    orig = MaterializingExecutor.materialize
    MaterializingExecutor.materialize = lambda self, prov, scope="system", cancel_token=None: None
    try:
        q: queue.Queue = queue.Queue()
        progress.set_sink(q)  # execute_tool normally binds this; do it directly here
        try:
            res = T.ensure_capability({"name": "gseapy"}, {"progress_q": q})  # gseapy is in the seed catalog
        finally:
            progress.clear_sink()
        msgs = [q.get_nowait()["message"] for _ in range(q.qsize())]
        check("ensure returns ready", res.get("status") == "ready", str(res))
        check("emitted a 'Materializing' phase", any("Materializing" in m for m in msgs), str(msgs))
    finally:
        MaterializingExecutor.materialize = orig


def test_cancellable_subprocess():
    print("cancellable subprocess (#2)")
    r = run_cancellable(["echo", "ok"])
    check("normal run returns output", r.returncode == 0 and "ok" in r.stdout)

    # streams milestone lines live (filtered) into the progress sink
    sq: queue.Queue = queue.Queue()
    progress.set_sink(sq)
    try:
        run_cancellable(["bash", "-c", "echo Downloading nextflow; echo boring chatter"])
    finally:
        progress.clear_sink()
    streamed = [sq.get_nowait()["message"] for _ in range(sq.qsize())]
    check("streams milestone lines", any("Downloading nextflow" in m for m in streamed), str(streamed))
    check("filters out boring lines", not any("boring chatter" in m for m in streamed), str(streamed))

    class _Tok:
        def __init__(self): self._cbs = []
        def register(self, cb): self._cbs.append(cb); return lambda: None
        def fire(self): [cb() for cb in self._cbs]

    tok = _Tok()
    threading.Thread(target=lambda: (time.sleep(0.5), tok.fire())).start()
    t0 = time.time()
    r2 = run_cancellable(["sleep", "30"], cancel_token=tok, timeout_s=60)
    dt = time.time() - t0
    check("cancel kills the process promptly", r2.returncode != 0 and dt < 5, f"rc={r2.returncode} dt={dt:.1f}s")

    # run_micromamba + r layer accept the cancel token (plumbing present)
    from core.exec.mamba import run_micromamba
    from core.exec.r import r_install, ensure_r_runtime, r_has_package
    check("run_micromamba accepts cancel_token", "cancel_token" in inspect.signature(run_micromamba).parameters)
    for fn in (r_install, ensure_r_runtime):
        check(f"{fn.__name__} accepts cancel_token", "cancel_token" in inspect.signature(fn).parameters)


def main() -> int:
    init_db()
    test_progress_sink()
    test_execute_tool_streams()
    test_ensure_capability_emits_and_takes_ctx()
    test_cancellable_subprocess()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL PROGRESS/CANCEL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
