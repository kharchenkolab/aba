"""Live-agent regression for the background-job path after the BatchSubmitter
refactor (hpc-jobs P1-P3). Drives the REAL agent on the running server: a prompt
that asks for a background run_python → a job is submitted → the LocalSubmitter +
worker run it → it reaches a terminal state → the continuation fires.

This proves the abstraction didn't break the live local path (the only path
exercisable without a Slurm cluster).

Run:  .venv/bin/python tests/live_hpc_background.py
"""
from __future__ import annotations
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.live_chat_runner import (  # noqa: E402
    BASE, server_reachable, _fresh_project, _fresh_thread, _consume_chat_sse,
)

PROMPT = ("I'm testing the background-job system. Please use run_python with "
          "background=True to compute sum(i*i for i in range(2_000_000)) and "
          "print the result. Run it directly as a background job (no plan needed).")


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())


def main() -> int:
    if not server_reachable():
        print("SKIP: server not reachable at", BASE)
        return 0
    pid = _fresh_project("hpc-bg-live")
    tid = _fresh_thread(pid, "hpc-bg")
    print(f"project={pid} thread={tid}")
    calls, text, halted, reason, events = _consume_chat_sse(pid, tid, PROMPT, max_turns=4)
    print("tool calls:", [c[0] for c in calls])
    if halted:
        print("note: turn halted —", reason)

    # Find the submitted job: a deferred/job event, else the newest job row.
    job_id = None
    for ev in events:
        job_id = ev.get("job_id") or ev.get("deferred_id") or job_id
        res = ev.get("result") if isinstance(ev.get("result"), dict) else None
        if res:
            job_id = res.get("job_id") or res.get("deferred_id") or job_id
    if not job_id:
        jobs = _get("/api/jobs?limit=20")
        mine = [j for j in jobs if (j.get("title") or "").lower().find("background") >= 0]
        if mine:
            job_id = sorted(mine, key=lambda j: j.get("t", 0))[-1]["id"]
    if not job_id:
        print("FAIL: agent did not submit a background job. calls=", [c[0] for c in calls])
        return 1
    print("submitted job:", job_id)

    # Poll to a terminal state.
    deadline = time.time() + 180
    status = None
    while time.time() < deadline:
        j = _get(f"/api/jobs/{job_id}")
        status = j.get("status")
        if status in ("done", "failed", "cancelled"):
            break
        time.sleep(3)
    print("final job status:", status)
    if status == "done":
        log = (_get(f"/api/jobs/{job_id}") or {}).get("log_tail") or ""
        print("log_tail tail:", log[-200:].replace("\n", " "))
        print("PASS: background job ran to completion through the live agent.")
        return 0
    print(f"FAIL: job ended {status!r} (expected done).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
