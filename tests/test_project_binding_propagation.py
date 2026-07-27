"""The project binding must survive every thread hop — or writes land in the
WRONG project.

`projects.bind(pid)` binds the active project (and its DB) for the current
execution context. A worker thread is a DIFFERENT execution context, and
`loop.run_in_executor(None, fn, ...)` does not carry contextvars into it — so
inside that thread `projects.current()` and `active_db_path()` silently fall back
to the PROCESS-GLOBAL project: whatever project some other request last touched.

Live (2026-07-27, the orbtest workflow sweep). Two projects were driven with
overlapping turns. In the second one the agent ran R on a remote site, then
Python; the transcript is in project B, but:

  * every execution_record for B's thread was written into project A's DB
    (B ended with ZERO provenance for a run it actually performed);
  * the harvest directory was created under A/work/thread-<B's thread>/;
  * the produced CSV was registered as an ARTIFACT of A — a project that never
    produced it. `find_files` in B then listed A's paths as its own.

Messages were right because they are written on the event loop; everything the
tool body wrote was wrong because the tool body runs past the executor hop. This
is the same class as the 2026-06 history corruption that bind() was introduced to
fix — the executor hop was the hole left in it.

The three helpers in core.projects (in_thread / in_pool / spawn) are the only
sanctioned way across; the static check below is what catches the next one.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core import projects                      # noqa: E402
from core.graph import _schema                 # noqa: E402

GLOBAL_PID = "prj_GLOBALbystander"
BOUND_PID = "prj_BOUNDproducer"


def _probe():
    """What the code inside the thread would resolve — the two values every
    project-scoped write depends on."""
    return projects.current(), str(_schema.active_db_path())


def _sees_bound(observed) -> bool:
    pid, db = observed
    return pid == BOUND_PID and f"{BOUND_PID}/" in db.replace("\\", "/")


def _sees_global(observed) -> bool:
    pid, db = observed
    return pid == GLOBAL_PID or f"{BOUND_PID}/" not in db.replace("\\", "/")


@pytest.fixture
def globals_point_elsewhere(monkeypatch):
    """The precondition of the whole bug: a DIFFERENT project is the global.
    Without this the test measures nothing — bound and global would agree and
    every helper would look correct.
    """
    monkeypatch.setattr(projects, "_single", lambda: False)
    monkeypatch.setitem(projects._state, "current", GLOBAL_PID)
    # ARMED: assert the precondition actually took, on the loop, before we
    # start comparing threads.
    with projects.bind(BOUND_PID):
        assert _sees_bound(_probe()), "bind() itself is broken; nothing below means anything"
    assert _sees_global(_probe()), "the global was not repointed — the test is vacuous"
    return GLOBAL_PID


# ── the control: this is what the bug looked like ────────────────────────────

def test_a_bare_run_in_executor_LOSES_the_binding(globals_point_elsewhere):
    """PROVEN-red, inline and permanently: the mechanism the fix replaces still
    demonstrates the failure, so the passing tests below are known to measure a
    real difference rather than a coincidence.
    """
    async def go():
        loop = asyncio.get_running_loop()
        with projects.bind(BOUND_PID):
            return await loop.run_in_executor(None, _probe)

    observed = asyncio.run(go())
    assert _sees_global(observed), (
        "run_in_executor started propagating contextvars — if so this guard's "
        "premise changed and the helpers may be simplifiable")
    assert not _sees_bound(observed)


# ── the fix: all three sanctioned crossings ──────────────────────────────────

def test_in_thread_keeps_the_binding(globals_point_elsewhere):
    async def go():
        with projects.bind(BOUND_PID):
            return await projects.in_thread(_probe)

    assert _sees_bound(asyncio.run(go()))


def test_in_pool_keeps_the_binding_on_a_custom_executor(globals_point_elsewhere):
    """to_thread only uses the default executor; the compute adapter owns its
    own pool, so the guarantee has to hold there too."""
    async def go():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            with projects.bind(BOUND_PID):
                return await projects.in_pool(pool, _probe)

    assert _sees_bound(asyncio.run(go()))


def test_spawn_keeps_the_binding_fire_and_forget(globals_point_elsewhere):
    async def go():
        with projects.bind(BOUND_PID):
            fut = projects.spawn(_probe)
        # NOTE: awaited AFTER the bind block exits. The context copy is a
        # snapshot, so a background advisor that outlives the request must still
        # write into the project that launched it.
        return await asyncio.wrap_future(fut)

    assert _sees_bound(asyncio.run(go()))


def test_spawn_with_no_running_loop_runs_inline_and_bound(globals_point_elsewhere):
    """WIDE — the degenerate shape: a *sync* FastAPI route has no running loop.
    The old callsites each carried their own `except RuntimeError: fn()` fallback;
    losing it would mean the advisor silently never runs."""
    seen = []
    with projects.bind(BOUND_PID):
        ret = projects.spawn(lambda: seen.append(_probe()))
    assert ret is None, "no loop → nothing to schedule against"
    assert len(seen) == 1, "the call was DROPPED, not run inline"
    assert _sees_bound(seen[0])


def test_the_substrate_sync_bridge_keeps_the_binding(globals_point_elsewhere):
    """core.compute.adapter.run_sync's second case spawns a RAW
    threading.Thread, which starts with an EMPTY context — everything the
    substrate call does inside it (path resolution, harvest bookkeeping) would
    resolve the project from the process-global. Exercised through the real
    entry point, in the shape it hits live: a running loop on a worker thread.
    """
    import threading
    from core.compute import adapter

    seen: list = []

    async def _coro():
        seen.append(_probe())
        return "done"

    result: dict = {}

    def worker():
        # a running loop ON A WORKER THREAD is run_sync's second case
        async def go():
            return adapter.run_sync(_coro())
        with projects.bind(BOUND_PID):
            result["v"] = asyncio.run(go())

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert result.get("v") == "done", "the bridge stopped returning values"
    assert seen and _sees_bound(seen[0]), \
        f"the substrate bridge lost the project binding: {seen}"


def test_the_background_job_lane_binds_its_project(globals_point_elsewhere, monkeypatch):
    """The job WORKER is global; the WORK is per-project. Without a binding
    around the job body every ambient read inside resolves to the process-global.

    Live (2026-07-27): job continuations from a finished sweep kept writing exec
    records into whatever project the NEXT sweep had just created. The calls that
    take project_id explicitly were fine; the ambient reads deeper in were not —
    which is why passing the id to the entry points was not enough.
    """
    from core.jobs import runner

    seen: list = []

    async def _inner(job_id, project_id, job):
        seen.append(_probe())

    monkeypatch.setattr(runner, "_run_one_inner", _inner)
    monkeypatch.setattr(runner, "get_job",
                        lambda jid, project_id=None: {"status": "queued",
                                                      "params": {}, "id": jid})

    asyncio.run(runner._run_one("job_x", BOUND_PID))
    assert seen and _sees_bound(seen[0]), f"job body ran unbound: {seen}"


def test_a_legacy_job_row_takes_its_project_from_params(globals_point_elsewhere,
                                                        monkeypatch):
    """WIDE — the shape that has no dequeued project_id: older rows carry it in
    params, and that path must bind too rather than silently running ambient."""
    from core.jobs import runner

    seen: list = []

    async def _inner(job_id, project_id, job):
        seen.append(_probe())

    monkeypatch.setattr(runner, "_run_one_inner", _inner)
    monkeypatch.setattr(runner, "get_job",
                        lambda jid, project_id=None: {
                            "status": "queued", "id": jid,
                            "params": {"project_id": BOUND_PID}})

    asyncio.run(runner._run_one("job_y"))      # no project_id argument
    assert seen and _sees_bound(seen[0]), f"legacy row ran unbound: {seen}"


def test_a_job_with_no_project_anywhere_still_runs(globals_point_elsewhere,
                                                   monkeypatch):
    """CEILING: bind(None) is a documented no-op. A job that names no project
    must still execute (ambient), not raise or be skipped."""
    from core.jobs import runner

    ran: list = []

    async def _inner(job_id, project_id, job):
        ran.append(True)

    monkeypatch.setattr(runner, "_run_one_inner", _inner)
    monkeypatch.setattr(runner, "get_job",
                        lambda jid, project_id=None: {"status": "queued",
                                                      "params": {}, "id": jid})
    asyncio.run(runner._run_one("job_z"))
    assert ran == [True]


def test_kwargs_and_args_are_passed_through():
    """The helpers wrap the callable twice (partial∘partial); a signature slip
    would only show up at runtime deep in a tool call."""
    def f(a, b, *, c=0):
        return (a, b, c)

    assert asyncio.run(projects.in_thread(f, 1, 2, c=3)) == (1, 2, 3)

    async def pooled():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return await projects.in_pool(pool, f, 4, 5, c=6)

    assert asyncio.run(pooled()) == (4, 5, 6)

    box = []
    projects.spawn(lambda: box.append(f(7, 8, c=9)))
    assert box == [(7, 8, 9)]


# ── WIDE: the shapes where there is nothing to propagate ─────────────────────

def test_no_bind_at_all_still_resolves_to_the_global(globals_point_elsewhere):
    """CEILING: the fix must not change single-project behaviour. With no bind
    in scope the worker thread should see exactly what it saw before — the
    process-global — not raise and not invent a project."""
    assert _sees_global(asyncio.run(projects.in_thread(_probe)))


def test_binding_a_falsy_pid_is_a_no_op(globals_point_elsewhere):
    """bind(None) means "no project to bind"; crossing a thread must not turn
    that into an error."""
    async def go():
        with projects.bind(None):
            return await projects.in_thread(_probe)

    assert _sees_global(asyncio.run(go()))


def test_single_mode_is_unaffected(monkeypatch):
    """WIDE — the other deployment shape: in SINGLE mode (ABA_DB_PATH set) the
    harness owns DB_PATH, bind() is a no-op and current() is the sentinel.
    Propagation must be inert there rather than repointing anything."""
    monkeypatch.setattr(projects, "_single", lambda: True)
    before = str(_schema.active_db_path())

    async def go():
        with projects.bind("prj_ignored"):
            return await projects.in_thread(_probe)

    pid, db = asyncio.run(go())
    assert pid == "single"
    assert db == before


# ── the static half: catch the NEXT bare hop ─────────────────────────────────

# core/projects.py IS the sanctioned implementation. Everything else must go
# through it. Scans call syntax only, so the explanatory prose in this repo
# (which names run_in_executor a dozen times) does not register as a violation.
_CALL = re.compile(r"\brun_in_executor\s*\(")
_SANCTIONED = {"backend/core/projects.py"}
_SCAN_DIRS = ("backend/core", "backend/content", "backend/guide.py",
              "backend/main.py", "backend/lifespan.py")


def _violations(files: dict[str, str]) -> list[str]:
    """Pure rule → violations, so the scanner can be tested on synthetic input
    before being trusted on the repo (a scanner that matches nothing reads as
    green)."""
    out = []
    for rel, text in files.items():
        if rel in _SANCTIONED:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if _CALL.search(code):
                out.append(f"{rel}:{i}: {line.strip()[:90]}")
    return out


def test_the_scanner_actually_matches():
    """ARMED: a static check that cannot fire is decoration."""
    bad = {"backend/core/x.py": "    out = await loop.run_in_executor(None, fn)\n"}
    assert _violations(bad), "the scanner missed the exact line it exists to catch"
    # and it must not fire on prose or on the sanctioned module
    assert not _violations({"backend/core/x.py": "# run_in_executor(None, fn) drops ctx\n"})
    assert not _violations({"backend/core/projects.py":
                            "return loop.run_in_executor(pool, call)\n"})
    # a partial-wrapped variant is still a violation
    assert _violations({"backend/core/y.py":
                        "loop.run_in_executor(self._pool, functools.partial(fn))\n"})


def test_no_bare_executor_hop_outside_the_helper():
    files = {}
    for spec in _SCAN_DIRS:
        p = ROOT / spec
        paths = [p] if p.is_file() else sorted(p.rglob("*.py"))
        for f in paths:
            if "vendor" in f.parts or "__pycache__" in f.parts:
                continue
            files[str(f.relative_to(ROOT))] = f.read_text(encoding="utf-8", errors="ignore")
    assert files, "scanned nothing — the paths moved"
    bad = _violations(files)
    assert not bad, (
        "bare run_in_executor loses the project binding; use projects.in_thread "
        "/ in_pool / spawn:\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
