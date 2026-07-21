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


def _reticulate_pin_r() -> str:
    """Pin reticulate to a REAL interpreter in every R kernel.

    Left unset, `library(reticulate); import(...)` finds no configured Python and
    bootstraps its own: it downloads `uv`, then an interpreter, then packages —
    an unbounded network install inside a session that is supposed to be
    provisioned. Live 2026-07-21 that hung an R turn for 3.7 minutes on
    "Downloading uv" until the user killed it, with no output and no timeout.

    Pinned, reticulate binds to the interpreter we name and never downloads one.
    Preference: the project's own Python session when its prefix is directly
    execable, else the controller interpreter — which is always present and, on a
    mount-scoped base, is the only Python path that even RESOLVES from inside the
    R session's namespace (the two sessions have different mounts). The fallback
    won't carry the project's packages, so `import()` fails immediately and
    honestly instead of hanging — which is the point: this makes the failure
    fast and legible, it does not make reticulate a supported bridge.
    """
    import sys as _sys
    from pathlib import Path
    py = None
    try:
        from core.compute import base_env, project_env
        from core import projects
        if base_env.active("python"):
            _pid = str(projects.current() or "_none")
            _rt = project_env.runtime(_pid, "python")
            if _rt.get("direct_exec") and _rt.get("prefix"):
                py = str(Path(_rt["prefix"]) / "bin" / "python")
    except Exception:  # noqa: BLE001 — never break kernel startup over this
        py = None
    py = py or _sys.executable
    if not py:
        return ""
    # RETICULATE_PYTHON is the documented, long-stable knob; setting it to an
    # existing binary is what suppresses the managed-venv/uv bootstrap.
    return f"Sys.setenv(RETICULATE_PYTHON={str(py)!r})\n"


def _weft_setup_code(lang: str, remote: bool = False) -> str:
    """The kernel's first-block setup: DATA_DIR / ARTIFACTS_DIR / WORK_DIR — each as
    a VARIABLE *and* an env var — plus harvest helpers, WORK_DIR bound to the kernel's
    OWN cwd (its sandbox), and NO chdir.

    Both forms on purpose: agents reach for the bare name (`DATA_DIR/…`) AND for
    `os.environ['DATA_DIR']` / `Sys.getenv('DATA_DIR')` interchangeably, and the
    one-shot lane (core/exec/run.py) already provides both — so the interactive
    kernel must too, or code that probes the env form dies with a bare KeyError
    (observed live 2026-07-21: agent did `os.environ['DATA_DIR']`, got KeyError,
    fell back to a hardcoded path). ARTIFACTS_DIR was previously absent entirely.

    A weft kernel must keep its sandbox as cwd — the file-block protocol reads/writes
    `blocks/NNNN.*` and `kernel.stop` RELATIVE to cwd, so chdir'ing away orphans the
    protocol and the kernel dies. So the sandbox IS the work dir; aba harvests from
    there. WORK_DIR is set from `getcwd()` at runtime (the kernel knows its own
    sandbox; the controller doesn't know the id until kernel_start returns); the
    run_exec cwd snippet re-points WORK_DIR (both forms) on a cwd change.

    `remote=True`: the controller's project data/artifacts dirs do not exist on the
    kernel's machine — bind DATA_DIR/ARTIFACTS_DIR to the sandbox too, so writes stay
    (run,rel)-addressable there instead of failing on a foreign path."""
    from core.exec.kernels import setup_helpers as _j
    data, artifacts = _j._project_data_artifacts()
    if lang == "r":
        from core.exec.r import cran_repo, _ppm_ua_expr
        repoline = f'options(repos=c(CRAN={cran_repo()!r})); {_ppm_ua_expr()}\n'
        dirs = ("DATA_DIR <- getwd(); ARTIFACTS_DIR <- getwd()\n" if remote else
                f"DATA_DIR <- {str(data)!r}; ARTIFACTS_DIR <- {str(artifacts)!r}\n")
        return (f"{repoline}{dirs}WORK_DIR <- getwd()\n"
                "Sys.setenv(DATA_DIR=DATA_DIR, ARTIFACTS_DIR=ARTIFACTS_DIR, WORK_DIR=WORK_DIR)\n"
                + _reticulate_pin_r()
                + _j._harvest_helpers_r())
    dirs = ("DATA_DIR = ARTIFACTS_DIR = _os.getcwd()\n" if remote else
            f"DATA_DIR = {str(data)!r}\nARTIFACTS_DIR = {str(artifacts)!r}\n")
    return ("import os as _os\n_os.environ.setdefault('MPLBACKEND', 'Agg')\n"
            f"{dirs}WORK_DIR = _os.getcwd()\n"
            "_os.environ['DATA_DIR'] = DATA_DIR\n"
            "_os.environ['ARTIFACTS_DIR'] = ARTIFACTS_DIR\n"
            "_os.environ['WORK_DIR'] = WORK_DIR\n"
            + _j._harvest_helpers_py())


def _site_platform(site: str) -> str | None:
    """The site's conda platform string (linux-aarch64, osx-arm64, …) from its
    registered capabilities (os + arch). None when the site can't say — the
    caller then skips the cross-platform re-lock rather than guessing."""
    try:
        from core.compute import adapter as _ad
        desc = _ad.get_compute().sync_call("sites_describe", site) or {}
        caps = desc.get("capabilities") or {}
        os_, arch = caps.get("os"), caps.get("arch")
        if not (os_ and arch):
            return None
        if os_ == "darwin":
            return f"osx-{'arm64' if arch in ('arm64', 'aarch64') else '64'}"
        return f"linux-{'64' if arch in ('x86_64', 'amd64') else arch}"
    except Exception:  # noqa: BLE001
        return None


def _slurm_time_s(t: str) -> int | None:
    """'H:MM:SS' / 'D-HH:MM:SS' / 'MM:SS' → seconds; None if unparseable."""
    try:
        d, rest = t.split("-", 1) if "-" in t else (None, t)
        seg = [int(x) for x in rest.split(":")]
        sec = (seg[0] * 3600 + seg[1] * 60 + seg[2] if len(seg) == 3
               else seg[0] * 60 + seg[1] if len(seg) == 2 else seg[0] * 60)
        return sec + (int(d) * 86400 if d else 0)
    except Exception:  # noqa: BLE001
        return None


def _fit_walltime(e) -> str | None:
    """A walltime that fits the partition the job will ACTUALLY land on, out
    of a site.capability_violation's hints — the PartitionTimeLimit fence for
    interactive kernels (a capped partition refuses the default 8h hold).

    Weft submits kernel allocations without --partition (bug4), so slurm
    routes them to the cluster DEFAULT partition — clamping to the roomiest
    partition's cap produced a job slurm ACCEPTS but never starts (2h ask on
    a 1h default partition pends forever with PartitionTimeLimit while the
    node idles; chunk-A regression, 2026-07-19). Weft's partition hints do
    NOT currently say which partition is the default (sinfo is probed with
    %R — bare names, no '*'; the flag is requested in the bug4 handoff), so:
    an explicit `default: true` on a partition record wins when present
    (future weft), else a single available partition, else the SMALLEST cap
    ≥10 min — the only ask guaranteed to START no matter which partition
    slurm routes to (conservative: it may truncate the hold on clusters
    whose default is roomy; weft's own doctrine prefers short holds +
    restart). Tiny debug partitions (<10 min) are excluded. None when
    nothing safe exists or walltime wasn't the problem."""
    if getattr(e, "code", "") != "site.capability_violation":
        return None
    hints = getattr(e, "hints", None) or {}
    pinfo = hints.get("partitions") or {}
    parts = pinfo.get("available") or []

    def _cap(p):
        return _slurm_time_s(str(p.get("max_walltime") or ""))

    best = None
    default = [p for p in parts if p.get("default") is True]
    if default:
        best = _cap(default[0])
    elif len(parts) == 1:
        best = _cap(parts[0])
    else:
        floors = [c for c in (_cap(p) for p in parts) if c and c >= 600]
        best = min(floors) if floors else None
    if not best:
        return None
    asked = _slurm_time_s(str((pinfo.get("asked") or {}).get("walltime")
                              or ""))
    if asked is not None and asked <= best:
        return None      # walltime already fits — the violation is elsewhere
    h, rem = divmod(best, 3600)
    return f"{h:02d}:{rem // 60:02d}:{rem % 60:02d}"


def for_pool(scope_key: str, lang: str, *, cwd: str, env_name: str | None,
             site: str = _LOCAL_SITE):
    """Build a WeftKernelSession for the pool, or return None for an unknown
    named env (the pool raises the clear error — there is no other kernel
    transport). Three lanes: ISOLATED (a frozen named EnvID, W-K1a),
    DEFAULT (env_name=None → a live project session, W-K1b), and BARE
    (env_name='system' → no env at all, the machine's own interpreter). A
    named env is realized by the caller before the pool lock.

    `site` != local (P1, misc/bug1.md): a persistent interpreter ON that
    machine, held by weft — same peek-streamed execute path. The kernel
    attaches a FROZEN env id (a live local session can't follow it to
    another machine): a named env's env_id, else the project snapshot —
    the same identity a detached job would run under. A platform-mismatch
    at start re-locks a NAMED env once (job-lane parity)."""
    from core import projects
    pid = str(projects.current() or "_none")
    if env_name and env_name.lower() == "system":
        # BARE lane (ledger 4a): the machine's own interpreter, NO env
        # realization — env choice is orthogonal to execution mode, so
        # env='system' gets the same persistent session as any env. No
        # ensure_ready / platform re-lock (nothing is realized); the
        # PartitionTimeLimit clamp still applies — kernel_start submits a
        # walltimed job on a Slurm site regardless of what it activates.
        from core.compute.errors import ComputeError
        setup = _weft_setup_code(lang, remote=(site != _LOCAL_SITE))
        try:
            return WeftKernelSession(scope_key, lang, site=site,
                                     setup_code=setup, label=f"aba:{scope_key}")
        except ComputeError as e:
            wall = _fit_walltime(e)
            if wall:
                return WeftKernelSession(scope_key, lang, site=site,
                                         setup_code=setup, walltime=wall,
                                         label=f"aba:{scope_key}")
            raise
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
        setup = _weft_setup_code(lang, remote=True)

        def _start(eid: str):
            named_envs.ensure_ready(eid, language=lang, site=site)
            return WeftKernelSession(scope_key, lang, env_id=eid, site=site,
                                     setup_code=setup, label=f"aba:{scope_key}")

        try:
            return _start(env_id)
        except ComputeError as e:
            # lazy platform re-lock, JOB-LANE PARITY: the mismatch surfaces
            # from ensure_ready's realize task (typed, with hints) or from
            # kernel_start. NAMED envs re-solve their recorded spec; the
            # DEFAULT env re-locks the BASE pack (session-installed extras
            # don't travel — same trade the one-shot lane makes). Without
            # this the kernel lane silently lost every cross-platform site
            # (found live: aarch64 slurm fixture — one-shot re-locked and
            # ran while the session lane failed and fell back).
            from core.jobs.weft_submitter import _mismatch_platform
            plat = _mismatch_platform(e)
            if not plat and getattr(e, "code", "") == "env.layer_conflict":
                # An EXTENDED env's layer chain can fail to realize on a
                # DIFFERENT-platform site with layer_conflict (the delta was
                # solved against the controller-platform parent) — same root
                # as platform_mismatch, different error shape (found live:
                # extended env on the linux fixture from a mac controller,
                # F-ENV-2). ensure_platform re-solves base + layers for the
                # site's platform. Only cross-platform: a SAME-platform
                # layer_conflict is a genuine solve failure — re-locking
                # would just repeat it.
                sp = _site_platform(site)
                if sp and sp != named_envs.controller_platform():
                    plat = sp
            if plat:
                if env_name:
                    relock = named_envs.ensure_platform(pid, env_name, plat)
                else:
                    from core.compute import base_env
                    relock = base_env.ensure_platform(lang, plat)
                env_id = relock["env_id"]
                try:
                    return _start(env_id)
                except ComputeError as e2:
                    e = e2             # may now hit the walltime fence below
            # PartitionTimeLimit fence: the default 8h interactive walltime
            # exceeds a capped partition's max (weft refuses rather than
            # queue forever). Clamp to the roomiest partition cap and retry
            # once — weft's own suggestion (short walltimes + restart).
            wall = _fit_walltime(e)
            if wall:
                named_envs.ensure_ready(env_id, language=lang, site=site)
                return WeftKernelSession(scope_key, lang, env_id=env_id,
                                         site=site, setup_code=setup,
                                         walltime=wall,
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

    Attaches in one of THREE modes — at most one of `env_id` / `session_id`:
    a frozen realized `env_id` (has `.weft-ready` on `site`), a live
    `session_id`, or NEITHER — a BARE kernel on the machine's own interpreter
    (weft's env_id=None default: no activation, `python3`/`Rscript` from PATH).
    The session attach is what the default interactive lane wants: a
    `session_install` (live `ensure_capability`) lands in the running kernel
    and is visible to the next block, no restart — matching today's jupyter
    session-kernel UX. Frozen `env_id` is for isolated named envs (immutable
    identity). Bare is the `env='system'` lever: env choice is orthogonal to
    execution mode, so a stdlib-only step gets the same persistent session as
    any env — just with nothing realized (and nothing installable: a bare
    kernel has no session to `session_install` into). `site` is a registered
    weft site ("local" or a declared remote); the env/session→handle
    resolution is the caller's job (the pool wiring), so the transport stays
    testable on its own.
    """

    def __init__(self, scope_key: str, lang: str, *, env_id: str | None = None,
                 session_id: str | None = None, site: str = _LOCAL_SITE,
                 setup_code: str | None = None, walltime: str = "08:00:00",
                 resources: dict | None = None, label: str = ""):
        if env_id and session_id:
            raise ValueError("at most one of env_id / session_id "
                             "(frozen env OR live session; neither = bare "
                             "kernel on the machine's own interpreter)")
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
        attach = ({"session_id": session_id} if session_id
                  else {"env_id": env_id} if env_id else {})
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
            # keep reading until both streams are exhausted (bounded). On a
            # REMOTE site the `.rc` sentinel can become visible BEFORE the
            # block's final `.out` bytes are readable over the data shim
            # (mendel repro: interspersed blocks returned rc=0 with empty
            # stdout when this drain stopped at the first no-advance pull) —
            # so remote drains are time-gated: only several consecutive quiet
            # pulls, ~0.15s apart, count as settled. Local stays advance-gated
            # (no added latency; the sandbox is directly consistent).
            need_quiet = 4 if self.site != _LOCAL_SITE else 1
            quiet = 0
            for _ in range(256):
                before = (out_off, err_off)
                try:
                    _pull()
                except ComputeError:
                    return
                if (out_off, err_off) == before:
                    quiet += 1
                    if quiet >= need_quiet:
                        return
                    time.sleep(0.15)
                else:
                    quiet = 0

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
