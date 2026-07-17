"""The fully-remote / mixed-coordination EPIC at the data-plane level
(misc/datasets2.md; D5-L2): against a real (dockerized) slurm cluster —

  1. dataset downloaded FROM A URL directly onto the remote node
  2. compute round 1 on the cluster (input by ref, CAS staging)
  3. the intermediate KEPT on the cluster IN PLACE (retain.dir)
  4. compute round 2 on the cluster consuming the KEPT intermediate
  5. one small result synced back to the controller (guardrail-sized)
  6. a LOCAL compute round on the synced result
  7. back to the cluster: a round consuming the locally-produced ref
     (bytes move site-ward automatically)
  8. memo: repeating round 1 is a cache hit, not a recompute

A 16-hex checksum minted in round 1 is threaded through every hop and
asserted at the end — data integrity across url→hpc→keep→hpc→home→local
→hpc. Controller state lives in a throwaway ABA_HOME; the container is
removed on exit.

Run (docker via orbstack):  python regtest/datasets/epic_mechanism.py
"""
from __future__ import annotations

import http.server
import os
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
_tmp = tempfile.mkdtemp(prefix="aba_epic_")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ABA_HOME"] = str(Path(_tmp) / "home")
sys.path.insert(0, str(REPO / "backend"))

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILS.append(name)


def sh(*a, timeout=600):
    return subprocess.run(list(a), capture_output=True, text=True, timeout=timeout)


# ── the "public data portal" (local http, reachable from the container) ──────
www = Path(_tmp) / "www"
www.mkdir(parents=True)
(www / "cohort.bin").write_bytes(os.urandom(8_000_000))


class _H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(www), **k)

    def log_message(self, *a):
        pass


srv = socketserver.TCPServer(("0.0.0.0", 0), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://host.docker.internal:{srv.server_address[1]}/cohort.bin"

# ── the cluster ──────────────────────────────────────────────────────────────
import weft as _weft  # noqa: E402

WEFT_REPO = Path(_weft.__file__).resolve().parents[2]
keydir = Path(tempfile.mkdtemp(prefix="epic_keys_"))
assert sh("sh", str(WEFT_REPO / "tests/fixtures/slurm/build.sh"),
          str(keydir)).returncode == 0
NAME = f"aba-epic-{uuid.uuid4().hex[:6]}"
assert sh("docker", "run", "-d", "--rm", "--name", NAME,
          "--device", "/dev/fuse", "--cap-add", "SYS_ADMIN",
          "--add-host", "host.docker.internal:host-gateway",
          "--hostname", "weftslurm", "-p", "127.0.0.1::22",
          "weft-test-slurm").returncode == 0
PORT = sh("docker", "port", NAME, "22").stdout.strip().rsplit(":", 1)[-1]
KEY = str(keydir / "id_ed25519")
SSH_OPTS = ["-i", KEY, "-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null", "-o", "IdentitiesOnly=yes"]
for _ in range(120):
    ok = sh("ssh", *SSH_OPTS, "-o", "BatchMode=yes", "-p", PORT,
            "physicist@127.0.0.1", "sinfo -h -o %a | head -1")
    if ok.returncode == 0 and "up" in ok.stdout.lower():
        break
    time.sleep(0.5)
else:
    sh("docker", "rm", "-f", NAME)
    sys.exit("cluster never became ready")

from core.compute import adapter as ad  # noqa: E402

st = ad.configure()
assert st["ok"], st
comp = ad.get_compute()


def wait(jid, n=600):
    for _ in range(n):
        s = comp.sync_call("task_status", jid)[0]
        if s["state"] in ("DONE", "FAILED", "CANCELLED"):
            return s
        time.sleep(0.5)
    raise AssertionError(f"timeout waiting on {jid}")


def tail(jid):
    try:
        return comp.sync_call("task_result", jid)["logs"]["tail"]
    except Exception:  # noqa: BLE001
        return ""


def retained_row(label, n=240):
    """Wait for a retain (background transfer/link) to settle."""
    for _ in range(n):
        rows = comp.sync_call("retained_runs", label=label)
        if rows and rows[0].get("state") == "done":
            return rows[0]
        time.sleep(0.5)
    raise AssertionError(f"retain {label!r} never settled")


try:
    r = comp.sync_call(
        "register_site", "hpc", "slurm",
        {"root": "/home/physicist/.weft", "host": "127.0.0.1",
         "port": int(PORT), "user": "physicist", "ssh_opts": SSH_OPTS,
         "modules_init": "export MODULEPATH=/opt/site-modules",
         "retain": {"dir": "/home/physicist/keeps"}})
    check("0. cluster registered (with an in-place retain.dir)",
          r.get("site") == "hpc")

    # 1 ── URL → straight into the CLUSTER's CAS
    r1 = comp.sync_call("data_register", URL, site="hpc")
    ref_raw = r1["ref"]
    local_cas = ad.weft_workspace() / ".weft" / "cas"
    n_local = sum(1 for p in local_cas.rglob("*") if p.is_file()) \
        if local_cas.exists() else 0
    check("1. URL fetched onto the cluster, controller untouched",
          ref_raw.startswith("dref:") and r1.get("bytes") == 8_000_000
          and n_local <= 1, f"local CAS files: {n_local}")

    # 2 ── round 1 on the cluster
    t1 = comp.sync_call("task_submit", {
        "command": ("mkdir -p out && "
                    "sha256sum raw | cut -c1-16 > out/inter.txt && "
                    "cat out/inter.txt"),
        "site": "hpc", "label": "epic-round1",
        "inputs": [{"ref": ref_raw, "mount_as": "raw"}]})
    jid1 = t1["job_id"]
    s = wait(jid1)
    checksum = tail(jid1).strip().splitlines()[-1].strip()
    check("2. remote round 1 (ref-staged input)",
          s["state"] == "DONE" and len(checksum) == 16, f"sha16={checksum}")

    # 3 ── KEEP the intermediate on the cluster, in place
    k1 = comp.sync_call("run_retain", jid1, label="epic-keep1",
                        background=False)
    row = retained_row("epic-keep1")
    check("3. intermediate kept IN PLACE on the cluster",
          bool(row.get("in_place")) and row.get("site") == "hpc",
          f"location={row.get('location')}")

    # 3b ── the kept file becomes a ref (durable-home = retained tree)
    kept_inter = f"{row['location'].rstrip('/')}/out/inter.txt"
    r2 = comp.sync_call("data_register", kept_inter, site="hpc",
                        ingest=False)
    ref_inter = r2["ref"]
    check("3b. kept path registered in place (run-lineage home)",
          ref_inter.startswith("dref:"),
          f"home={r2.get('external_home', '?')}")

    # 4 ── round 2 on the cluster, consuming the KEPT intermediate
    t2 = comp.sync_call("task_submit", {
        "command": ("mkdir -p out && "
                    "printf 'summary: %s rounds=2\\n' \"$(cat inter)\" "
                    "> out/summary.txt && cat out/summary.txt"),
        "site": "hpc", "label": "epic-round2",
        "inputs": [{"ref": ref_inter, "mount_as": "inter"}]})
    jid2 = t2["job_id"]
    s2 = wait(jid2)
    check("4. remote round 2 consumed the kept intermediate",
          s2["state"] == "DONE" and checksum in tail(jid2))

    comp.sync_call("run_retain", jid2, label="epic-keep2", background=False)
    row2 = retained_row("epic-keep2")

    # 5 ── sync ONE small result home
    ref_sum = comp.sync_call(
        "data_register", f"{row2['location'].rstrip('/')}/out/summary.txt",
        site="hpc", ingest=False)["ref"]
    home_copy = Path(_tmp) / "home-copy"
    f = comp.sync_call("data_fetch", ref_sum, str(home_copy))
    fetched = (home_copy if home_copy.is_file()
               else next(home_copy.rglob("summary.txt"), None))
    txt = fetched.read_text() if fetched else ""
    check("5. small result synced home", checksum in txt,
          f"{len(txt)} bytes at controller")

    # 6 ── LOCAL round on the synced result
    t3 = comp.sync_call("task_submit", {
        "command": ("mkdir -p out && "
                    "printf 'local-verified %s\\n' \"$(cat sum)\" "
                    "> out/verdict.txt && cat out/verdict.txt"),
        "site": "local", "label": "epic-local",
        "inputs": [{"ref": ref_sum, "mount_as": "sum"}]})
    jid3 = t3["job_id"]
    s3 = wait(jid3)
    check("6. local round on the synced result",
          s3["state"] == "DONE" and checksum in tail(jid3))

    comp.sync_call("run_retain", jid3, label="epic-keep3", background=False)
    row3 = retained_row("epic-keep3")
    ref_verdict = comp.sync_call(
        "data_register", f"{row3['location'].rstrip('/')}/out/verdict.txt")["ref"]

    # 7 ── BACK to the cluster with the locally-produced ref
    t4 = comp.sync_call("task_submit", {
        "command": "cat v && printf 'roundtrip-complete\\n'",
        "site": "hpc", "label": "epic-round3",
        "inputs": [{"ref": ref_verdict, "mount_as": "v"}]})
    jid4 = t4["job_id"]
    s4 = wait(jid4)
    check("7. remote round on locally-produced bytes (auto site-ward move)",
          s4["state"] == "DONE" and checksum in tail(jid4)
          and "roundtrip-complete" in tail(jid4))

    # 8 ── memoization: identical round-1 resubmit is a cache hit
    m = comp.sync_call("task_submit", {
        "command": ("mkdir -p out && "
                    "sha256sum raw | cut -c1-16 > out/inter.txt && "
                    "cat out/inter.txt"),
        "site": "hpc", "label": "epic-round1",
        "inputs": [{"ref": ref_raw, "mount_as": "raw"}]})
    check("8. identical remote round memoized (no recompute)",
          m.get("memoized") is True and m.get("job_id") == jid1)

except Exception as e:  # noqa: BLE001
    import traceback
    traceback.print_exc()
    FAILS.append(f"exception: {e}")
finally:
    sh("docker", "rm", "-f", NAME)
    srv.shutdown()
    ad.shutdown()

print("\nEPIC:", "ALL PASS" if not FAILS else f"FAILURES: {FAILS}")
sys.exit(1 if FAILS else 0)
