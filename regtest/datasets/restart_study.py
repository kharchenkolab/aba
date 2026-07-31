"""Controller-restart live study (release_test_plan: 'Controller restart /
resume' row — the misc/bug1.md P0 class, previously unit-only).

A REAL, killable backend: uvicorn subprocess on a throwaway home (bootstrap
borrowed from study.py), driven over real HTTP. The scenario is the compressed
resume-days-later journey:

  1. agent submits a genuinely BACKGROUND local job (deferred contract),
  2. the controller is killed -9 mid-flight (job still running substrate-side),
  3. the controller restarts (fresh pid, same home): the jobs lease must be
     re-acquired, reconcile must adopt the surviving substrate task, the job
     must land DONE (never a false infra failure), and the deferred
     continuation must still deliver the true number to the thread.

`--site NAME` runs the SAME crash against a job on a CLUSTER site instead of a
local one. That combination was the untested cell: this study had the real kill
but a local job, while regtest's mn_restart_* scenarios have cluster jobs under a
SIMULATED outage (no poll pass, then reconcile_jobs()). Reconcile branches on
exactly that distinction — a local weft row is stamped `local_orphan_at` because
its supervisor died with the process, a remote-site row is deliberately left
untouched — so the remote branch under a REAL crash was covered nowhere.

Run:  python regtest/datasets/restart_study.py [--site hpc]
      (--site needs the docker slurm fixture: ABA_MN_CLUSTER_PORT + ABA_MN_KEYDIR)
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import study  # noqa: E402 — throwaway home, oauth bridge, drive_turn, harness

from study import REPO, drive_turn, tools_named, verify_jobs_truth  # noqa: E402
from multinode import wait_for_text, _thread_raw, _denum  # noqa: E402

import httpx  # noqa: E402

PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
PY = sys.executable


def start_backend() -> subprocess.Popen:
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(PORT)],
        cwd=str(REPO / "backend"), env=dict(os.environ),
        stdout=open(study._tmp / f"uvicorn_{int(time.time())}.log", "w"),
        stderr=subprocess.STDOUT)
    for _ in range(60):
        try:
            if httpx.get(f"{BASE}/api/health", timeout=3).status_code == 200:
                return proc
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1)
    proc.kill()
    sys.exit("[restart] backend did not become healthy")


def register_fixture_site() -> bool:
    """Register the docker slurm fixture as `hpc` IN THIS PROCESS.

    start_backend() passes `env=dict(os.environ)`, so the child uvicorn inherits
    ABA_HOME and therefore the same weft workspace — a site registered here is in
    weft's own state and `sites_list` shows it to the child. Same call multinode
    makes; no second fixture.
    """
    from multinode import _cluster_conn
    from core.compute import adapter as ad
    conn = _cluster_conn()
    if not conn:
        return False
    ad.configure()
    ad.get_compute().sync_call(
        "register_site", "hpc", "slurm",
        {"root": "/home/physicist/.weft", "host": "127.0.0.1",
         "port": conn["port"], "user": "physicist", "durable": True,
         "ssh_opts": ["-i", f"{conn['keydir']}/id_ed25519",
                      "-o", "StrictHostKeyChecking=no",
                      "-o", "UserKnownHostsFile=/dev/null",
                      "-o", "IdentitiesOnly=yes"]})
    return True


def jobs(client, pid):
    r = client.get(f"/api/jobs?project_id={pid}")
    rows = r.json() if r.status_code == 200 else []
    return rows if isinstance(rows, list) else rows.get("jobs", [])


def main() -> None:
    checks: list = []
    site = None
    if "--site" in sys.argv:
        site = sys.argv[sys.argv.index("--site") + 1]
        if not register_fixture_site():
            sys.exit("[restart] --site needs the docker fixture — set "
                     "ABA_MN_CLUSTER_PORT + ABA_MN_KEYDIR")
        print(f"[restart] site {site!r} registered; the crash will hit a "
              f"CLUSTER job, not a local one")
    proc = start_backend()
    client = httpx.Client(base_url=BASE, timeout=120)
    try:
        pid = client.post("/api/projects", json={"name": "ds-restart"}).json()["id"]
        client.post(f"/api/projects/{pid}/open")
        tid = client.post("/api/threads",
                          json={"project_id": pid, "title": "restart"}).json()["id"]

        expected = str(sum((i * 7) % 13 for i in range(1, 10001)))
        where = (f"on machine '{site}'" if site else "locally, no site")
        # env='system' when remote: this study is about CRASH RECOVERY, so the
        # base pack's platform must not decide the outcome (see the mn_restart_*
        # cases — requiring it made every fixture job fail on a mismatch first).
        env_hint = (" Use env='system' so no environment build is needed."
                    if site else "")
        caps = [drive_turn(client, pid, tid,
            f"Run a BACKGROUND job ({where}): sleep for 40 seconds, "
            f"then compute the sum of (i*7) mod 13 for i from 1 to 10000 and "
            f"print exactly RSTOTAL=<result>. Submit it in the background and "
            f"end your turn — I'll come back for the result.{env_hint}")]
        bg = [t for t in tools_named(caps, "run_python")
              if t["input"].get("background")]
        checks.append(("agent submitted a background job", bool(bg)))

        # wait for the row to be RUNNING substrate-side, then kill mid-flight
        # ARM: the job must be live SUBSTRATE-side before the kill, or "it
        # survived the crash" is a statement about nothing. Locally that means
        # status=running; on a cluster site the row can still read queued while
        # the task stages, so a dispatched task id counts as live there.
        running = None
        for _ in range(60):
            rows = [j for j in jobs(client, pid)
                    if j.get("status") in ("queued", "running")]
            hit = [j for j in rows if j.get("status") == "running"]
            if not hit and site:
                hit = [j for j in rows if (j.get("params") or {}).get("weft_id")]
            if hit:
                running = hit[0]
                break
            time.sleep(2)
        checks.append((f"job was live substrate-side before the kill "
                       f"({(running or {}).get('status') or 'none'})", bool(running)))
        if site:
            prm = (running or {}).get("params") or {}
            checks.append(("the killed-through job is on the CLUSTER lane, not local",
                           bool(prm.get("weft_site") or prm.get("site"))
                           and (prm.get("weft_site") or prm.get("site")) != "local"))

        os.kill(proc.pid, signal.SIGKILL)
        proc.wait(timeout=15)
        checks.append(("controller killed -9 mid-job", True))
        time.sleep(3)   # let the flock actually release

        proc = start_backend()
        checks.append(("controller restarted (fresh pid, same home)", True))

        # the surviving substrate task must be adopted and land DONE — honestly
        final = None
        t0 = time.time()
        while time.time() - t0 < 300:
            rows = jobs(client, pid)
            if rows and all(j.get("status") not in ("queued", "running")
                            for j in rows):
                final = rows
                break
            time.sleep(6)
        st = {j.get("id"): j.get("status") for j in (final or [])}
        checks.append(("job reached a terminal state after restart",
                       final is not None))
        checks.append(("terminal state is DONE, not a false infra failure",
                       bool(final) and all(s == "done" for s in st.values())))
        checks.append(("no done-row carries residual error",
                       bool(final) and not any(j.get("error") for j in final
                                               if j.get("status") == "done")))

        # deferred continuation must still deliver the true number
        full = wait_for_text(client, pid, tid, expected, timeout_s=240)
        checks.append(("continuation delivered the true RSTOTAL after restart",
                       _denum(expected) in _denum(full)))

        violations = verify_jobs_truth()
        checks.append(("jobs-vs-substrate truth sweep clean", not violations))
        for v in violations:
            checks.append((f"truth-sweep: {v}", False))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()

    print("\n== restart_recovery ==")
    for label, ok in checks:
        print(f"    {'✓' if ok else '✗'} {label}")
    ok = all(v for _, v in checks)
    print("RESTART STUDY:", "ALL PASS" if ok else "FAILURES")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
