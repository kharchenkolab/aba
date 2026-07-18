"""Weft-native persistent kernel transport (kernels_to_weft.md W-K0).

The second `KernelSession` implementation, alongside `jupyter.py`. Where the
jupyter transport drives an out-of-process IPython kernel over ZMQ (local-only,
needs `ipykernel`/`irkernel` baked into the env), this one drives weft's native
`kernel_*` file-block protocol through the compute port:

  * `kernel_start(site, lang, env_id=…)` holds the interpreter (inside a
    scheduler allocation on a remote site — same call, no ports/tunnels);
  * `kernel_exec(wait=False)` writes a code block and returns a handle;
  * `kernel_poll(block, timeout)` returns `{state: running}` or the finished
    `{rc, out, err}` when the `.rc` sentinel lands.

weft's `poll` only returns the block's captured output AT COMPLETION. For live
streaming we tail the block's `.out`/`.err` files — which grow on the shared
filesystem DURING the run — at an advancing offset each poll, and feed the same
`Coalescer` + progress sinks the jupyter transport uses. On a LOCAL site those
files are directly readable (the fast path here); on a REMOTE site aba cannot
read them directly yet, so streaming degrades to completion-only output until
weft exposes an incremental-output tool (the non-blocking ask in
kernels_to_weft.md §4). The interface is written remote-shaped so that wiring a
partial-read is a one-method change, not a rewrite.

This transport owns no OS process: weft manages the interpreter (possibly on
another host), so `kernel_pid()` is None and the pool's OS-reaper machinery does
not apply — it retires with the jupyter transport (kernels_to_weft.md §8).
"""
from __future__ import annotations

import time
from typing import Optional

from core import config
from core.exec.base import ExecResult

_LOCAL_SITE = "local"

# Cadence of the streaming loop: each tick pulls incremental output via
# kernel_peek. ~0.2s is finer than aba's 0.5s coalesce window, so no live-UX
# loss, and it's the SAME code path local and remote (peek rides weft's shim
# read-from — locals could read the block files directly, but one path is worth
# more than the micro-optimization).
_PEEK_INTERVAL_S = 0.2

# kernel_peek reports incremental output + running/rc but does NOT assert
# liveness; a kernel that dies mid-block never writes its `.rc`, so we check
# kernel_status on this cadence to surface death instead of waiting forever.
_STATUS_INTERVAL_S = 1.0

# After Stop (SIGINT via kernel_interrupt), how long to let the block abort
# cleanly — preserving the interpreter + its state — before hard-stopping the
# kernel. Same ceiling + rationale as the jupyter transport.
_CANCEL_GRACE_S = config.settings.kernel_cancel_grace_s.get()


def _weft_setup_code(lang: str, remote: bool = False) -> str:
    """The kernel's first-block setup: DATA_DIR + harvest helpers, WORK_DIR bound
    to the kernel's OWN cwd (its sandbox), and NO chdir.

    A weft kernel must keep its sandbox as cwd — the file-block protocol reads/writes
    `blocks/NNNN.*` and `kernel.stop` RELATIVE to cwd, so chdir'ing away orphans the
    protocol and the kernel dies. So the sandbox IS the work dir; aba harvests from
    there. WORK_DIR is set from `getcwd()` at runtime (the kernel knows its own
    sandbox; the controller doesn't know the id until kernel_start returns).

    `remote=True`: the controller's project data dir does not exist on the
    kernel's machine — bind DATA_DIR to the sandbox too, so writes stay
    (run,rel)-addressable there instead of failing on a foreign path."""
    from core.exec.kernels import jupyter as _j
    if lang == "r":
        from core.exec.r import cran_repo, _ppm_ua_expr
        repoline = f'options(repos=c(CRAN={cran_repo()!r})); {_ppm_ua_expr()}\n'
        data_line = ("DATA_DIR <- getwd()\n" if remote else
                     f"DATA_DIR <- {str(_j._project_data_artifacts()[0])!r}\n")
        return (f"{repoline}{data_line}WORK_DIR <- getwd()\n"
                + _j._harvest_helpers_r())
    data_line = ("DATA_DIR = _os.getcwd()\n" if remote else
                 f"DATA_DIR = {str(_j._project_data_artifacts()[0])!r}\n")
    return ("import os as _os\n_os.environ.setdefault('MPLBACKEND', 'Agg')\n"
            f"{data_line}WORK_DIR = _os.getcwd()\n"
            + _j._harvest_helpers_py())


def for_pool(scope_key: str, lang: str, *, cwd: str, env_name: str | None,
             site: str = _LOCAL_SITE):
    """Build a WeftKernelSession for the pool, or return None to fall back to the
    jupyter transport. W-K1a handles the ISOLATED-env lane (a frozen named EnvID);
    the default lane (env_name=None → a live project session) is W-K1b and returns
    None for now. The env is realized by the caller before the pool lock.

    `site` != local (P1, misc/bug1.md): a persistent interpreter ON that
    machine, held by weft — same peek-streamed execute path. The kernel
    attaches a FROZEN env id (a live local session can't follow it to
    another machine): a named env's env_id, else the project snapshot —
    the same identity a detached job would run under. A platform-mismatch
    at start re-locks a NAMED env once (job-lane parity)."""
    from core import projects
    pid = str(projects.current() or "_none")
    if site and site != _LOCAL_SITE:
        from core.compute.errors import ComputeError
        from core.compute import named_envs
        if env_name:
            row = named_envs.resolve(pid, env_name)
            if row is None:
                return None            # unknown env — caller surfaces the error
            env_id = row["env_id"]
        else:
            from core.compute import base_env, project_env
            base_env.require(lang)
            env_id = project_env.snapshot(pid, lang)
        # kernel_start REFUSES an env not realized on its site (task realize
        # builds implicitly; kernels don't — found by the mendel repro):
        # pre-realize there, first use pays the build like a first job would
        named_envs.ensure_ready(env_id, language=lang, site=site)
        setup = _weft_setup_code(lang, remote=True)
        try:
            return WeftKernelSession(scope_key, lang, env_id=env_id, site=site,
                                     setup_code=setup, label=f"aba:{scope_key}")
        except ComputeError as e:
            from core.jobs.weft_submitter import _mismatch_platform
            plat = _mismatch_platform(e)
            if plat and env_name:      # named env: re-lock for the site, retry once
                from core.compute import named_envs
                relock = named_envs.ensure_platform(pid, env_name, plat)
                return WeftKernelSession(scope_key, lang,
                                         env_id=relock["env_id"], site=site,
                                         setup_code=setup,
                                         label=f"aba:{scope_key}")
            raise
    if not env_name:
        # Default lane (W-K1b): attach to the project's LIVE session so an
        # ensure_capability (session_install) is visible to the running kernel with
        # no restart — the live-install UX. run_exec already ensured the session
        # before the pool lock; ensure() here is idempotent (returns the same id).
        from core.compute import project_env
        info = project_env.ensure(pid, lang)
        return WeftKernelSession(scope_key, lang, session_id=info["session_id"],
                                site="local", setup_code=_weft_setup_code(lang))
    from core.compute import named_envs
    row = named_envs.resolve(pid, env_name)
    if row is None:
        return None                     # unknown env — let jupyter raise the clear error
    # Realize before the pool lock (idempotent). ensure_READY (not ensure_realized):
    # strategy-blind — a squashfs env has no raw prefix, and we don't need one here
    # (WeftKernelSession hands the EnvID to weft's kernel_start, which mounts it).
    named_envs.ensure_ready(row["env_id"])
    return WeftKernelSession(scope_key, lang, env_id=row["env_id"], site="local",
                            setup_code=_weft_setup_code(lang))


class WeftKernelSession:
    """A persistent weft kernel behind the `KernelSession` interface.

    Attaches to EITHER a frozen realized `env_id` (has `.weft-ready` on `site`)
    OR a live `session_id` — exactly one. The session attach is what the default
    interactive lane wants: a `session_install` (live `ensure_capability`) lands
    in the running kernel and is visible to the next block, no restart — matching
    today's jupyter session-kernel UX. Frozen `env_id` is for isolated named
    envs (immutable identity). `site` is a registered weft site ("local" or a
    declared remote); the env/session→handle resolution is the caller's job
    (the pool wiring), so the transport stays testable on its own.
    """

    def __init__(self, scope_key: str, lang: str, *, env_id: str | None = None,
                 session_id: str | None = None, site: str = _LOCAL_SITE,
                 setup_code: str | None = None, walltime: str = "08:00:00",
                 resources: dict | None = None, label: str = ""):
        if bool(env_id) == bool(session_id):
            raise ValueError("exactly one of env_id / session_id is required "
                             "(a kernel attaches to a frozen env OR a live session)")
        self.scope_key = scope_key
        self.lang = lang
        self.site = site
        self.env_id = env_id
        self.session_id = session_id
        self.env_name: Optional[str] = None   # pool-compat; W-K1 populates
        self.last_used = time.time()
        self.alive = False
        self.busy = False
        self._cancel_grace_s = _CANCEL_GRACE_S
        self.kernel_id: Optional[str] = None

        self.work_dir: Optional[str] = None
        attach = {"session_id": session_id} if session_id else {"env_id": env_id}
        r = self._call("kernel_start", site, lang, walltime=walltime,
                       resources=resources or {}, label=label, **attach)
        self.kernel_id = r["kernel_id"]
        if site == _LOCAL_SITE:
            # The kernel's sandbox = its cwd = where bare relative writes land.
            # aba harvests outputs from here (not aba scratch — a weft kernel can't
            # chdir without breaking its protocol). Local-only; a remote kernel's
            # sandbox comes home via weft's retain/collect (retention design).
            from core.compute import adapter as _adapter
            self.work_dir = str(_adapter.weft_workspace() / "site-local"
                                / "kernels" / self.kernel_id)
        self.alive = True
        if setup_code:
            # Configure the session namespace once (DATA_DIR / WORK_DIR / harvest
            # helpers) — the caller supplies the language-appropriate cell.
            self.execute(setup_code, timeout_s=120)

    # -- weft port access -----------------------------------------------------

    def _call(self, name: str, /, *args, **kw):
        """Sync pass-through to a weft kernel_* tool (worker-thread context).

        Runs on the calling thread — correct here: execute() already runs in a
        tool worker thread, and every kernel_* call is fast (block writes + short
        polls, never a solve). weft error payloads surface as ComputeError."""
        from core.compute import adapter as _adapter
        return _adapter.get_compute().sync_call(name, *args, **kw)

    # -- KernelSession interface ----------------------------------------------

    def touch(self) -> None:
        self.last_used = time.time()

    def execute(self, code: str, *, cancel_token=None, timeout_s: int = 90) -> ExecResult:
        from core.runtime import progress
        from core.exec.stream_coalesce import Coalescer
        from core.config import TOOL_STREAM_FLUSH_BYTES, TOOL_STREAM_FLUSH_INTERVAL_S
        from core.compute.errors import ComputeError

        stdout: list[str] = []
        stderr: list[str] = []
        _last_emit = [0.0]   # throttle the one-line chat tick to ~2/s

        def _emit_live(text: str):
            # Newest non-empty line as a tool_progress tick (curl/tqdm carriage
            # returns → latest segment). Identical contract to jupyter.py so the
            # chat "running · <line>" indicator behaves the same across transports.
            line = next((s.strip() for s in reversed(text.replace("\r", "\n").splitlines())
                         if s.strip()), "")
            if not line:
                return
            now = time.time()
            if now - _last_emit[0] < 0.5:
                return
            _last_emit[0] = now
            progress.emit(line[:200], phase="run")

        # Full coalesced live-tail to the output drawer, keyed by tool_use_id —
        # capture the sink ONCE on this (worker) thread and close over it, since
        # progress.current_sink() is thread-local (same fix as jupyter.py).
        _chunk_q = progress.current_sink()

        def _emit_chunk(ev: dict) -> None:
            if _chunk_q is None:
                return
            try:
                _chunk_q.put_nowait({
                    "type": "chunk",
                    "stream": ev.get("stream", "stdout"),
                    "text": ev.get("text", ""),
                    "bytes_total": ev.get("bytes_total", 0),
                    "elapsed_s": ev.get("elapsed_s", 0.0),
                    "reason": ev.get("reason"),
                })
            except Exception:  # noqa: BLE001 — live-tail must never break a run
                pass

        coalescer = Coalescer(
            flush_bytes=TOOL_STREAM_FLUSH_BYTES,
            flush_interval_s=TOOL_STREAM_FLUSH_INTERVAL_S,
            on_flush=_emit_chunk,
        )

        def _feed(stream: str, text: str) -> None:
            if not text:
                return
            (stderr if stream == "stderr" else stdout).append(text)
            _emit_live(text)
            coalescer.push(stream, text)

        self.busy = True
        self.touch()
        try:
            sub = self._call("kernel_exec", self.kernel_id, code, wait=False)
        except ComputeError as e:
            self.busy = False
            self.alive = False
            return ExecResult(returncode=1, stdout="",
                              stderr=f"[kernel] block submit failed: {e}",
                              cancelled=False, timed_out=False)
        block = int(sub.get("block", 0))
        out_off = err_off = 0
        deadline = time.time() + timeout_s
        unregister = cancel_token.register(self.interrupt) if cancel_token is not None else None
        cancelled = timed_out = kernel_died = False
        died_msg = ""
        rc: Optional[int] = None

        done = False

        def _pull() -> None:
            """One kernel_peek: append incremental output, advance offsets, and
            record rc/done when the block has finished. Raises ComputeError only
            if the kernel is unknown/unreachable."""
            nonlocal out_off, err_off, rc, done
            pk = self._call("kernel_peek", self.kernel_id, block,
                            out_offset=out_off, err_offset=err_off)
            if pk.get("out_delta"):
                _feed("stdout", pk["out_delta"])
                out_off = pk.get("out_offset", out_off)
            if pk.get("err_delta"):
                _feed("stderr", pk["err_delta"])
                err_off = pk.get("err_offset", err_off)
            if not pk.get("running", True):
                rc = pk.get("rc")
                done = True

        def _drain_remaining() -> None:
            # After done, a final delta may still exceed one peek's max_bytes —
            # keep reading until both streams are exhausted (bounded).
            for _ in range(256):
                before = (out_off, err_off)
                try:
                    _pull()
                except ComputeError:
                    return
                if (out_off, err_off) == before:
                    return

        last_status = time.time()
        try:
            while True:
                try:
                    _pull()
                except ComputeError as e:
                    kernel_died = True
                    died_msg = str(e)
                    break
                coalescer.maybe_flush()
                self.touch()
                if done:
                    _drain_remaining()
                    break
                # Stop pressed? weft SIGINT ends the block rc=130 (surfaces as
                # done on a later peek); catching the token here breaks promptly.
                if cancel_token is not None and getattr(cancel_token, "cancelled", False):
                    cancelled = True          # interrupt already fired via the token
                    break
                if time.time() >= deadline:
                    timed_out = True
                    break
                # peek doesn't assert liveness — a dead kernel never writes .rc, so
                # poll kernel_status on its own cadence to surface death.
                now = time.time()
                if now - last_status >= _STATUS_INTERVAL_S:
                    last_status = now
                    try:
                        st = self._call("kernel_status", self.kernel_id)
                    except ComputeError as e:
                        kernel_died = True
                        died_msg = str(e)
                        break
                    if st.get("state") not in (None, "running"):
                        kernel_died = True
                        died_msg = f"kernel {st.get('state')}"
                        break
                time.sleep(_PEEK_INTERVAL_S)

            # rc==130 IS weft's SIGINT exit — a block that finished at rc=130 was
            # interrupted, even if we observed the token a beat late.
            if rc == 130:
                cancelled = True

            if cancelled and not done:
                # Block still running — SIGINT already sent via the token; give it
                # a short grace to unwind cleanly (state preserved, finishing
                # rc=130). If it won't stop (wedged in native code), hard-stop the
                # kernel so the abandoned block can't corrupt the next call; the
                # pool starts a fresh session next time.
                self.interrupt()
                grace_end = time.time() + self._cancel_grace_s
                while time.time() < grace_end:
                    try:
                        _pull()
                    except ComputeError:
                        break
                    if done:
                        break
                    time.sleep(0.1)
                if not done:
                    self.shutdown()
            elif timed_out:
                self.interrupt()              # SIGINT a cell past its ceiling
        finally:
            self.busy = False
            if unregister is not None:
                unregister()
            try:
                coalescer.flush(reason="final")
            except Exception:  # noqa: BLE001
                pass

        if kernel_died:
            self.alive = False
            self.touch()
            msg = (died_msg or "".join(stderr)).strip() or (
                "The compute kernel died mid-execution (killed, crashed, or out "
                "of memory / walltime). Rerun the cell — the session will restart.")
            return ExecResult(returncode=1, stdout="".join(stdout), stderr=msg,
                              cancelled=False, timed_out=False)
        if cancelled:
            self.touch()
            return ExecResult(returncode=1, stdout="".join(stdout),
                              stderr="".join(stderr), cancelled=True, timed_out=False)
        self.touch()
        rc = 0 if (rc is None and not timed_out) else rc
        code_rc = -1 if timed_out else (0 if (rc == 0) else 1)
        return ExecResult(
            returncode=code_rc,
            stdout="".join(stdout),
            stderr="".join(stderr),
            cancelled=False,
            timed_out=timed_out,
        )

    def interrupt(self) -> None:
        if not self.kernel_id:
            return
        try:
            self._call("kernel_interrupt", self.kernel_id)
        except Exception:  # noqa: BLE001 — best-effort SIGINT
            pass

    def shutdown(self) -> None:
        self.alive = False
        if not self.kernel_id:
            return
        try:
            self._call("kernel_stop", self.kernel_id)
        except Exception:  # noqa: BLE001 — best-effort; weft reaps by walltime anyway
            pass

    def kernel_pid(self) -> Optional[int]:
        """weft owns the interpreter process (possibly on another host), so this
        transport owns no local pid — the pool's OS-reaper skips it. Returns None."""
        return None

    def kernel_dead(self) -> bool:
        """Liveness via weft's kernel_status (state != 'running'). Best-effort:
        an unreachable substrate reports 'not dead' so a transient blip doesn't
        nuke a live session."""
        if not self.kernel_id:
            return True
        try:
            st = self._call("kernel_status", self.kernel_id)
        except Exception:  # noqa: BLE001
            return False
        return st.get("state") not in (None, "running")
