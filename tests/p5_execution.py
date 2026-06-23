"""
P5: background execution unified with the P0–P4 stack. A backgrounded run uses
the same scratch workspace + materialized-library overlay as the synchronous
path; run_python routes long/flagged runs to the job queue and returns a
deferred result; jobs are killpg-cancellable. Isolated dirs; live pip.

Run:
    .venv/bin/python tests/p5_execution.py
"""
from __future__ import annotations
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_p5_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "p5.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["ABA_REFS_DIR"] = str(Path(_tmp) / "refs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))
# Catalog content is pack-sourced (installation scope) — point it at the shared
# seed fixture so the capability catalog is populated (pack seeds as test data).
sys.path.insert(0, str(Path(__file__).resolve().parent))       # tests/ for the helper
import _catalog_fixture                                          # noqa: E402
_catalog_fixture.install()

from core.graph._schema import init_db                       # noqa: E402
from core.graph.entities import list_entities                # noqa: E402
from core.graph.jobs import get_job                          # noqa: E402
from core.exec import LocalRouter                            # noqa: E402
import content.bio  # noqa: E402,F401
import content.bio.lifecycle.registry  # noqa: E402,F401  (registers on_job_complete artifact hook)
from content.bio.tools import run_python, ensure_capability  # noqa: E402
from core.jobs.runner import submit_python_job, cancel_job, _run_one  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_router():
    print("ExecutionRouter: sync vs background")
    r = LocalRouter()
    check("short run → local", r.route(estimate={"runtime_min": 0.5}).location == "local")
    check("long run → background", r.route(estimate={"runtime_min": 10}).location == "background")
    check("explicit override → background", r.route(override="background").location == "background")


def test_deferred_shape():
    print("run_python(background=true) → deferred result")
    out = run_python({"code": "print('hi')", "background": True, "title": "t"})
    check("returns deferred shape with job_id",
          out.get("deferred") is True and out.get("deferred_id", "").startswith("job_"), str(out))
    check("job was queued", get_job(out["deferred_id"]) is not None)

    # Router-wrinkle fix: a high timeout_s is a ceiling, NOT a runtime estimate,
    # so it must NOT auto-background; only an explicit estimate (or flag) does.
    sync = run_python({"code": "print('quick')", "timeout_s": 1800})
    check("high timeout_s alone runs synchronously (not backgrounded)",
          not sync.get("deferred") and "quick" in (sync.get("stdout") or ""), str(sync)[:160])
    est = run_python({"code": "print('hi')", "estimated_runtime_min": 10})
    check("estimated_runtime_min over threshold → background",
          est.get("deferred") is True, str(est)[:160])


async def _drive(job_id):
    await _run_one(job_id)


def test_env_unification():
    print("background job sees the materialized-library overlay (shared core)")
    ensure_capability({"name": "pyfaidx"})        # pip → overlay
    job = submit_python_job(
        "import pyfaidx; print('PYFAIDX_OK', pyfaidx.__version__)",
        title="overlay-check", focus_entity_id=None, timeout_s=60, project_id="single")
    asyncio.run(_drive(job["id"]))
    j = get_job(job["id"])
    check("background job completed", j and j["status"] == "done", str(j and j["status"]))
    check("background job imported the overlay library",
          "PYFAIDX_OK" in (j.get("log_tail") or ""), str(j and j.get("log_tail"))[:200])


def test_lifecycle_and_artifacts():
    print("job lifecycle + artifact harvest on completion")
    # Post-cutover (Option B / Phase 5) a harvested PNG is NOT auto-minted as a
    # figure entity (that created shadow-entity clutter) — it lands in the exec
    # result's plots[] and is copied into the artifact registry, to be pinned on
    # demand. So assert the background job HARVESTED the figure (the real
    # contract), not that a figure entity magically appeared. Capture the
    # completed job's result via the same on_job_complete hook the bio
    # registrar listens on.
    from core.hooks.dispatcher import register as _register_hook
    captured: dict = {}
    _register_hook("on_job_complete",
                   lambda ctx: captured.setdefault("ro", ctx.get("result_obj") or {}), priority=1)
    job = submit_python_job(
        "import matplotlib.pyplot as plt\nplt.plot([1,2,3]); plt.savefig('bg.png')\nprint('made plot')",
        title="bg-plot", focus_entity_id=None, timeout_s=60, project_id="single")
    asyncio.run(_drive(job["id"]))
    j = get_job(job["id"])
    check("job done", j and j["status"] == "done", str(j and j["status"]))
    plots = (captured.get("ro") or {}).get("plots") or []
    check("figure harvested from background job (plots[])",
          any(p.get("original_name") == "bg.png" for p in plots), f"plots={plots}")


def test_cancel():
    print("background job cancellation (killpg)")
    async def _run_and_cancel():
        job = submit_python_job("import time; time.sleep(30)", title="sleep",
                                focus_entity_id=None, timeout_s=60, project_id="single")
        task = asyncio.create_task(_run_one(job["id"]))
        await asyncio.sleep(1.5)               # let it start + spawn + acquire token
        t0 = time.time()
        cancel_job(job["id"])                  # fires the token → killpg
        await task
        return job["id"], time.time() - t0
    jid, elapsed = asyncio.run(_run_and_cancel())
    j = get_job(jid)
    check("job marked cancelled", j and j["status"] == "cancelled", str(j and j["status"]))
    check("cancel was prompt (killed, not waited out)", elapsed < 10, f"{elapsed:.1f}s")


def main() -> int:
    init_db()
    test_router()
    test_deferred_shape()
    test_env_unification()
    test_lifecycle_and_artifacts()
    test_cancel()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL P5 EXECUTION CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
