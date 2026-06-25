"""Full-stack T7 (ondemand.md): a LIVE agent on a compute node backgrounds a job
that runs as a REAL Slurm job. Verifies the job was submitted to Slurm
(params.submitter == 'slurm' + a real slurm_id), the backend's own
_slurm_poll_loop finalizes it, and the continuation fires (status → done).

Runs INSIDE dev_c1 against a uvicorn started there with ABA_BATCH_SUBMITTER=slurm.
The job body is pure-Python (the el7 node can't load the host-built numpy).
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, "/home/pkharchenko/aba/aba")
os.environ.setdefault("ABA_BASE", "http://localhost:8077")
from tests.live_chat_runner import (  # noqa: E402
    BASE, server_reachable, _fresh_project, _fresh_thread, _consume_chat_sse,
)

PROMPT = ("Test the HPC background-job system. Use run_python with background=True "
          "to compute sum(i*i for i in range(2_000_000)) and print the result. "
          "Pure Python (no numpy). Run it directly as a background job — no plan needed.")


def _get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode())


def main() -> int:
    if not server_reachable():
        print("SKIP: server not reachable at", BASE)
        return 0
    pid = _fresh_project("t7-slurm")
    tid = _fresh_thread(pid, "t7")
    print(f"project={pid} thread={tid}")
    calls, text, halted, reason, events = _consume_chat_sse(pid, tid, PROMPT, max_turns=4)
    print("tool calls:", [c[0] for c in calls], "| halted:", halted, reason)

    job_id = None
    for ev in events:
        job_id = ev.get("job_id") or ev.get("deferred_id") or job_id
        res = ev.get("result") if isinstance(ev.get("result"), dict) else None
        if res:
            job_id = res.get("job_id") or res.get("deferred_id") or job_id
    if not job_id:
        jobs = _get("/api/jobs?limit=20")
        mine = [j for j in jobs if "background" in (j.get("title") or "").lower()]
        if mine:
            job_id = sorted(mine, key=lambda j: j.get("t", 0))[-1]["id"]
    if not job_id:
        print("FAIL: agent did not submit a background job. calls=", [c[0] for c in calls])
        return 1

    j = _get(f"/api/jobs/{job_id}")
    params = j.get("params") or {}
    print(f"job={job_id} submitter={params.get('submitter')} slurm_id={params.get('slurm_id')}")
    if params.get("submitter") != "slurm" or not params.get("slurm_id"):
        print(f"FAIL: job did not go to Slurm (submitter={params.get('submitter')})")
        return 1
    print("PASS: agent's background job was dispatched to REAL Slurm")

    # The backend's own _slurm_poll_loop finalizes it; wait for the row to settle.
    deadline = time.time() + 180
    st = None
    while time.time() < deadline:
        st = _get(f"/api/jobs/{job_id}")["status"]
        if st in ("done", "failed", "cancelled"):
            break
        time.sleep(3)
    print("final status:", st)
    if st == "done":
        print("log tail:", (_get(f"/api/jobs/{job_id}").get("log_tail") or "")[-120:].replace("\n", " "))
        print("PASS: full stack — live agent → REAL Slurm job → poll loop → done + continuation")
        return 0
    print(f"FAIL: job ended {st!r} (expected done)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
