"""Convoy canary — the durable route must never starve the rest of the server.

The failure class (2026-07, live): /api/runs/{rid}/durable was polled per open
Run card; each poll parked a threadpool worker on a slow serialized
computation; at ~40 parked pollers EVERY sync route (images, messages,
entities) stopped answering until the agent released the substrate. The fix is
layered (async single-flight route + bounded occupancy here; batched verbs +
per-thread readers in the substrate) — this canary asserts the OUTCOME from
the outside, the way a browser hits it:

  1. storm: N concurrent /durable requests for one run;
  2. probe: a cheap unrelated sync route, fired repeatedly DURING the storm;
  3. verdicts:
       - probes that overlapped the storm answered fast (p95 under budget) —
         the "server stopped" state is gone;
       - the storm's wall-clock ≈ ONE computation, not N (single-flight seen
         from outside: 4× a lone request, floored for noise);
       - every storm request succeeded.

ARMED: the canary FAILS (exit 2, NOT-ARMED) unless enough probes verifiably
overlapped in-flight storm requests — a canary whose probes all ran after the
storm drained measures nothing and must say so, never pass.

Pure HTTP, stdlib only, no agent turns — cheap enough for a pre-pilot gate.
Setup trouble (server down, no runs to poll) exits 2; a product failure
exits 1. Usage:

    python regtest/harness/convoy_canary.py [--base http://127.0.0.1:8000]
        [--run RID] [--project PID] [--storm 16] [--probe-budget-ms 1500]
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request


def _get(base: str, path: str, timeout: float = 30.0):
    """(status, seconds, body|None) — errors return status 0, never raise."""
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(base + path, timeout=timeout) as r:
            body = r.read()
            return r.status, time.monotonic() - t0, body
    except urllib.error.HTTPError as e:
        return e.code, time.monotonic() - t0, None
    except Exception:  # noqa: BLE001 — timeout/refused = status 0
        return 0, time.monotonic() - t0, None


def _discover_run(base: str, project: str | None) -> str | None:
    """Newest analysis entity — the run whose panel a user would have open."""
    qs = "?type=analysis&limit=50" + (f"&project_id={project}" if project else "")
    status, _, body = _get(base, f"/api/entities{qs}")
    if status != 200 or not body:
        return None
    try:
        ents = json.loads(body)
        rows = ents if isinstance(ents, list) else ents.get("entities") or []
        rows = [e for e in rows if e.get("type") == "analysis"]
        rows.sort(key=lambda e: e.get("created_at") or "", reverse=True)
        return rows[0]["id"] if rows else None
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--run", default=None, help="run id (default: newest analysis)")
    ap.add_argument("--project", default=None, help="project id for discovery")
    ap.add_argument("--storm", type=int, default=16)
    ap.add_argument("--probe-budget-ms", type=int, default=1500)
    args = ap.parse_args()
    base = args.base.rstrip("/")

    status, _, _ = _get(base, "/api/projects", timeout=5)
    if status != 200:
        print(f"SETUP-ERROR: {base} not answering (/api/projects → {status})")
        return 2
    if args.project:
        # /durable (like the UI) reads the OPENED project — pin it first
        req = urllib.request.Request(
            f"{base}/api/projects/{args.project}/open", method="POST", data=b"")
        try:
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:  # noqa: BLE001
            print(f"SETUP-ERROR: cannot open project {args.project}: {e}")
            return 2
    rid = args.run or _discover_run(base, args.project)
    if not rid:
        print("SETUP-ERROR: no analysis run found to poll (pass --run/--project)")
        return 2
    probe_path = "/api/entities?type=analysis&limit=1" + (
        f"&project_id={args.project}" if args.project else "")

    # lone-request baseline (also warms imports so the storm measures steady state)
    s0, t_single, _ = _get(base, f"/api/runs/{rid}/durable")
    if s0 != 200:
        print(f"SETUP-ERROR: /durable for {rid} → {s0} (need a pollable run)")
        return 2

    # ── storm + overlapping probes ──────────────────────────────────────────
    spans: list[tuple[float, float, int]] = []   # (start, end, status) per storm req
    probes: list[tuple[float, float, int]] = []  # (start, sec, status) per probe
    # parties: storm threads + prober + the MAIN thread (which uses the same
    # barrier to timestamp the release — it must be counted, or its wait
    # re-arms the barrier and blocks forever)
    gate = threading.Barrier(args.storm + 2)
    storm_live = threading.Event()
    storm_live.set()

    def _storm_one():
        gate.wait()
        t0 = time.monotonic()
        st, _, _ = _get(base, f"/api/runs/{rid}/durable")
        spans.append((t0, time.monotonic(), st))

    def _prober():
        gate.wait()
        while storm_live.is_set():
            t0 = time.monotonic()
            st, sec, _ = _get(base, probe_path, timeout=10)
            probes.append((t0, sec, st))
            time.sleep(0.05)

    threads = [threading.Thread(target=_storm_one) for _ in range(args.storm)]
    pt = threading.Thread(target=_prober)
    for t in threads:
        t.start()
    pt.start()
    wall0 = time.monotonic()
    gate.wait()
    for t in threads:
        t.join()
    storm_wall = time.monotonic() - wall0
    storm_live.clear()
    pt.join()

    # ── verdicts ────────────────────────────────────────────────────────────
    fails: list[str] = []
    bad = [st for _, _, st in spans if st != 200]
    if bad:
        fails.append(f"storm: {len(bad)}/{len(spans)} requests failed ({bad[:5]})")

    # ARMED: probes count only if they LAUNCHED while a storm request was in
    # flight; a canary that only probed the drained server proved nothing.
    def _overlapped(t0: float) -> bool:
        return any(s <= t0 <= e for s, e, _ in spans)
    during = [(sec, st) for t0, sec, st in probes if _overlapped(t0)]
    if len(during) < 3:
        print(f"NOT-ARMED: only {len(during)} probes overlapped the storm "
              f"(storm wall {storm_wall:.2f}s — too fast to probe against; "
              f"raise --storm or point at a bigger run)")
        return 2

    budget = args.probe_budget_ms / 1000
    lat = sorted(sec for sec, _ in during)
    p95 = lat[max(0, int(len(lat) * 0.95) - 1)]
    if p95 > budget:
        fails.append(f"probe p95 {p95 * 1000:.0f}ms > {args.probe_budget_ms}ms "
                     f"budget WHILE durable storm in flight — the starvation class")
    dead = [st for _, st in during if st != 200]
    if dead:
        fails.append(f"probes during storm: {len(dead)}/{len(during)} failed "
                     f"— 'not slow: stopped' is back")

    # single-flight seen from outside: N concurrent pollers ≈ one computation
    coalesce_cap = max(4 * t_single, 2.0)
    if storm_wall > coalesce_cap:
        fails.append(f"storm wall {storm_wall:.2f}s > {coalesce_cap:.2f}s "
                     f"(4× lone request {t_single:.2f}s) — pollers are NOT "
                     f"sharing a flight")

    print(f"convoy canary — run {rid} @ {base}")
    print(f"  lone /durable:      {t_single * 1000:.0f}ms")
    print(f"  storm ({args.storm} concurrent): wall {storm_wall * 1000:.0f}ms, "
          f"{len(spans) - len(bad)}/{len(spans)} ok")
    print(f"  probes during storm: {len(during)} overlapped, "
          f"p95 {p95 * 1000:.0f}ms, median {statistics.median(l for l in lat) * 1000:.0f}ms")
    if fails:
        print("FAIL:")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS: probes stayed live and pollers shared the flight")
    return 0


if __name__ == "__main__":
    sys.exit(main())
