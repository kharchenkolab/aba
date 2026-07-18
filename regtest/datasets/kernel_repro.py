"""Focused repro: persistent remote kernel stdout capture across many blocks.

The volume scenario (mn_repeat_sync, real mendel) saw per-block stdout go
SILENT from ~step 5 onward on one kernel while rc stayed 0 and earlier blocks
were fine. Drive the transport directly — no agent — and compare what
execute() returns per block against weft's own transcript, to locate the drop
(capture side vs read-back side).

Run: cd regtest/datasets && python kernel_repro.py
"""
from __future__ import annotations
import sys

import study  # noqa: F401 — throwaway home, backend importable, init_db

from core.compute import adapter as ad

st = ad.configure()
assert st["ok"], st["detail"]
r = ad.get_compute().sync_call(
    "register_site", "mendel", "ssh",
    {"root": "/home/pkharchenko/aba-krepro-weft", "host": "mendel"})
assert r.get("site") == "mendel", r
print("[repro] mendel registered")

from core import projects  # noqa: E402

projects.init()
projects.set_current(projects.create_project("krepro")["id"])

from core.exec.kernels import weft as wk  # noqa: E402

failures = []
sess = None
try:
    sess = wk.for_pool("repro@mendel", "python", cwd="/tmp", env_name=None,
                       site="mendel")
    print(f"[repro] kernel {sess.kernel_id} up on mendel")
    for i in range(1, 11):
        res = sess.execute(f"print('blk-{i} ok', {i}*7)", timeout_s=180)
        out = (res.stdout or "").strip()
        ok = res.returncode == 0 and f"blk-{i} ok {i * 7}" in out
        print(f"[repro] block {i}: rc={res.returncode} ok={ok} "
              f"out={out[:60]!r} err={(res.stderr or '')[:60]!r}", flush=True)
        if not ok:
            failures.append(i)
    # substrate-side view: what did weft itself capture?
    try:
        tr = ad.get_compute().sync_call("kernel_transcript", sess.kernel_id,
                                        last=30)
        print("[repro] weft transcript (block, rc, out head):")
        for row in tr:
            print("   ", row.get("block"), row.get("rc"),
                  str(row.get("out"))[:60])
    except Exception as e:  # noqa: BLE001
        print(f"[repro] transcript unavailable: {e}")
finally:
    try:
        if sess is not None:
            sess.shutdown()
    except Exception:  # noqa: BLE001
        pass
    try:
        ad.get_compute().sync_call("site_unregister", "mendel")
        print("[repro] site unregistered")
    except Exception as e:  # noqa: BLE001
        print(f"[repro] unregister: {e}")
    import subprocess
    subprocess.run(["ssh", "-o", "BatchMode=yes", "mendel",
                    "rm -rf /home/pkharchenko/aba-krepro-weft"], timeout=60)
    print("[repro] mendel dir cleaned")

print("RESULT:", "ALL BLOCKS CAPTURED" if not failures
      else f"stdout missing for blocks {failures}")
sys.exit(1 if failures else 0)
