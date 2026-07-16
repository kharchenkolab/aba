"""W-K0 reality check (kernels_to_weft.md §6, weft doctrine "fixtures lie;
reality is the test"): drive a REAL weft kernel through WeftKernelSession and
confirm the file-block protocol behaves as the fast test's fake assumes —
incremental `.out` growth, state persistence across blocks, SIGINT→rc=130 on
interrupt, and a nonzero rc on a failing block.

Opt-in: set ABA_WEFT_KERNEL_IT=1 (needs a configured weft workspace with pixi
and at least one realized local python env). Auto-skips otherwise so the default
suite never depends on a multi-minute realize or a live substrate.

Runs both under pytest and standalone:
    ABA_WEFT_KERNEL_IT=1 python tests/test_weft_kernel_integration.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_wk0it_rt_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

try:
    import pytest
    pytestmark = pytest.mark.platform
except ImportError:  # pragma: no cover
    pytest = None

_ENABLED = os.environ.get("ABA_WEFT_KERNEL_IT") == "1"


def _skip(msg: str):
    if pytest is not None:
        pytest.skip(msg, allow_module_level=False)
    raise SystemExit(f"SKIP: {msg}")


def _realized_python_env_id() -> str | None:
    """A realized local python EnvID (the bare env-dir hash — kernel_start's
    env_dir_rel strips any scheme, so the hash is a valid env_id)."""
    from core.compute import adapter as admod
    envs_dir = admod.weft_workspace() / "site-local" / "envs"
    if not envs_dir.exists():
        return None
    for d in sorted(envs_dir.iterdir()):
        if (d / ".weft-ready").exists() and (d / ".pixi" / "envs" / "default"
                                             / "bin" / "python").exists():
            return d.name
    return None


class _CaptureSink:
    def __init__(self):
        self.events = []

    def put_nowait(self, ev):
        self.events.append(ev)

    def chunks(self):
        return [e for e in self.events if e.get("type") == "chunk"]


class _Token:
    def __init__(self):
        self.run_id = "it"
        self.cancelled = False
        self._cbs = []

    def register(self, cb):
        self._cbs.append(cb)
        return lambda: self._cbs.remove(cb) if cb in self._cbs else None

    def cancel(self):
        self.cancelled = True
        for cb in list(self._cbs):
            cb()


def _session():
    from core.compute import adapter as admod
    import core.exec.kernels.weft as wmod
    st = admod.configure()
    if not st.get("ok"):
        _skip(f"weft substrate not configured: {st.get('detail')}")
    env_id = _realized_python_env_id()
    if not env_id:
        _skip("no realized local python env in the weft workspace")
    return wmod.WeftKernelSession("it-thread", "python", env_id=env_id, site="local")


def _python_env_canonical() -> str | None:
    """A realized python env's CANONICAL id (as the store lists it), so
    session_start resolves it. Falls back to the bare dir hash."""
    from core.compute import adapter as admod
    h = _realized_python_env_id()
    if not h:
        return None
    try:
        envs = admod.get_compute().sync_call("list_envs").get("envs", [])
    except Exception:  # noqa: BLE001
        return h
    for e in envs:
        eid = e.get("env_id") or e.get("id") or ""
        if eid.rsplit(":", 1)[-1] == h:
            return eid
    return h


def test_it_state_persists_and_streams():
    if not _ENABLED:
        _skip("set ABA_WEFT_KERNEL_IT=1 to run the real-weft kernel integration test")
    from core.runtime import progress
    s = _session()
    try:
        r = s.execute("secret = 40 + 2\nprint('val', secret)", timeout_s=60)
        assert r.returncode == 0, r
        assert "val 42" in r.stdout, r.stdout
        # state carries across blocks (the whole point of a kernel)
        r2 = s.execute("print('again', secret)", timeout_s=60)
        assert r2.returncode == 0 and "again 42" in r2.stdout, r2

        # incremental streaming: 8 rows at 300ms ≈ 2.4s spans several 0.5s
        # coalesce windows → the growing .out reaches the live sink in multiple
        # bursts, not one end-of-block dump (the proof the offset-tail streams).
        sink = _CaptureSink()
        progress.set_sink(sink)
        try:
            r3 = s.execute(
                "import time\n"
                "for i in range(8):\n"
                "    print('row', i, flush=True); time.sleep(0.3)\n",
                timeout_s=60)
        finally:
            progress.clear_sink()
        assert r3.returncode == 0, r3
        assert r3.stdout.count("row") == 8, r3.stdout
        streamed = "".join(c["text"] for c in sink.chunks())
        assert "row" in streamed, "live sink saw no output"
        assert len(sink.chunks()) >= 2, "output arrived in a single burst, not streamed"
    finally:
        s.shutdown()


def test_it_interrupt_and_failure():
    if not _ENABLED:
        _skip("set ABA_WEFT_KERNEL_IT=1 to run the real-weft kernel integration test")
    s = _session()
    try:
        tok = _Token()

        def _cancel_soon():
            time.sleep(0.6)
            tok.cancel()

        threading.Thread(target=_cancel_soon, daemon=True).start()
        r = s.execute("import time\ntime.sleep(30)\n", cancel_token=tok, timeout_s=40)
        assert r.cancelled is True, r
        assert s.alive, "session should survive SIGINT"
        # reusable after interrupt
        r2 = s.execute("print('post', 7 * 6)", timeout_s=60)
        assert r2.returncode == 0 and "post 42" in r2.stdout, r2
        # a failing block returns nonzero + the error text
        r3 = s.execute("raise RuntimeError('boom')", timeout_s=60)
        assert r3.returncode != 0, r3
        assert "boom" in (r3.stderr + r3.stdout), r3
    finally:
        s.shutdown()


def test_it_session_attach_live_install():
    """The W-K1 money property: a kernel on a LIVE session sees a
    session_install in its next block — no restart. This is what makes weft
    kernels a drop-in for aba's ensure_capability UX (vs. a frozen env, where
    an install would require snapshot + kernel_restart + replay)."""
    if not _ENABLED:
        _skip("set ABA_WEFT_KERNEL_IT=1 to run the real-weft kernel integration test")
    from core.compute import adapter as admod
    import core.exec.kernels.weft as wmod
    st = admod.configure()
    if not st.get("ok"):
        _skip(f"weft substrate not configured: {st.get('detail')}")
    base = _python_env_canonical()
    if not base:
        _skip("no realized local python env to clone a session from")
    ad = admod.get_compute()
    sess = ad.sync_call("session_start", base, "local")
    session_id = sess.get("session_id") or sess.get("id")
    assert session_id, sess
    s = wmod.WeftKernelSession("it-sess", "python", session_id=session_id, site="local")
    try:
        # package absent at start
        r0 = s.execute(
            "try:\n import cowsay; print('HAS')\n"
            "except Exception:\n print('MISSING')\n", timeout_s=60)
        assert r0.returncode == 0, r0
        assert "MISSING" in r0.stdout, f"cowsay unexpectedly already present: {r0.stdout}"
        # live install into the SAME session prefix the kernel is running
        ad.sync_call("session_install", session_id, pypi=["cowsay"])
        # visible in the next block after the same cache-invalidation nudge
        # aba's ensure_capability path applies — no kernel restart
        r1 = s.execute(
            "import importlib; importlib.invalidate_caches()\n"
            "import cowsay; print('NOW', cowsay.__name__)\n", timeout_s=120)
        assert r1.returncode == 0, r1
        assert "NOW cowsay" in r1.stdout, r1
        assert s.alive, "session kernel should survive a live install (no restart)"
    finally:
        s.shutdown()
        try:
            ad.sync_call("session_stop", session_id)
        except Exception:  # noqa: BLE001
            pass


def test_it_pool_weft_isolated_lane():
    """W-K1a: the KernelPool routes the isolated-env lane to a WeftKernelSession
    when ABA_WEFT_KERNELS is on, the setup cell chdir's the kernel into the aba
    scratch cwd (so a bare write lands where harvest looks — not the weft
    sandbox), and state persists across cells."""
    if not _ENABLED:
        _skip("set ABA_WEFT_KERNEL_IT=1 to run the real-weft kernel integration test")
    import tempfile
    from core.compute import adapter as admod
    from core.exec.kernels.pool import KernelPool
    import core.exec.kernels.weft as wmod
    import core.config as cfg
    from core.compute import named_envs

    st = admod.configure()
    if not st.get("ok"):
        _skip(f"weft substrate not configured: {st.get('detail')}")
    env_id = _realized_python_env_id()
    if not env_id:
        _skip("no realized local python env")

    # Route the isolated-env lane at a realized EnvID without needing a registered
    # named env: patch resolve → that EnvID, ensure_realized → no-op (already ready).
    import contextlib
    saved = (cfg.WEFT_KERNELS, named_envs.resolve, named_envs.ensure_realized)
    cfg.WEFT_KERNELS = True
    named_envs.resolve = lambda pid, name: {"env_id": env_id, "language": "python"}
    named_envs.ensure_realized = lambda eid, **k: None
    cwd = tempfile.mkdtemp(prefix="aba_wk1a_cwd_")
    pool = KernelPool(idle_ttl=10**9)
    try:
        s = pool.get_or_start("wk1a-iso", "python", cwd=cwd, env_name="iso-test")
        assert type(s).__name__ == "WeftKernelSession", f"expected weft session, got {type(s)}"
        assert s.work_dir, "weft session should expose its sandbox as work_dir"
        # NO chdir (would break the file-block protocol); a bare relative write
        # lands in the kernel sandbox (work_dir) — that's the local harvest source.
        r = s.execute("open('made_here.txt','w').write('ok')\nprint('WROTE', WORK_DIR is not None)",
                      timeout_s=60)
        assert r.returncode == 0, r
        assert "WROTE True" in r.stdout, r
        import os as _os
        assert _os.path.exists(_os.path.join(s.work_dir, "made_here.txt")), \
            "bare write did not land in the kernel sandbox (work_dir) — harvest bridge broken"
        # the run_exec change harvests from work_dir; confirm a produced figure there
        # is picked up (closes the harvest-from-sandbox loop end to end)
        s.execute("import matplotlib; matplotlib.use('Agg')\n"
                  "import matplotlib.pyplot as plt\nplt.plot([0,1,2],[0,1,4]); plt.savefig('fig.png')",
                  timeout_s=90)
        from core.exec.run import harvest_artifacts
        plots, tables, files, warns = harvest_artifacts(s.work_dir, since_ts=0.0)
        assert any("fig.png" in (p.get("original_name") or p.get("url") or "") for p in plots), \
            f"harvest of work_dir did not surface fig.png; plots={plots}"
        # state persists across cells
        r2 = s.execute("z = 6 * 7\nprint('z', z)", timeout_s=60)
        assert "z 42" in r2.stdout, r2
        r3 = s.execute("print('again', z)", timeout_s=60)
        assert "again 42" in r3.stdout, r3
    finally:
        pool.shutdown_all()
        cfg.WEFT_KERNELS, named_envs.resolve, named_envs.ensure_realized = saved


def test_it_pool_weft_default_lane():
    """W-K1b: the pool routes the DEFAULT lane (env_name=None) to a session-attached
    WeftKernelSession, and a session_install is visible to the running kernel through
    the pool — the live-install UX, end to end."""
    if not _ENABLED:
        _skip("set ABA_WEFT_KERNEL_IT=1 to run the real-weft kernel integration test")
    import tempfile
    from core.compute import adapter as admod
    from core.exec.kernels.pool import KernelPool
    import core.config as cfg
    from core.compute import project_env

    st = admod.configure()
    if not st.get("ok"):
        _skip(f"weft substrate not configured: {st.get('detail')}")
    base = _python_env_canonical()
    if not base:
        _skip("no realized local python env")
    ad = admod.get_compute()
    sess = ad.sync_call("session_start", base, "local")
    session_id = sess.get("session_id") or sess.get("id")
    assert session_id, sess

    saved = (cfg.WEFT_KERNELS, project_env.ensure)
    cfg.WEFT_KERNELS = True
    project_env.ensure = lambda pid, lang: {"session_id": session_id, "base_env_id": base}
    cwd = tempfile.mkdtemp(prefix="aba_wk1b_cwd_")
    pool = KernelPool(idle_ttl=10**9)
    try:
        s = pool.get_or_start("wk1b-default", "python", cwd=cwd, env_name=None)
        assert type(s).__name__ == "WeftKernelSession", f"expected weft session, got {type(s)}"
        assert s.session_id == session_id, "default lane should attach to the project session"
        r0 = s.execute("try:\n import humanize; print('HAS')\nexcept Exception:\n print('MISSING')",
                       timeout_s=60)
        assert "MISSING" in r0.stdout, f"humanize unexpectedly present: {r0.stdout}"
        ad.sync_call("session_install", session_id, pypi=["humanize"])
        r1 = s.execute("import importlib; importlib.invalidate_caches()\n"
                       "import humanize; print('NOW', humanize.__name__)", timeout_s=120)
        assert "NOW humanize" in r1.stdout, r1
        assert s.alive
    finally:
        pool.shutdown_all()
        cfg.WEFT_KERNELS, project_env.ensure = saved
        try:
            ad.sync_call("session_stop", session_id)
        except Exception:  # noqa: BLE001
            pass


def _standalone() -> int:
    if not _ENABLED:
        print("SKIP: set ABA_WEFT_KERNEL_IT=1 to run the real-weft integration test")
        return 0
    failures = []
    for t in (test_it_state_persists_and_streams, test_it_interrupt_and_failure,
              test_it_session_attach_live_install, test_it_pool_weft_isolated_lane,
              test_it_pool_weft_default_lane):
        try:
            t()
            print(f"  [PASS] {t.__name__}", flush=True)
        except SystemExit as e:
            print(f"  [SKIP] {t.__name__}: {e}", flush=True)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}", flush=True)
            failures.append(t.__name__)
    print(f"\n{'ok' if not failures else 'FAILURES: ' + ', '.join(failures)}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_standalone())
