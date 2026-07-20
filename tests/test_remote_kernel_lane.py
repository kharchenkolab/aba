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
        self.walltime_cap = None      # slurm-style cap, e.g. "1:00:00"
        self.partitions_hint = None   # override the violation's partition list
        self._block = 0
        self._block_out: dict[int, str] = {}

    def sync_call(self, name, *a, **kw):
        if name == "kernel_start":
            if self.fail_start:
                raise RuntimeError("site unreachable")
            if self.walltime_cap is not None:
                from core.compute.errors import ComputeError
                asked = kw.get("walltime") or ""
                import core.exec.kernels.weft as _wk
                if (_wk._slurm_time_s(asked) or 0) > \
                        _wk._slurm_time_s(self.walltime_cap):
                    raise ComputeError(
                        "site.capability_violation",
                        "kernel ask exceeds what hpc offers "
                        "(it would queue forever)", stage="submit",
                        hints={"partitions": {
                            "asked": {"walltime": asked},
                            "available": self.partitions_hint or [
                                {"name": "standard",
                                 "max_walltime": self.walltime_cap},
                                {"name": "short", "max_walltime": "1:00"}]}})
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
        return {"realizations": [{"site": "mendel", "state": "ready"},
                                 {"site": "hpc", "state": "ready"}]}


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
    starts0, execs0 = len(FAKE.kernel_starts), len(FAKE.execs)
    pool = poolm.KernelPool()
    s = pool.get_or_start("thr@mendel", "python", cwd=_TMP, env_name=None,
                          site="mendel")
    check("weft session built for the remote site",
          type(s).__name__ == "WeftKernelSession", type(s).__name__)
    st = FAKE.kernel_starts[starts0]
    check("kernel_start received the site", st["site"] == "mendel", str(st))
    check("frozen snapshot env attached",
          st.get("env_id") == "env_snapshot_1", str(st))
    check("remote setup binds DATA_DIR to the sandbox, not a controller path",
          len(FAKE.execs) > execs0 and "getcwd()" in FAKE.execs[execs0]
          and _TMP not in FAKE.execs[execs0])
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


def test_system_env_detached_keeps_node_interpreter():
    # detached (background) lane unchanged: env='system' = node interpreter
    import core.jobs.weft_submitter as ws
    sub = ws.WeftSubmitter(site="mendel")
    env_id, env_name = sub._detached_env({"env": "system"}, _PID, "python")
    check("system env → node interpreter (no env id, no name)",
          env_id is None and env_name is None, f"{env_id!r},{env_name!r}")
    env_id2, _ = sub._detached_env({"env": "None"}, _PID, "python")
    check("'none' spelling accepted too", env_id2 is None)


def test_bare_session_invariant():
    # 4a decoupling: a kernel attaches to a frozen env OR a live session OR
    # NEITHER (bare, env='system'); both at once is the only invalid shape.
    try:
        weftk.WeftKernelSession("k", "python", env_id="e1", session_id="s1")
        check("env_id + session_id together rejected", False)
    except ValueError:
        check("env_id + session_id together rejected", True)
    s = weftk.WeftKernelSession("kbare", "python", site="mendel")
    st = FAKE.kernel_starts[-1]
    check("bare start sends NEITHER env_id nor session_id",
          "env_id" not in st and "session_id" not in st, str(st))
    s.shutdown()


def test_system_env_gets_persistent_bare_kernel():
    """4a: env='system' on a remote step is a PERSISTENT session like any
    env — just bare (no realization, node interpreter). It must never touch
    named-env resolution/realization, and state must persist across calls."""
    import core.compute.named_envs as ne
    touched = []
    orig_resolve, orig_ready = ne.resolve, ne.ensure_ready
    ne.resolve = lambda *a, **k: (touched.append("resolve"), None)[1]
    ne.ensure_ready = lambda *a, **k: touched.append("ensure_ready")
    starts0 = len(FAKE.kernel_starts)
    try:
        ctx = {"thread_id": "thrSys"}
        r1 = rex._run_remote_kernel({"code": "n = 1", "env": "system"},
                                    ctx, _PID, "thrSys", "mendel")
        r2 = rex._run_remote_kernel({"code": "print(n+1)", "env": "system"},
                                    ctx, _PID, "thrSys", "mendel")
    finally:
        ne.resolve, ne.ensure_ready = orig_resolve, orig_ready
    check("both system-env calls ok",
          (r1 or {}).get("returncode") == 0 and (r2 or {}).get("returncode") == 0,
          f"{str(r1)[:120]} / {str(r2)[:120]}")
    check("ONE bare kernel serves both calls (state persists)",
          len(FAKE.kernel_starts) - starts0 == 1,
          str(FAKE.kernel_starts[starts0:]))
    st = FAKE.kernel_starts[starts0]
    check("kernel started BARE (no env_id / session_id attached)",
          "env_id" not in st and "session_id" not in st, str(st))
    check("no named-env resolution or realization happened",
          touched == [], str(touched))
    check("result labeled env=system", (r1 or {}).get("env") == "system",
          str((r1 or {}).get("env")))


def test_system_env_dispatch_enters_kernel_lane_with_one_shot_fallback():
    """run_python(site=…, env='system') routes through the kernel lane; a
    site with no kernel still degrades to the one-shot fresh-process lane."""
    hit = {}
    orig_sync = rex._run_remote_sync
    rex._run_remote_sync = lambda *a, **k: (hit.update(sync=True), {"status": "ok"})[1]
    FAKE.fail_start = True
    try:
        r = rex.run_python({"code": "x=1", "site": "mendel", "env": "system"},
                           {"thread_id": "tSfb"})
    finally:
        rex._run_remote_sync = orig_sync
        FAKE.fail_start = False
    check("kernel-less site degrades to one-shot for env=system",
          hit.get("sync") is True and (r or {}).get("status") == "ok", str(r))


def test_system_env_local_step_refused_clearly():
    r = rex.run_python({"code": "x=1", "env": "system"}, {"thread_id": "tSloc"})
    check("local env='system' refused with a placement hint",
          (r or {}).get("status") == "error"
          and "site" in (r or {}).get("note", ""), str(r)[:200])


def test_snapshot_platform_mismatch_relocks_base_pack():
    """Cross-platform site + DEFAULT (snapshot) env: ensure_ready's realize
    task fails env.platform_mismatch → the kernel lane must re-lock the BASE
    pack for the site's platform and start the session with the re-locked
    env (job-lane parity; found live on the aarch64 slurm fixture where the
    one-shot lane re-locked but the session lane fell back)."""
    import core.compute.named_envs as ne
    from core.compute.errors import ComputeError
    calls = []
    orig_ready = ne.ensure_ready
    orig_plat = getattr(base_env, "ensure_platform", None)

    def fake_ready(eid, **k):
        calls.append(eid)
        if eid == "env_snapshot_1":
            raise ComputeError(
                "env.platform_mismatch",
                "env is locked for ['linux-64', 'osx-arm64'] but site hpc "
                "is linux-aarch64", stage="realize",
                hints={"site_platform": "linux-aarch64"})

    ne.ensure_ready = fake_ready
    base_env.ensure_platform = lambda lang, plat: {"env_id": f"env_relock_{plat}"}
    try:
        pool = poolm.KernelPool()
        s = pool.get_or_start("thr_relock@hpc", "python", cwd=_TMP,
                              env_name=None, site="hpc")
        check("re-locked env attached after platform mismatch",
              FAKE.kernel_starts[-1].get("env_id")
              == "env_relock_linux-aarch64", str(FAKE.kernel_starts[-1]))
        check("ensure_ready ran again on the re-locked env",
              calls == ["env_snapshot_1", "env_relock_linux-aarch64"],
              str(calls))
        s.shutdown()
    finally:
        ne.ensure_ready = orig_ready
        if orig_plat is not None:
            base_env.ensure_platform = orig_plat


def test_layer_conflict_cross_platform_relocks_named_env():
    """F-ENV-2 (found live): an EXTENDED named env fails to realize on a
    DIFFERENT-platform site with env.layer_conflict (the delta was solved
    against the controller-platform parent) — the kernel lane must treat that
    like a platform mismatch and re-lock via ensure_platform (which re-solves
    base + layers for the site's platform), not fall back to one-shot."""
    import core.compute.named_envs as ne
    from core.compute.errors import ComputeError
    calls = []
    orig_resolve, orig_ready = ne.resolve, ne.ensure_ready
    orig_plat = getattr(ne, "ensure_platform", None)

    def fake_ready(eid, **k):
        calls.append(("ready", eid))
        if eid == "env_chain_osx":
            raise ComputeError(
                "env.layer_conflict",
                "the delta does not fit on this parent without moving base "
                "package versions", stage="solve")

    ne.resolve = lambda pid, name: ({"env_id": "env_chain_osx",
                                     "language": "python"}
                                    if name == "chained" else None)
    ne.ensure_ready = fake_ready
    ne.ensure_platform = lambda pid, name, plat: (
        calls.append(("relock", name, plat)) or {"env_id": "env_chain_linux"})

    # the fixture site reports a linux capability set (cross-platform vs the
    # darwin controller this test runs on)
    orig_sync = FAKE.sync_call

    def sync_with_describe(nm, *a, **kw):
        if nm == "sites_describe":
            return {"capabilities": {"os": "linux", "arch": "aarch64"}}
        return orig_sync(nm, *a, **kw)

    FAKE.sync_call = sync_with_describe
    try:
        pool = poolm.KernelPool()
        s = pool.get_or_start("thr_layer@hpc", "python", cwd=_TMP,
                              env_name="chained", site="hpc")
        check("layer_conflict on a cross-platform site triggered the re-lock",
              ("relock", "chained", "linux-aarch64") in calls, str(calls))
        check("session started on the RE-LOCKED env id",
              FAKE.kernel_starts[-1].get("env_id") == "env_chain_linux",
              str(FAKE.kernel_starts[-1]))
        s.shutdown()
    finally:
        FAKE.sync_call = orig_sync
        ne.resolve, ne.ensure_ready = orig_resolve, orig_ready
        if orig_plat is not None:
            ne.ensure_platform = orig_plat


def test_walltime_clamped_to_partition_cap():
    """Capped partitions (PartitionTimeLimit fence): kernel_start refusing
    the default 8h ask must trigger ONE retry clamped to the roomiest
    partition cap — an interactive session on a capped cluster starts
    instead of falling back to one-shot (found live: 1h-capped fixture)."""
    FAKE.walltime_cap = "1:00:00"
    try:
        pool = poolm.KernelPool()
        s = pool.get_or_start("thr_wall@hpc", "python", cwd=_TMP,
                              env_name=None, site="hpc")
        check("session started after walltime clamp",
              type(s).__name__ == "WeftKernelSession", type(s).__name__)
        check("clamped walltime equals the partition cap",
              FAKE.kernel_starts[-1].get("walltime") == "01:00:00",
              str(FAKE.kernel_starts[-1]))
        s.shutdown()
    finally:
        FAKE.walltime_cap = None


def test_walltime_clamps_conservative_not_roomiest():
    """PartitionTimeLimit wedge (chunk-A regression 2026-07-19): weft submits
    kernel allocations WITHOUT --partition (bug4), so slurm routes them to
    the cluster DEFAULT partition — clamping to the roomiest partition (gpu
    2h) produced a 2h ask on the 1h default partition: slurm ACCEPTS it and
    pends it forever while the node idles. Weft's hints carry NO default
    marker today (sinfo %R — bare names), so the clamp must fall back to
    the SMALLEST ≥10-min cap (guaranteed to start wherever slurm routes),
    never the roomiest, and tiny debug partitions must not poison it."""
    FAKE.walltime_cap = "1:00:00"
    FAKE.partitions_hint = [
        {"name": "standard", "max_walltime": "1:00:00"},
        {"name": "short", "max_walltime": "1:00"},
        {"name": "gpu", "max_walltime": "2:00:00"},
    ]
    try:
        pool = poolm.KernelPool()
        s = pool.get_or_start("thr_defpart@hpc", "python", cwd=_TMP,
                              env_name=None, site="hpc")
        check("session started after conservative clamp",
              type(s).__name__ == "WeftKernelSession", type(s).__name__)
        check("clamp is the smallest viable cap (1h), not gpu's 2h nor 1min",
              FAKE.kernel_starts[-1].get("walltime") == "01:00:00",
              str(FAKE.kernel_starts[-1]))
        s.shutdown()
    finally:
        FAKE.walltime_cap = None
        FAKE.partitions_hint = None


def test_walltime_explicit_default_flag_wins():
    """Future-proof: when weft ships a `default: true` partition marker
    (requested in the bug4 handoff), the clamp targets ITS cap even when a
    smaller viable partition exists."""
    FAKE.walltime_cap = "2:00:00"
    FAKE.partitions_hint = [
        {"name": "standard", "max_walltime": "1:00:00"},
        {"name": "batch", "max_walltime": "2:00:00", "default": True},
    ]
    try:
        pool = poolm.KernelPool()
        s = pool.get_or_start("thr_defflag@hpc", "python", cwd=_TMP,
                              env_name=None, site="hpc")
        check("clamp follows the flagged default partition (2h)",
              FAKE.kernel_starts[-1].get("walltime") == "02:00:00",
              str(FAKE.kernel_starts[-1]))
        s.shutdown()
    finally:
        FAKE.walltime_cap = None
        FAKE.partitions_hint = None


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
