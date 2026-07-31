"""Live surface probe — drive the REAL running server through a real agent
session and assert every USER-FACING surface, the way a person opening the app
actually hits them.

This closes the test-vs-reality gap the fleet kept missing: the fleet drives a
cheap agent and (now) checks mechanism, but it runs in-process against a
TestClient. This probe hits a DEPLOYED server over HTTP with the real chat SSE
endpoint, produces real outputs (real kernel + real harvest + real manifest),
then verifies what a user would touch:

  - the turn completes (no wedge/erroring);
  - the run's OUTPUTS MANIFEST lists what was produced (a table next to figures
    is not silently dropped);
  - artifact_ids are UNIQUE across outputs (no store-member collision);
  - a directory store is ONE 'store' entry, not hundreds of shard rows;
  - every advertised output URL SERVES (200/206 / honest 413), none 404s;
  - execution ran on the weft substrate (transport truth, NON-VACUOUS: zero
    substrate-stamped exec records is a FAIL, not a pass);
  - the standing surface-parity oracle holds for the probe project.

The report is ITEMIZED (per-kind output counts, per-href statuses, per-run
transport line) so a failure is diagnosable without re-running the probe.

Prompt shapes cover the surface matrix; each runs in its own fresh project:

  mixed   (default) a table AND figures in one run — the drop-a-table class
  table   a lone CSV — no figure machinery involved
  figure  a lone figure — no table machinery involved
  store   a chunked DIRECTORY store + a table — collapse + shard-leak class

Domain-neutral by construction: prompts ask for generic synthetic numeric
data. Setup problems (server down, project/thread creation failed) exit 2 —
SETUP-ERROR, never conflated with a product FAIL (exit 1). Usage:

    python regtest/harness/live_surface_probe.py [--base URL]
        [--shape mixed --shape store | --all-shapes] [--timeout 600]

Run it through a subagent (it drives a real agent turn), never in the main
orchestrator context.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from surfaces import surface_parity_failures  # noqa: E402
from transport import transport_truth  # noqa: E402

# Keep in sync with backend.content.bio.lifecycle.runs._STORE_DIR_SUFFIXES —
# asserted equal by tests/test_live_surface_probe_eval.py (the probe must not
# import backend: it runs against a DEPLOYED server from any checkout).
STORE_SUFFIXES = (".zarr",)

OK_STATUSES = (200, 206, 413)   # served, ranged, or honestly refused

SHAPES: dict[str, dict] = {
    "mixed": {
        "prompt": (
            "Make a small synthetic tabular dataset in Python: 500 rows, three "
            "numeric columns x, y, z where y is x plus noise and z is random. "
            "Compute and print summary statistics. Then create two figures — a "
            "histogram of x and a scatter of x vs y — and save a CSV of the "
            "per-row values. Tell me what files you produced. Keep it to one run."
        ),
        "expect": {"figure": 1, "table": 1},
    },
    "table": {
        "prompt": (
            "In Python, build a small synthetic numeric table (100 rows, columns "
            "a, b, c with random values), print its head, and save it as "
            "summary.csv. No plots, no figures — just the CSV. One run."
        ),
        "expect": {"table": 1},
    },
    "figure": {
        "prompt": (
            "In Python, draw 1000 random normal values and save a single "
            "histogram figure of them (PNG). No CSV or table outputs — just the "
            "figure. One run."
        ),
        "expect": {"figure": 1},
    },
    "store": {
        "prompt": (
            "In Python, create a synthetic 2-D numeric array (500 rows x 50 "
            "columns of random values) and write it as a chunked-array directory "
            "store (a directory named data.zarr). Also save the per-row means "
            "as means.csv. Tell me what you produced. Keep it to one run."
        ),
        "expect": {"store": 1, "table": 1},
    },
}


# ---------- SSE (accumulating: resume hops must NOT drop earlier events) ----------
def consume(stream, cap: dict) -> None:
    """Fold one SSE stream into `cap`. Called once for the initial /api/chat
    stream and once per resume hop — accumulating, so an error event emitted
    BEFORE an approval gate still fails the probe after the gate resolves."""
    for line in stream.iter_lines():
        if not line or not line.startswith("data: "):
            continue
        try:
            ev = json.loads(line[6:])
        except Exception:
            continue
        t = ev.get("type")
        cap["kinds"][t] = cap["kinds"].get(t, 0) + 1
        if ev.get("run_id"):
            cap["run_id"] = ev["run_id"]
        if t == "tool_start":
            cap["tools"].append(ev.get("name") or ev.get("tool") or "?")
        elif t in ("error", "cancelled"):
            cap["errors"].append(str(ev)[:300])


# ---------- pure evaluation (unit-guarded; no I/O) ----------
def evaluate_shape(shape: str, expect: dict, runs_data: list[dict],
                   transport: dict) -> tuple[list[str], list[str]]:
    """Assess one shape's collected evidence. Pure — all fetching happened
    upstream. `runs_data`: [{run_id, outputs: [manifest entries],
    href_status: {href: int}}]; `transport`: transport_truth()'s return.
    Returns (failures, itemized report lines)."""
    fails: list[str] = []
    lines: list[str] = []
    totals: dict[str, int] = {}
    all_ids: list[str] = []

    for rd in runs_data:
        rid = rd.get("run_id")
        outs = rd.get("outputs") or []
        counts: dict[str, int] = {}
        shard_rows = 0
        for o in outs:
            k = o.get("kind") or "?"
            counts[k] = counts.get(k, 0) + 1
            totals[k] = totals.get(k, 0) + 1
            if o.get("artifact_id"):
                all_ids.append(o["artifact_id"])
            label = o.get("label") or ""
            # a store MEMBER row (path descends through a store-suffix dir)
            # must not surface — the store itself is the one legitimate row
            if k != "store" and any(
                    p.lower().endswith(STORE_SUFFIXES)
                    for p in label.split("/")[:-1]):
                shard_rows += 1
        hs = rd.get("href_status") or {}
        n_ok = sum(1 for s in hs.values() if s in OK_STATUSES)
        by_code: dict[int, int] = {}
        for s in hs.values():
            by_code[s] = by_code.get(s, 0) + 1
        code_str = " ".join(f"{c}x{n}" for c, n in sorted(by_code.items()))
        kind_str = " ".join(f"{k}={n}" for k, n in sorted(counts.items())) or "none"
        lines.append(f"run {rid}: outputs {len(outs)} ({kind_str}); "
                     f"hrefs {n_ok}/{len(hs)} ok [{code_str or '-'}]")
        if shard_rows:
            fails.append(f"{shape}: run {rid}: {shard_rows} raw store-shard "
                         f"row(s) leaked into the manifest (store not collapsed)")
        for href, st in sorted(hs.items()):
            if st not in OK_STATUSES:
                fails.append(f"{shape}: run {rid}: advertised href -> {st} "
                             f"(a user clicking it dead-links): {href}")

    for kind, n_min in (expect or {}).items():
        if totals.get(kind, 0) < n_min:
            fails.append(f"{shape}: expected >= {n_min} '{kind}' output(s) in "
                         f"the manifest, saw {totals.get(kind, 0)} "
                         f"(the produce-then-drop class)")

    dupes = sorted({a for a in all_ids if all_ids.count(a) > 1})
    if dupes:
        fails.append(f"{shape}: artifact_id collision — {len(dupes)} id(s) "
                     f"shared across distinct outputs (pin/dedup/address "
                     f"broken), e.g. {dupes[:2]}")

    checked = transport.get("checked", 0)
    tfails = transport.get("failures") or []
    lines.append(f"transport: execs checked={checked} "
                 f"off-substrate={len(tfails)}")
    fails.extend(f"{shape}: {f}" for f in tfails)
    if checked == 0:
        fails.append(f"{shape}: transport UNPROVEN — zero substrate-stamped "
                     f"exec records for a turn that claims real execution "
                     f"(a vacuous pass is a fail)")

    return fails, lines


# ---------- live driving ----------
def _drive_shape(c, shape: str, spec: dict, timeout: float) -> tuple[list[str], list[str]]:
    pid = c.post("/api/projects", json={"name": f"live-probe-{shape}"}).json().get("id")
    c.post(f"/api/projects/{pid}/open")
    tid = c.post("/api/threads",
                 json={"project_id": pid, "title": f"probe-{shape}"}).json().get("id")
    if not pid or not tid:
        raise RuntimeError(f"project/thread creation failed (pid={pid} tid={tid})")
    print(f"[probe:{shape}] project={pid} thread={tid}  driving a real turn…",
          flush=True)

    cap = {"run_id": None, "tools": [], "errors": [], "kinds": {}}
    t0 = time.time()
    with c.stream("POST", "/api/chat", timeout=timeout,
                  json={"text": spec["prompt"], "project_id": pid,
                        "thread_id": tid}) as r:
        r.raise_for_status()
        consume(r, cap)
    for _ in range(6):   # resolve approval gates the way the UI does
        rid = cap["run_id"]
        if not rid:
            break
        st = c.get(f"/api/turns/{rid}").json().get("state")
        if st != "awaiting_user":
            break
        with c.stream("POST", f"/api/turns/{rid}/resume", timeout=timeout,
                      json={"user_text": "Yes, go ahead."}) as r2:
            r2.raise_for_status()
            consume(r2, cap)
    dt = time.time() - t0
    print(f"[probe:{shape}] turn done in {dt:.0f}s tools={len(cap['tools'])} "
          f"errors={len(cap['errors'])}", flush=True)

    fails: list[str] = []
    if cap["errors"]:
        fails.append(f"{shape}: turn emitted {len(cap['errors'])} error "
                     f"event(s): {cap['errors'][:2]}")

    ents = c.get("/api/entities",
                 params={"project_id": pid, "include_archived": True}).json()
    ents = ents if isinstance(ents, list) else ents.get("entities", [])
    runs = [e["id"] for e in ents if e.get("type") == "analysis"]
    if not runs:
        fails.append(f"{shape}: no analysis run entity produced — the agent "
                     f"ran nothing harvestable")
        return fails, [f"(no runs; turn tools={cap['tools']})"]

    runs_data: list[dict] = []
    for rid in runs:
        c.post(f"/api/runs/{rid}/refresh-manifest")
        rows = c.get("/api/entities", params={"project_id": pid}).json()
        rows = rows if isinstance(rows, list) else rows.get("entities", [])
        ent = next((e for e in rows if e["id"] == rid), None)
        md = (ent or {}).get("metadata") or {}
        outs = (md.get("run") or {}).get("outputs") or md.get("outputs") or []
        href_status: dict[str, int] = {}
        for o in outs:
            href = o.get("href")
            if href and href.startswith("/api/runs/"):
                href_status[href] = c.get(href).status_code
        runs_data.append({"run_id": rid, "outputs": outs,
                          "href_status": href_status})

    tt = transport_truth(c, pid, run_ids=runs)
    sfails, lines = evaluate_shape(shape, spec.get("expect") or {}, runs_data, tt)
    fails.extend(sfails)

    # the standing parity oracle on top — same walk the sweep + live audit use
    fails.extend(f"{shape}: {f}"
                 for f in surface_parity_failures(c, pid, run_ids=runs))
    lines.append(f"turn: {dt:.0f}s, {len(cap['tools'])} tool call(s), "
                 f"{len(cap['errors'])} error event(s)")
    return fails, lines


def run(base: str, shapes: list[str], timeout: float) -> int:
    import httpx   # deferred: the pure evaluator must import without it
    all_fails: list[str] = []
    report: list[str] = []
    with httpx.Client(base_url=base) as c:
        try:
            h = c.get("/api/health", timeout=10.0)
            if h.status_code != 200:
                print(f"SETUP-ERROR: server unhealthy at {base}: {h.status_code}")
                return 2
            pj = c.get("/api/projects").json()
            rows = pj if isinstance(pj, list) else pj.get("projects", [])
            initial = next((p.get("id") for p in rows
                            if p.get("current") or p.get("active")
                            or p.get("open")), None)
        except Exception as e:  # noqa: BLE001
            print(f"SETUP-ERROR: cannot reach server at {base}: {e}")
            return 2

        for shape in shapes:
            try:
                fails, lines = _drive_shape(c, shape, SHAPES[shape], timeout)
            except Exception as e:  # noqa: BLE001 — a crashed drive is setup-class
                print(f"SETUP-ERROR: shape '{shape}' could not be driven: {e}")
                return 2
            all_fails.extend(fails)
            report.append(f"[{shape}]")
            report.extend(f"  {ln}" for ln in lines)

        if initial:   # opening probe projects flipped the server's active binding
            try:
                c.post(f"/api/projects/{initial}/open")
                report.append(f"(restored active project {initial})")
            except Exception:  # noqa: BLE001 — restore is best-effort
                pass

    print("\n=== LIVE SURFACE PROBE ===", flush=True)
    for ln in report:
        print(ln, flush=True)
    if not all_fails:
        print("PASS — every produced output is advertised, unique, servable, "
              "and substrate-executed.", flush=True)
        return 0
    print(f"FAIL — {len(all_fails)} surface defect(s):", flush=True)
    for f in all_fails:
        print(f"  X {f}", flush=True)
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--shape", action="append", choices=sorted(SHAPES),
                    help="repeatable; default: mixed")
    ap.add_argument("--all-shapes", action="store_true")
    ap.add_argument("--timeout", type=float, default=600.0,
                    help="per-SSE-stream read timeout, seconds")
    args = ap.parse_args()
    _shapes = list(SHAPES) if args.all_shapes else (args.shape or ["mixed"])
    sys.exit(run(args.base, _shapes, args.timeout))
