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
    import time as _t
    sess = wk.for_pool("repro@mendel", "python", cwd="/tmp", env_name=None,
                       site="mendel")
    print(f"[repro] kernel {sess.kernel_id} up on mendel")
    kid = sess.kernel_id
    comp = ad.get_compute()
    for i in range(1, 11):
        # drive the raw protocol — isolates the peek/read path completely
        sub = comp.sync_call("kernel_exec", kid, f"print('blk-{i} ok', {i}*7)",
                             wait=False)
        blk = int(sub.get("block", 0))
        out, off, rc, done = "", 0, None, False
        deadline = _t.time() + 120
        while _t.time() < deadline and not done:
            pk = comp.sync_call("kernel_peek", kid, blk, out_offset=off,
                                err_offset=0)
            if pk.get("out_delta"):
                out += pk["out_delta"]; off = pk.get("out_offset", off)
            if not pk.get("running", True):
                rc = pk.get("rc"); done = True
            else:
                _t.sleep(0.2)
        ok = rc == 0 and f"blk-{i} ok {i * 7}" in out
        print(f"[repro] block {i} (blk={blk}): rc={rc} ok={ok} "
              f"out={out.strip()[:50]!r}", flush=True)
        if not ok:
            failures.append(i)
            # eventually-visible (slow shim) or never-readable?
            for delay in (5, 15):
                _t.sleep(delay)
                pk = comp.sync_call("kernel_peek", kid, blk,
                                    out_offset=0, err_offset=0)
                print(f"[repro]   +{delay}s re-peek blk={blk}: "
                      f"running={pk.get('running')} rc={pk.get('rc')} "
                      f"out={str(pk.get('out_delta'))[:50]!r}", flush=True)
    # substrate-side view: what did weft itself capture?
    try:
        tr = comp.sync_call("kernel_transcript", kid, last=30)
        print("[repro] weft transcript rows:")
        for row in tr:
            print("   ", repr(row)[:200])
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
