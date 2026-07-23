"""The durable-route convoy guard — poller storms may not park the worker pool.

The failure this guards (2026-07, cluster deployment): /api/runs/{rid}/durable
is polled per open Run card, its computation makes ~100 serialized substrate
queries, and as a sync-def route every poller PARKED an anyio threadpool
worker for the whole wait. At ~40 parked pollers the pool was empty and every
other sync route (images, messages, entities) stopped — not slow: stopped —
until the agent released the substrate and everything drained at once.

The contract under guard (core/web/coalesce.py + the route wiring):
  - same-run pollers share ONE in-flight computation (single-flight);
  - durable computations hold at most 2 threadpool workers, EVER, no matter
    how many pollers exist (bounded occupancy — ceiling AND floor asserted:
    a `<= 2` alone would bless an accidental full serialization);
  - a resolved flight is FORGOTTEN — the next request recomputes. This is
    deliberately NOT a cache; a test here fails if someone "improves" it
    into one, because staleness was rejected in review (no TTL patches);
  - errors (incl. 404) reach every awaiter and clear the flight;
  - one awaiter's cancellation (client disconnect) never kills the flight
    others are awaiting;
  - the shared view is never mutated by the per-request tree transform.

Every blocking scenario is HANDSHAKED (the fn signals it entered and waits
for release): a storm that never actually overlapped measures nothing, and
must fail, not pass.

Run: pytest tests/test_durable_route_convoy.py
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import os
import sys
import tempfile
import threading
from pathlib import Path

import pytest

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_convoy_"))
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core.web.coalesce import Coalescer  # noqa: E402

pytestmark = pytest.mark.bio  # route tests import content.bio

_TICK = 0.005
_PATIENCE = 400  # × _TICK = 2s — generous; loops exit on their condition


async def _until(cond, what: str):
    """Await a threaded condition without parking the loop; ARMED — a
    condition that never fires fails the test instead of timing out silent."""
    for _ in range(_PATIENCE):
        if cond():
            return
        await asyncio.sleep(_TICK)
    raise AssertionError(f"handshake never fired: {what}")


# ── the coalescer itself ─────────────────────────────────────────────────────

def test_same_key_storm_computes_once():
    async def scenario():
        co = Coalescer(max_concurrent=2)
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(5)
            return {"n": calls}

        storm = [asyncio.ensure_future(co.get("run-1", fn)) for _ in range(10)]
        # the flight is verifiably OPEN while all 10 awaiters are attached
        await _until(entered.is_set, "computation entered")
        assert co.inflight() == 1
        release.set()
        results = await asyncio.gather(*storm)
        assert calls == 1, f"single-flight leaked: {calls} computations"
        assert all(r == {"n": 1} for r in results)
        assert results[0] is results[1], "awaiters must share the one result"
        assert co.inflight() == 0
    asyncio.run(scenario())


def test_distinct_keys_occupancy_is_exactly_the_bound():
    async def scenario():
        co = Coalescer(max_concurrent=2)
        lock, release = threading.Lock(), threading.Event()
        cur = high = 0

        def fn(key):
            nonlocal cur, high
            with lock:
                cur += 1
                high = max(high, cur)
            release.wait(5)
            with lock:
                cur -= 1
            return key

        storm = [asyncio.ensure_future(co.get(f"k{i}", lambda i=i: fn(f"k{i}")))
                 for i in range(8)]
        # floor: both permitted slots genuinely run concurrently…
        await _until(lambda: high >= 2, "two concurrent computations")
        # …ceiling: a third is never admitted while they hold the semaphore
        # (absence over a settle window — 20 ticks after the floor fired)
        for _ in range(20):
            await asyncio.sleep(_TICK)
        with lock:
            assert cur == 2, f"occupancy bound violated: {cur} concurrent"
        release.set()
        results = await asyncio.gather(*storm)
        assert sorted(results) == [f"k{i}" for i in range(8)]
        assert high == 2, f"high-water {high}: ceiling and floor are BOTH the contract"
    asyncio.run(scenario())


def test_resolved_flight_is_forgotten_not_cached():
    async def scenario():
        co = Coalescer()
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            return calls

        assert await co.get("k", fn) == 1
        assert await co.get("k", fn) == 2, \
            "a resolved flight must be recomputed — this is NOT a cache"
    asyncio.run(scenario())


def test_error_reaches_all_awaiters_then_clears():
    async def scenario():
        co = Coalescer()
        entered, release = threading.Event(), threading.Event()

        def boom():
            entered.set()
            release.wait(5)
            raise ValueError("substrate says no")

        storm = [asyncio.ensure_future(co.get("k", boom)) for _ in range(5)]
        await _until(entered.is_set, "failing computation entered")
        release.set()
        results = await asyncio.gather(*storm, return_exceptions=True)
        assert all(isinstance(r, ValueError) for r in results), \
            f"every awaiter must see the failure: {results}"
        assert co.inflight() == 0, "failed flight must clear, not poison"
        assert await co.get("k", lambda: "recovered") == "recovered"
    asyncio.run(scenario())


def test_awaiter_cancellation_does_not_kill_the_flight():
    async def scenario():
        co = Coalescer()
        entered, release = threading.Event(), threading.Event()
        calls = 0

        def fn():
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(5)
            return "ok"

        keepers = [asyncio.ensure_future(co.get("k", fn)) for _ in range(2)]
        doomed = asyncio.ensure_future(co.get("k", fn))
        await _until(entered.is_set, "computation entered")
        doomed.cancel()
        await asyncio.sleep(_TICK)  # let the cancellation land
        release.set()
        assert await asyncio.gather(*keepers) == ["ok", "ok"], \
            "one client disconnecting must not kill the flight others await"
        assert doomed.cancelled()
        assert calls == 1
    asyncio.run(scenario())


# ── the route wiring ─────────────────────────────────────────────────────────

def _mini_app(monkeypatch, view_fn, entity=True):
    """The REAL router with the substrate walk stubbed at its seam."""
    from fastapi import FastAPI
    import content.bio.web.routes.runs as rr
    import content.bio.lifecycle.runs as runsmod
    monkeypatch.setattr(runsmod, "run_durable_view", view_fn)
    monkeypatch.setattr(rr, "get_entity",
                        lambda rid: ({"id": rid, "type": "analysis"}
                                     if entity else None))
    monkeypatch.setattr(rr, "_durable_flight", Coalescer(max_concurrent=2))
    app = FastAPI()
    app.include_router(rr.router)
    return app, rr


def test_route_is_async():
    """Tripwire: a sync-def /durable parks a worker per poller — the exact
    shape that emptied the pool. Reverting to `def` must fail loudly here."""
    import content.bio.web.routes.runs as rr
    assert inspect.iscoroutinefunction(rr.run_durable), \
        "/durable must be async def (waiters must not hold pool workers)"


def test_route_storm_shares_one_view_across_flat_and_tree(monkeypatch):
    async def scenario():
        import httpx
        entered, release = threading.Event(), threading.Event()
        calls = 0
        view = {"files": [{"rel": "out/a.csv", "state": "retained",
                           "badge": "retained ✓", "bytes": 3, "url": None,
                           "kind": "file", "site": "local", "large": False}],
                "summary": {"retained": 1, "total": 1}}

        def slow_view(rid):
            nonlocal calls
            calls += 1
            entered.set()
            release.wait(5)
            return view

        app, _rr = _mini_app(monkeypatch, slow_view)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://t") as client:
            urls = ["/api/runs/r1/durable?flat=1"] * 3 + ["/api/runs/r1/durable"] * 3
            storm = [asyncio.ensure_future(client.get(u)) for u in urls]
            await _until(entered.is_set, "view computation entered")
            release.set()
            resps = await asyncio.gather(*storm)
        assert all(r.status_code == 200 for r in resps)
        assert calls == 1, f"6 concurrent polls made {calls} view computations"
        flats = [r.json() for r in resps[:3]]
        trees = [r.json() for r in resps[3:]]
        assert all(f == view for f in flats)
        for t in trees:  # tree shape derived from the SAME flight
            assert t["kind"] == "root" and t["summary"] == view["summary"]
            (folder,) = t["children"]
            assert folder["name"] == "out"
            assert folder["children"][0]["state"] == "retained"
    asyncio.run(scenario())


def test_route_404_shared_and_flight_cleared(monkeypatch):
    async def scenario():
        import httpx
        app, rr = _mini_app(monkeypatch, lambda rid: {"files": [],
                                                      "summary": {}},
                            entity=False)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://t") as client:
            r1, r2 = await asyncio.gather(client.get("/api/runs/nope/durable"),
                                          client.get("/api/runs/nope/durable"))
        assert r1.status_code == r2.status_code == 404
        assert rr._durable_flight.inflight() == 0, "404 must not poison the key"
    asyncio.run(scenario())


# ── the amplifier: batched substrate verbs (weft bd6ae6e) ────────────────────
# The route fix bounds how many workers park; THIS bounds why they parked at
# all — the view's per-file stat loop was 2N store queries + N subprocess
# spawns, serialized. retention.file_stats/inventories are the O(1) forms,
# with a version-skew fallback that emulates on a pre-batch substrate.

def _fresh_memo(monkeypatch):
    import core.compute.retention as ret
    monkeypatch.setattr(ret, "_BATCH_REFUSED", set())
    return ret


def test_batched_wrappers_are_one_call(monkeypatch):
    ret = _fresh_memo(monkeypatch)
    calls = []

    def _call(name, /, *a, **kw):
        calls.append((name, a, kw))
        if name == "run_file_stat":
            return {"files": {r: {"exists": True} for r in kw["rels"]}}
        return {"inventories": {t: {"entries": []} for t in kw["targets"]}}
    monkeypatch.setattr(ret, "_call", _call)

    out = ret.file_stats("krn_1", ["a.csv", "b/c.dat"])
    assert set(out["files"]) == {"a.csv", "b/c.dat"}
    inv = ret.inventories(["krn_1", "krn_2"])
    assert set(inv["inventories"]) == {"krn_1", "krn_2"}
    assert [c[0] for c in calls] == ["run_file_stat", "run_inventory"], \
        f"each wrapper must be exactly ONE substrate call: {calls}"
    # degenerate inputs short-circuit — no round-trip for nothing
    assert ret.file_stats("krn_1", []) == {"files": {}}
    assert ret.inventories([]) == {"inventories": {}}
    assert len(calls) == 2


def test_wrapper_emulates_on_pre_batch_substrate_capped_and_memoized(monkeypatch):
    ret = _fresh_memo(monkeypatch)
    batched_attempts, single_calls = [], []

    def _call(name, /, *a, **kw):
        if "rels" in kw:
            batched_attempts.append(name)
            raise TypeError("run_file_stat() got an unexpected keyword "
                            "argument 'rels'")
        single_calls.append(a)
        return {"exists": a[1] == "hit.csv", "bytes": 7}
    monkeypatch.setattr(ret, "_call", _call)

    rels = ["hit.csv"] + [f"f{i}.dat" for i in range(60)]   # 61 > the 50 cap
    out = ret.file_stats("krn_1", rels)["files"]
    assert out["hit.csv"]["exists"] is True
    # the emulation keeps the PRE-BATCH round-trip budget: 50 answered, the
    # tail UNANSWERED (absent = not-checked — never reported "absent on disk")
    assert len(single_calls) == 50 and len(out) == 50
    assert "f59.dat" not in out
    # memo: the refusal is learned ONCE per process
    ret.file_stats("krn_1", ["x.bin"])
    assert len(batched_attempts) == 1, "refused batch form must be memoized"


def test_wrapper_does_not_mask_real_errors_as_version_skew(monkeypatch):
    """Only 'substrate predates the batched form' may degrade to emulation.
    A real failure must propagate — silently retrying it per-file would turn
    one outage into 50 round-trips AND hide the typed cause."""
    from core.compute.errors import ComputeError
    ret = _fresh_memo(monkeypatch)

    def _call(name, /, *a, **kw):
        raise ComputeError("data.missing", "no such run", stage="weft")
    monkeypatch.setattr(ret, "_call", _call)
    with pytest.raises(ComputeError):
        ret.file_stats("krn_1", ["a.csv"])
    assert not ret._BATCH_REFUSED, "a real error must not poison the memo"


def test_inventories_emulation_carries_typed_errors_per_entry(monkeypatch):
    """Fallback parity with weft's batch contract: one absent receipt never
    fails the batch — its entry carries the typed error."""
    from core.compute.errors import ComputeError, is_error_payload
    ret = _fresh_memo(monkeypatch)

    def _call(name, /, *a, **kw):
        if "targets" in kw:
            raise TypeError("run_inventory() got an unexpected keyword "
                            "argument 'targets'")
        if a[0] == "krn_bad":
            raise ComputeError("data.missing", "no inventory recorded",
                               stage="weft")
        return {"entries": [{"path": "x.csv", "bytes": 1, "mtime": 1}]}
    monkeypatch.setattr(ret, "_call", _call)

    out = ret.inventories(["krn_ok", "krn_bad"])["inventories"]
    assert out["krn_ok"]["entries"][0]["path"] == "x.csv"
    assert is_error_payload(out["krn_bad"])
    assert out["krn_bad"]["error"] == "data.missing"


# ── the shared-view invariant ────────────────────────────────────────────────

def test_tree_transform_never_mutates_the_shared_view():
    """Concurrent requests serialize ONE view object; the per-request tree
    derivation must be pure. WIDE: nested dirs, root-level file, empty view."""
    from content.bio.lifecycle.runs import durable_tree_from_view
    dv = {"files": [
        {"rel": "a/b/deep.csv", "state": "saving", "badge": "saving…",
         "bytes": 1, "url": None, "kind": "file", "site": None, "large": False},
        {"rel": "top.png", "state": "retained", "badge": "retained ✓",
         "bytes": 2, "url": "/artifacts/p/x", "kind": "figure",
         "site": "local", "large": False},
    ], "summary": {"retained": 1, "saving": 1, "total": 2}}
    before = copy.deepcopy(dv)
    tree = durable_tree_from_view(dv)
    assert dv == before, "tree transform mutated the shared view"
    assert tree["summary"] == dv["summary"]
    names = sorted(c["name"] for c in tree["children"])
    assert names == ["a", "top.png"]

    empty = {"files": [], "summary": {"total": 0}}
    t0 = durable_tree_from_view(empty)
    assert t0["children"] == [] and t0["summary"] == {"total": 0}


if __name__ == "__main__":
    import subprocess
    raise SystemExit(subprocess.call(
        [sys.executable, "-m", "pytest", __file__, "-v"]))
