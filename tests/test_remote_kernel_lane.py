"""Remote kernel lane (P1, misc/bug1.md): run_python(site=<remote>) holds a
persistent interpreter ON the site via weft's kernel protocol.

Guards:
  1. pool.get_or_start(site=...) builds a weft session with the site passed to
     kernel_start — and NEVER falls back to the local jupyter transport for a
     remote site (that would silently run "remote" code locally).
  2. State persists: two _run_remote_kernel calls reuse ONE kernel (weft
     kernel_start called once; both blocks executed on it).
  3. New small sandbox files are fetched over the data plane and land locally;
     oversized ones are reported remote-only, not silently dropped.
  4. A kernel that cannot start → None (the run tool falls back to the
     one-shot sync lane); an established kernel's errors are returned, not
     fallen through.

Run: python tests/test_remote_kernel_lane.py
"""
from __future__ import annotations
import base64
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_rkern_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_PROJECTS_DIR"] = _TMP + "/projects"
os.environ.pop("ABA_DB_PATH", None)
_BACKEND = str(Path(__file__).resolve().parents[1] / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from core import projects  # noqa: E402

projects.init()
_PID = projects.create_project("RemoteKernel")["id"]
projects.set_current(_PID)

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


class FakeWeft:
    """kernel_* + run_inventory/run_file_read over an in-memory sandbox."""

    def __init__(self):
        self.kernel_starts: list[dict] = []
        self.execs: list[str] = []
        self.sandbox: dict[str, bytes] = {}     # rel -> content
        self._mtime = 100.0
        self.fail_start = False
        self._block = 0
        self._block_out: dict[int, str] = {}

    def sync_call(self, name, *a, **kw):
        if name == "kernel_start":
            if self.fail_start:
                raise RuntimeError("site unreachable")
            self.kernel_starts.append({"site": a[0], "lang": a[1], **kw})
            return {"kernel_id": "krn_test1"}
        if name == "kernel_exec":
            self.execs.append(a[1])
            self._block += 1
            # "run" the code: a marker line as output; a code containing
            # WRITE:<name>:<size> drops a sandbox file
            out = f"ok block {self._block}"
            for line in a[1].splitlines():
                if line.startswith("WRITE:"):
                    _, rel, size = line.split(":")
                    self.sandbox[rel] = b"x" * int(size)
                    self._mtime += 1
            self._block_out[self._block] = out
            return {"block": self._block}
        if name == "kernel_peek":
            blk = a[1]
            txt = self._block_out.get(blk, "")
            off = kw.get("out_offset", 0)
            delta = txt[off:]
            return {"out_delta": delta, "out_offset": off + len(delta),
                    "err_delta": "", "err_offset": 0, "running": False, "rc": 0}
        if name == "kernel_status":
            return {"state": "running"}
        if name == "kernel_stop":
            return {}
        if name == "run_inventory":
            return {"entries": [{"path": rel, "bytes": len(b), "mtime": self._mtime}
                                for rel, b in self.sandbox.items()], "live": True}
        if name == "run_file_stat":
            rel = a[1]
            b = self.sandbox.get(rel)
            return {"exists": b is not None, "bytes": len(b or b"")}
        if name == "run_file_read":
            rel = a[1]
            b = self.sandbox.get(rel, b"")
            cap = kw.get("max_bytes", 1 << 20)
            if len(b) > cap:
                return {"truncated": True}
            return {"bytes_b64": base64.b64encode(b).decode(), "truncated": False}
        raise RuntimeError(f"unexpected call {name}")


FAKE = FakeWeft()


class _FakeCompute:
    def sync_call(self, name, *a, **kw):
        return FAKE.sync_call(name, *a, **kw)

    def env_status(self, env_id):
        # remote pre-realization check (kernel_start refuses an env not
        # realized on its site): report ready-on-site so no realize task runs
        return {"realizations": [{"site": "mendel", "state": "ready"}]}


# patch the compute port at its sources (call-time imports)
import core.compute.adapter as adapter  # noqa: E402

adapter.get_compute = lambda: _FakeCompute()
adapter.run_sync = lambda v: v          # fake port methods return plain values

# frozen-env identity for the remote lane: avoid real pack machinery
import core.compute.base_env as base_env  # noqa: E402
import core.compute.project_env as project_env  # noqa: E402

base_env.require = lambda lang: None
project_env.snapshot = lambda pid, lang: "env_snapshot_1"

import core.exec.kernels.weft as weftk  # noqa: E402
import core.exec.kernels.pool as poolm  # noqa: E402
import content.bio.tools.run_exec as rex  # noqa: E402


def test_pool_remote_uses_weft_never_jupyter():
    pool = poolm.KernelPool()
    s = pool.get_or_start("thr@mendel", "python", cwd=_TMP, env_name=None,
                          site="mendel")
    check("weft session built for the remote site",
          type(s).__name__ == "WeftKernelSession", type(s).__name__)
    check("kernel_start received the site",
          FAKE.kernel_starts and FAKE.kernel_starts[0]["site"] == "mendel",
          str(FAKE.kernel_starts))
    check("frozen snapshot env attached",
          FAKE.kernel_starts[0].get("env_id") == "env_snapshot_1",
          str(FAKE.kernel_starts[0]))
    check("remote setup binds DATA_DIR to the sandbox, not a controller path",
          FAKE.execs and "getcwd()" in FAKE.execs[0] and _TMP not in FAKE.execs[0])
    s.shutdown()


def test_pool_remote_start_failure_raises_not_jupyter():
    FAKE.fail_start = True
    pool = poolm.KernelPool()
    try:
        pool.get_or_start("thr2@mendel", "python", cwd=_TMP, env_name=None,
                          site="mendel")
        check("remote start failure raises (no silent local fallback)", False)
    except Exception as e:  # noqa: BLE001
        check("remote start failure raises (no silent local fallback)",
              "jupyter" not in str(type(e)).lower(), str(e))
    finally:
        FAKE.fail_start = False


def test_run_remote_kernel_state_persists_and_fetches():
    # own thread scope: the pool is process-global, other tests hold sessions
    starts0, execs0 = len(FAKE.kernel_starts), len(FAKE.execs)
    ctx = {"thread_id": "thrP"}
    r1 = rex._run_remote_kernel({"code": "a = 41"}, ctx, _PID, "thrP", "mendel")
    check("first call ok", r1 is not None and r1.get("returncode") == 0, str(r1)[:200])
    r2 = rex._run_remote_kernel({"code": "WRITE:result.txt:64\nprint(a+1)"},
                                ctx, _PID, "thrP", "mendel")
    check("second call ok", r2 is not None and r2.get("returncode") == 0, str(r2)[:200])
    check("ONE kernel serves both calls (state persists)",
          len(FAKE.kernel_starts) - starts0 == 1,
          str(len(FAKE.kernel_starts) - starts0))
    ran = FAKE.execs[execs0:]
    check("both cells executed in that one kernel",
          any("a = 41" in c for c in ran) and any("WRITE:result.txt" in c for c in ran),
          str(len(ran)))
    check("mode says remote session", r2.get("execution_mode") == "remote-session")
    # the new small file was fetched locally for harvest
    fetched = []
    for root, _d, fns in os.walk(_TMP):
        fetched += [os.path.join(root, f) for f in fns if f == "result.txt"]
    check("new sandbox file fetched over the data plane", len(fetched) >= 1,
          str(fetched))


def test_oversized_output_stays_remote_but_is_reported():
    ctx = {"thread_id": "thrX"}
    big = rex._REMOTE_KERNEL_FETCH_BYTES + 1
    r = rex._run_remote_kernel({"code": f"WRITE:huge.bin:{big}\nprint('d')"},
                               ctx, _PID, "thrX", "mendel")
    check("call ok", r is not None and r.get("returncode") == 0, str(r)[:200])
    check("oversized output reported as staying on the site",
          "huge.bin" in (r.get("note") or ""), (r or {}).get("note", "")[:200])


def test_start_failure_falls_back_to_one_shot():
    FAKE.fail_start = True
    try:
        r = rex._run_remote_kernel({"code": "x=1"}, {"thread_id": "thrY"},
                                   _PID, "thrY", "mendel")
        check("no session → None (one-shot fallback signal)", r is None, str(r))
    finally:
        FAKE.fail_start = False


def test_system_env_skips_kernel_and_realization():
    # P2 lever: env='system' = node interpreter, no pack realization, no kernel
    import core.jobs.weft_submitter as ws
    sub = ws.WeftSubmitter(site="mendel")
    env_id, env_name = sub._detached_env({"env": "system"}, _PID, "python")
    check("system env → node interpreter (no env id, no name)",
          env_id is None and env_name is None, f"{env_id!r},{env_name!r}")
    env_id2, _ = sub._detached_env({"env": "None"}, _PID, "python")
    check("'none' spelling accepted too", env_id2 is None)
    sent = {}
    orig_sync, orig_kern = rex._run_remote_sync, rex._run_remote_kernel
    rex._run_remote_sync = lambda *a, **k: (sent.update(hit=True), {"status": "ok"})[1]
    rex._run_remote_kernel = lambda *a, **k: (_failures.append("kernel lane entered for env=system"), None)[1]
    try:
        r = rex.run_python({"code": "x=1", "site": "mendel", "env": "system"},
                           {"thread_id": "tS"})
    finally:
        rex._run_remote_sync = orig_sync
        rex._run_remote_kernel = orig_kern
    check("env=system routes straight to the one-shot lane",
          sent.get("hit") is True and (r or {}).get("status") == "ok", str(r))


def _run():
    import traceback
    fails = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
        except Exception:  # noqa: BLE001
            fails += 1
            print(f"  [FAIL] {name} raised:")
            traceback.print_exc()
    print(f"\n{'ALL PASS' if not (fails or _failures) else f'FAILED ({fails + len(_failures)})'}")
    return 1 if (fails or _failures) else 0


if __name__ == "__main__":
    raise SystemExit(_run())
