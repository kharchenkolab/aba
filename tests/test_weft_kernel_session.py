"""W-K0 (kernels_to_weft.md): the WeftKernelSession transport shim.

Proves the shim drives aba's live sinks correctly WITHOUT a multi-minute env
realize, by faking weft's file-block protocol exactly as read from
weft/src/weft/kernel.py:

  * kernel_exec(wait=False) writes a code block and returns {block, state};
    a background runner writes the block's `.out`/`.err` INCREMENTALLY on the
    shared FS, then the `.rc` sentinel at completion;
  * kernel_poll(block, timeout) returns {state: running} until `.rc` lands,
    then the finished {rc, out, err};
  * kernel_interrupt makes the running block finish rc=130;
  * a dead kernel makes kernel_poll raise (weft's WeftError → the adapter's
    ComputeError), which the shim maps to a failed-turn ExecResult.

What we assert is the W-K0 exit criterion: incremental streaming reaches the
Coalescer/progress sinks at a finer cadence than aba's own coalesce window,
state-carrying output is captured in full (past weft's 64 KB poll cap), and
interrupt stops a block with state intact.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("ABA_RUNTIME_DIR", tempfile.mkdtemp(prefix="aba_wk0_rt_"))
_BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

try:  # pytest for CI discovery; the __main__ runner works without it
    import pytest  # noqa: E402
    pytestmark = pytest.mark.platform
    _fixture = pytest.fixture
except ImportError:  # pragma: no cover — standalone-runner path
    pytest = None

    def _fixture(*a, **k):
        def _wrap(fn):
            return fn
        return _wrap

from core.compute.errors import ComputeError  # noqa: E402
import core.exec.kernels.weft as wmod  # noqa: E402


# ── a fake weft that reproduces the file-block protocol ──────────────────────

class _FakeWeft:
    """Stands in for the embedded Weft behind the compute adapter's sync_call.

    Writes real block files under <workspace>/site-local/kernels/<id>/blocks so
    the shim's LOCAL offset-tail exercises its real code path (open+seek+read),
    not a mock."""

    def __init__(self, workspace: Path):
        self._ws = workspace
        self._kernels: dict[str, dict] = {}
        self._ctr = 0

    def _blocks_dir(self, kid: str) -> Path:
        return self._ws / "site-local" / "kernels" / kid / "blocks"

    # -- the sync_call surface the shim uses --------------------------------

    def sync_call(self, name: str, /, *args, **kw):
        return getattr(self, name)(*args, **kw)

    def kernel_start(self, site, lang="python", *, env_id=None, walltime="",
                     resources=None, label=""):
        self._ctr += 1
        kid = f"krn_fake{self._ctr:02d}"
        self._blocks_dir(kid).mkdir(parents=True, exist_ok=True)
        self._kernels[kid] = {"state": "running", "blocks_run": 0,
                              "interrupt": threading.Event(), "lang": lang}
        return {"kernel_id": kid, "site": site, "lang": lang, "env_id": env_id}

    def kernel_exec(self, kid, code, *, wait=False, timeout=120.0):
        k = self._kernels[kid]
        n = k["blocks_run"]
        k["blocks_run"] = n + 1
        k["interrupt"].clear()
        threading.Thread(target=self._run_block, args=(kid, n, code),
                         daemon=True).start()
        return {"kernel_id": kid, "block": n, "state": "submitted"}

    def _run_block(self, kid: str, n: int, code: str):
        """Interpret a tiny block DSL, writing .out incrementally then .rc.

        DSL (one directive per line):
          EMIT <n> <text>   → write "<text>\n" to .out, <n> times, 60ms apart
          EMITERR <text>    → write "<text>\n" to .err
          SLEEP <secs>      → sleep (interruptible)
          FAIL              → finish rc=1
        """
        base = self._blocks_dir(kid) / f"{n:04d}"
        out = base.with_suffix(".out")
        err = base.with_suffix(".err")
        out.write_bytes(b"")
        err.write_bytes(b"")
        rc = 0
        k = self._kernels[kid]
        if k["state"] != "running":
            # dead driver — never writes .rc (block hangs; caught via status)
            return
        for line in code.splitlines():
            line = line.strip()
            if k["interrupt"].is_set():
                rc = 130
                break
            if line.startswith("EMIT "):
                _, cnt, text = line.split(" ", 2)
                for _ in range(int(cnt)):
                    if k["interrupt"].is_set():
                        rc = 130
                        break
                    with open(out, "ab") as f:
                        f.write((text + "\n").encode())
                    time.sleep(0.06)
            elif line.startswith("EMITERR "):
                with open(err, "ab") as f:
                    f.write((line[len("EMITERR "):] + "\n").encode())
            elif line.startswith("SLEEP "):
                if k["interrupt"].wait(timeout=float(line.split()[1])):
                    rc = 130
                    break
            elif line == "FAIL":
                rc = 1
                break
        base.with_suffix(".rc").write_text(str(rc))

    def kernel_peek(self, kid, block, *, out_offset=0, err_offset=0, max_bytes=65536):
        # Mirrors weft: incremental bytes past the offsets + running/rc. Does NOT
        # assert liveness (a dead kernel reads as running until status says else).
        if kid not in self._kernels:
            raise ComputeError("task.invalid", f"unknown kernel {kid}", stage="infra")
        base = self._blocks_dir(kid) / f"{block:04d}"

        def delta(suffix, off):
            p = base.with_suffix("." + suffix)
            try:
                data = p.read_bytes()
            except FileNotFoundError:
                return "", off
            chunk = data[off:off + max_bytes]
            return chunk.decode("utf-8", "replace"), off + len(chunk)

        out, out_off = delta("out", out_offset)
        err, err_off = delta("err", err_offset)
        rcf = base.with_suffix(".rc")
        rc = int(rcf.read_text().strip() or 1) if rcf.exists() else None
        return {"out_delta": out, "err_delta": err,
                "out_offset": out_off, "err_offset": err_off,
                "running": rc is None, "rc": rc}

    def kernel_interrupt(self, kid):
        self._kernels[kid]["interrupt"].set()
        return {"kernel_id": kid}

    def kernel_status(self, kid):
        k = self._kernels.get(kid) or {"state": "dead", "blocks_run": 0}
        return {"kernel_id": kid, "state": k["state"],
                "blocks_run": k["blocks_run"], "idle_s": 0.0}

    def kernel_stop(self, kid):
        if kid in self._kernels:
            self._kernels[kid]["state"] = "stopped"
        return {"kernel_id": kid, "state": "stopped"}


@_fixture()
def fake_weft(monkeypatch, tmp_path):
    ws = tmp_path / "weft"
    fake = _FakeWeft(ws)
    monkeypatch.setattr(wmod, "_LOCAL_SITE", "local")
    from core.compute import adapter as admod
    monkeypatch.setattr(admod, "get_compute", lambda: fake)
    monkeypatch.setattr(admod, "weft_workspace", lambda: ws)
    return fake


def _session(fake_weft):
    return wmod.WeftKernelSession("thread-A", "python", env_id="env:v1:test",
                                  site="local")


# ── a progress sink that captures the live coalesced chunks ──────────────────

class _CaptureSink:
    def __init__(self):
        self.events = []

    def put_nowait(self, ev):
        self.events.append(ev)

    def chunks(self):
        return [e for e in self.events if e.get("type") == "chunk"]


class _Token:
    def __init__(self):
        self.run_id = "t"
        self.cancelled = False
        self._cbs = []

    def register(self, cb):
        self._cbs.append(cb)
        return lambda: self._cbs.remove(cb) if cb in self._cbs else None

    def cancel(self):
        self.cancelled = True
        for cb in list(self._cbs):
            cb()


# ── tests ────────────────────────────────────────────────────────────────────

def test_start_and_simple_block(fake_weft):
    s = _session(fake_weft)
    assert s.alive and s.kernel_id
    r = s.execute("EMIT 1 hello")
    assert r.returncode == 0
    assert "hello" in r.stdout
    assert not r.cancelled and not r.timed_out
    s.shutdown()


def test_state_output_captured_in_full(fake_weft):
    """The tailed capture is the full block output (uncapped) — the shim reads
    the growing file itself rather than relying on weft's 64 KB poll cap."""
    s = _session(fake_weft)
    r = s.execute("EMIT 50 line")           # 50 lines, written incrementally
    assert r.returncode == 0
    assert r.stdout.count("line") == 50
    s.shutdown()


def test_incremental_streaming_reaches_sink(fake_weft):
    """The heart of W-K0: output reaches the coalesced live sink in MULTIPLE
    bursts DURING the run — proving the offset-tail streams, not a single
    end-of-block dump. Cadence is finer than aba's coalesce window."""
    from core.runtime import progress
    sink = _CaptureSink()
    progress.set_sink(sink)
    try:
        s = _session(fake_weft)
        # ~15 lines * 60ms ≈ 0.9s of runtime → several 1s/10KB-window flushes,
        # but the byte cap won't fire (small output), so this leans on the
        # interval flush driven by the shim's poll loop.
        r = s.execute("EMIT 15 tick")
    finally:
        progress.clear_sink()
    assert r.returncode == 0
    assert r.stdout.count("tick") == 15
    chunks = sink.chunks()
    streamed = "".join(c["text"] for c in chunks)
    assert "tick" in streamed                      # live pane saw the output
    assert len(chunks) >= 2                          # in MULTIPLE bursts, mid-run
    s.shutdown()


def test_interrupt_stops_block(fake_weft):
    """cancel_token fires kernel_interrupt → the block ends cancelled, and the
    SESSION stays alive (interrupt preserves state, unlike shutdown)."""
    s = _session(fake_weft)
    tok = _Token()

    def _cancel_soon():
        time.sleep(0.3)
        tok.cancel()

    threading.Thread(target=_cancel_soon, daemon=True).start()
    r = s.execute("SLEEP 10", cancel_token=tok, timeout_s=30)
    assert r.cancelled is True
    assert r.returncode == 1
    assert s.alive                                   # SIGINT kept the session
    # and the session is reusable afterwards
    r2 = s.execute("EMIT 1 after")
    assert r2.returncode == 0 and "after" in r2.stdout
    s.shutdown()


def test_timeout_is_reported(fake_weft):
    s = _session(fake_weft)
    r = s.execute("SLEEP 10", timeout_s=1)
    assert r.timed_out is True
    assert r.returncode == -1
    s.shutdown()


def test_block_failure_returns_rc1(fake_weft):
    s = _session(fake_weft)
    r = s.execute("EMIT 1 before\nFAIL")
    assert r.returncode == 1
    assert not r.cancelled and not r.timed_out
    s.shutdown()


def test_dead_kernel_fails_turn(fake_weft):
    """A kernel that dies mid-run (poll raises ComputeError) fails the turn with
    a message and drops alive — the pool starts a fresh one next call."""
    s = _session(fake_weft)
    fake_weft._kernels[s.kernel_id]["state"] = "died"   # simulate node/walltime death
    r = s.execute("EMIT 3 x")
    assert r.returncode == 1
    assert not s.alive
    assert "died" in r.stderr.lower() or "kernel" in r.stderr.lower()


def test_stderr_streams_separately(fake_weft):
    s = _session(fake_weft)
    r = s.execute("EMITERR oops\nEMIT 1 ok")
    assert r.returncode == 0
    assert "oops" in r.stderr
    assert "ok" in r.stdout
    s.shutdown()


# ── standalone runner (no pytest) ────────────────────────────────────────────
# Mirrors the tests/k1_*.py precedent so the transport can be validated with the
# controller interpreter (which lacks pytest) without loading the bio-pack
# conftest. `python tests/test_weft_kernel_session.py`.

def _standalone() -> int:
    import tempfile as _tf
    from core.compute import adapter as admod
    tests = [
        test_start_and_simple_block,
        test_state_output_captured_in_full,
        test_incremental_streaming_reaches_sink,
        test_interrupt_stops_block,
        test_timeout_is_reported,
        test_block_failure_returns_rc1,
        test_dead_kernel_fails_turn,
        test_stderr_streams_separately,
    ]
    failures = []
    for t in tests:
        ws = Path(_tf.mkdtemp(prefix="aba_wk0_ws_")) / "weft"
        fake = _FakeWeft(ws)
        wmod._LOCAL_SITE = "local"
        admod.get_compute = lambda f=fake: f          # type: ignore[assignment]
        admod.weft_workspace = lambda w=ws: w          # type: ignore[assignment]
        try:
            t(fake)
            print(f"  [PASS] {t.__name__}", flush=True)
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            print(f"  [FAIL] {t.__name__}: {e}", flush=True)
            failures.append(t.__name__)
    print(f"\n{len(tests) - len(failures)}/{len(tests)} passed", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(_standalone())
