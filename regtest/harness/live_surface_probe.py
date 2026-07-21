"""Live surface probe — drive the REAL running server through a real agent
session and assert every USER-FACING surface, the way a person opening the app
actually hits them.

This closes the test-vs-reality gap the fleet kept missing: the fleet drives a
cheap agent and (now) checks mechanism, but it runs in-process against a
TestClient. This probe hits a DEPLOYED server over HTTP with the real chat SSE
endpoint, produces real outputs (figures + a table via a real kernel + real
harvest + real manifest), then verifies what a user would touch:

  - the turn completes (no wedge/erroring);
  - the run's OUTPUTS MANIFEST lists what was produced (a table next to figures
    is not silently dropped);
  - artifact_ids are UNIQUE across outputs (no store-member collision);
  - a directory store is ONE 'store' entry, not hundreds of shard rows;
  - every advertised output URL SERVES (200 / honest 413), none 404s;
  - execution ran on the weft substrate (transport truth).

Domain-neutral by construction: the prompt asks for generic tabular analytics
(synthetic numeric data, summary stats, a histogram + scatter, a results CSV) —
no domain packages or topics. Usage:

    ABA_HOME=~/.aba python regtest/harness/live_surface_probe.py [--base URL]
"""
from __future__ import annotations
import argparse
import json
import sys
import time

import httpx

PROMPT = (
    "Make a small synthetic tabular dataset in Python: 500 rows, three numeric "
    "columns x, y, z where y is x plus noise and z is random. Compute and print "
    "summary statistics. Then create two figures — a histogram of x and a scatter "
    "of x vs y — and save a CSV of the per-row values. Tell me what files you "
    "produced. Keep it to one run."
)


def _sse(client: httpx.Client, url: str, payload: dict, timeout: float) -> dict:
    cap = {"run_id": None, "tools": [], "errors": [], "kinds": {}, "state": None}
    with client.stream("POST", url, json=payload, timeout=timeout) as r:
        r.raise_for_status()
        for line in r.iter_lines():
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
    return cap


def run(base: str) -> int:
    fails: list[str] = []
    note = lambda m: print(f"  {m}", flush=True)
    with httpx.Client(base_url=base) as c:
        pid = c.post("/api/projects", json={"name": "live-probe"}).json().get("id")
        c.post(f"/api/projects/{pid}/open")
        tid = c.post("/api/threads", json={"project_id": pid, "title": "probe"}).json().get("id")
        print(f"[probe] project={pid} thread={tid}  driving a real turn…", flush=True)

        t0 = time.time()
        cap = _sse(c, "/api/chat",
                   {"text": PROMPT, "project_id": pid, "thread_id": tid}, timeout=600.0)
        # resolve approval gates the same way the UI does
        for _ in range(6):
            rid = cap["run_id"]
            if not rid:
                break
            st = c.get(f"/api/turns/{rid}").json().get("state")
            if st != "awaiting_user":
                break
            cap = _sse(c, f"/api/turns/{rid}/resume",
                       {"user_text": "Yes, go ahead."}, timeout=600.0)
        dt = time.time() - t0
        print(f"[probe] turn done in {dt:.0f}s  tools={cap['tools']}  errors={len(cap['errors'])}",
              flush=True)
        if cap["errors"]:
            fails.append(f"turn emitted {len(cap['errors'])} error events: {cap['errors'][:2]}")

        # --- discover the run(s) this thread produced ---
        ents = c.get("/api/entities", params={"project_id": pid, "include_archived": True}).json()
        ents = ents if isinstance(ents, list) else ents.get("entities", [])
        runs = [e["id"] for e in ents if e.get("type") == "analysis"]
        if not runs:
            fails.append("no analysis run entity produced — the agent ran nothing harvestable")
            return _report(fails)
        print(f"[probe] analysis runs: {runs}", flush=True)

        seen_table = seen_fig = False
        all_artifact_ids: list[str] = []
        for rid in runs:
            c.post(f"/api/runs/{rid}/refresh-manifest")
            ent = c.get("/api/entities", params={"project_id": pid}).json()
            ent = next((e for e in (ent if isinstance(ent, list) else ent.get("entities", []))
                        if e["id"] == rid), None)
            md = (ent or {}).get("metadata") or {}
            outs = (md.get("run") or {}).get("outputs") or md.get("outputs") or []
            note(f"run {rid}: {len(outs)} outputs")
            shard_rows = 0
            for o in outs:
                k, label = o.get("kind"), o.get("label", "")
                if k == "table":
                    seen_table = True
                if k == "figure":
                    seen_fig = True
                if o.get("artifact_id"):
                    all_artifact_ids.append(o["artifact_id"])
                # NO raw store-shard rows should surface
                if ".zarr/" in label and k != "store":
                    shard_rows += 1
                # SERVABILITY: every advertised href must serve (200) or honestly 413
                href = o.get("href")
                if href and href.startswith("/api/runs/"):
                    rr = c.get(href)
                    if rr.status_code not in (200, 206, 413):
                        fails.append(f"output '{label}' ({k}) advertised but {href} -> "
                                     f"{rr.status_code} (a user clicking it 404s)")
            if shard_rows:
                fails.append(f"run {rid}: {shard_rows} raw store-shard rows leaked into the "
                             f"manifest (directory store not collapsed)")
            # transport truth: execs on weft
            execs = c.get(f"/api/runs/{rid}/execs").json()
            execs = execs.get("execs", execs) if isinstance(execs, dict) else execs
            for ex in (execs or []):
                sub = ((ex.get("compute") or {}).get("substrate") if isinstance(ex.get("compute"), dict)
                       else ex.get("compute"))
                if sub and sub != "weft":
                    fails.append(f"exec {ex.get('exec_id')} ran on '{sub}', not weft")

        if not seen_fig:
            fails.append("no figure output surfaced (the agent's plots didn't reach the manifest)")
        if not seen_table:
            fails.append("no table output surfaced — a CSV the agent produced was dropped "
                         "from the manifest (the harvest/manifest gap class)")
        dupes = {a for a in all_artifact_ids if all_artifact_ids.count(a) > 1}
        if dupes:
            fails.append(f"artifact_id collision: {len(dupes)} id(s) shared across distinct "
                         f"outputs (pin/dedup/address broken) — e.g. {list(dupes)[:2]}")
    return _report(fails)


def _report(fails: list[str]) -> int:
    print("\n=== LIVE SURFACE PROBE ===", flush=True)
    if not fails:
        print("PASS — every produced output is advertised, unique, and servable.", flush=True)
        return 0
    print(f"FAIL — {len(fails)} surface defect(s):", flush=True)
    for f in fails:
        print(f"  ✗ {f}", flush=True)
    return 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    args = ap.parse_args()
    sys.exit(run(args.base))
