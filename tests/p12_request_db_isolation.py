"""#17 — concurrent HTTP requests for DIFFERENT projects must each read their
OWN project's database. The request-path analogue of p8 (which covers the turn
task). Regression for the request-vs-request leg of the global-DB race.

Before #17 the pin mutated a process-global DB_PATH per request; two requests
for different projects interleaving on the event loop could read each other's
DB. After #17 the pin binds a contextvar per request (pure-ASGI middleware),
which propagates to async handlers and to sync `def` handlers via the
threadpool's context copy — so each request is isolated.

We fire many interleaved requests for projects A and B through the real app
(httpx ASGITransport) and assert every response reflects its own project.

Deterministic w.r.t. the fix (the invariant holds for every response). Isolated
temp runtime. NOT single-project mode.

Run:
    .venv/bin/python tests/p12_request_db_isolation.py
"""
from __future__ import annotations
import os
import sys
import asyncio
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.pop("ABA_DB_PATH", None)
os.environ.pop("ABA_DB_PATH_OVERRIDE", None)
_TMP = tempfile.mkdtemp(prefix="aba_p17_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")
os.environ.setdefault("ABA_FAKE_SESSION", str(ROOT / "tests/fixtures/list_files.jsonl"))
sys.path.insert(0, str(ROOT / "backend"))

from core import projects                       # noqa: E402
import httpx                                     # noqa: E402
from httpx import ASGITransport                  # noqa: E402
from main import app                             # noqa: E402

projects.init()
A = projects.create_project("ProjAAA")["id"]
B = projects.create_project("ProjBBB")["id"]
# Park the global on a THIRD, unrelated state so a leaked/raced read is obvious.
C = projects.create_project("ProjCCC")["id"]
projects.set_current(C)

WANT = {A: "ProjAAA", B: "ProjBBB"}


async def _one(client, pid):
    r = await client.get(f"/api/entities/workspace?project_id={pid}")
    assert r.status_code == 200, f"{pid}: {r.status_code} {r.text}"
    return pid, r.json().get("title")


def test_concurrent_requests_read_their_own_project():
    async def run():
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            # 60 interleaved requests, alternating A/B, all in flight together.
            pids = [A, B] * 30
            results = await asyncio.gather(*(_one(client, p) for p in pids))
        bad = [(pid, title) for pid, title in results if title != WANT[pid]]
        assert not bad, f"{len(bad)}/{len(results)} requests read the WRONG project: {bad[:5]}"
        # And the process-global was never leaked to A or B by all that traffic.
        assert projects.current() == C, \
            f"global leaked under concurrent load: current()={projects.current()!r}"
        print(f"  {len(results)} concurrent requests, all isolated; global still {projects.current()}")

    asyncio.run(run())


def main() -> int:
    failed = []
    for t in [test_concurrent_requests_read_their_own_project]:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
            traceback.print_exc()
    if failed:
        print(f"\n{len(failed)} failed")
        return 1
    print("\nall passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
