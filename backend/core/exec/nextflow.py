"""Nextflow / nf-core execution core (P0 — HPC-routed pipelines).

`run_nextflow_code` is the reusable execution+harvest function for a Nextflow
pipeline — the workflow analogue of `run_python_code` / `run_r_code`. Both the
in-process worker (runner._run_one) and the Slurm compute-node entry
(jobs.slurm_entry) call it, so a pipeline harvests + provenances identically
either way.

Model (refs.md / misc/nfcore.md): we run the lightweight **Nextflow head
process**; Nextflow's own executor (the site profile, e.g. nf-core/configs
`cbe`) fans each task out as its own scheduler job. So on a cluster the head is
itself a long-lived Slurm job that submits task jobs — we don't wrap the whole
pipeline in one allocation.

Site wiring (the module to load, the profiles to append, the singularity cache,
the work-dir root, head-job resources) is config, not code — see
`nextflow_config()`. Defaults are conservative so this is a no-op off-cluster;
a deployment sets the `nextflow:` block in hpc.yaml (or the ABA_NEXTFLOW_* env).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import time
from pathlib import Path
from typing import Optional


# ── site config ──────────────────────────────────────────────────────────────
# Two execution modes (see run_nextflow_code / nextflow_config["execution"]):
#   "slurm" (default) — lightweight head fans each task out as its OWN Slurm job
#                       (executor=slurm via the cbe config). Right for heavy real data.
#   "local"           — the head IS the worker: tasks run as subprocesses on its node
#                       (executor=local), so it needs a bigger allocation. One scheduling
#                       event, not N — right for small/test pipelines whose per-task compute
#                       is dwarfed by Slurm queue latency.
_DEFAULT_HEAD = {"cores": 2, "mem_gb": 8, "qos": None, "partition": None, "walltime_h": 24}
_DEFAULT_LOCAL = {"cores": 8, "mem_gb": 32, "qos": None, "partition": None, "walltime_h": 24}
# env var → (name, caster) for per-field resource overrides (see nextflow_config).
_HEAD_ENV_OVERRIDES = {
    "cores": ("ABA_NEXTFLOW_HEAD_CORES", int),
    "mem_gb": ("ABA_NEXTFLOW_HEAD_MEM_GB", int),
    "walltime_h": ("ABA_NEXTFLOW_HEAD_WALLTIME_H", int),
    "qos": ("ABA_NEXTFLOW_HEAD_QOS", str),
    "partition": ("ABA_NEXTFLOW_HEAD_PARTITION", str),
}
_LOCAL_ENV_OVERRIDES = {
    "cores": ("ABA_NEXTFLOW_LOCAL_CORES", int),
    "mem_gb": ("ABA_NEXTFLOW_LOCAL_MEM_GB", int),
    "walltime_h": ("ABA_NEXTFLOW_LOCAL_WALLTIME_H", int),
    "qos": ("ABA_NEXTFLOW_LOCAL_QOS", str),
    "partition": ("ABA_NEXTFLOW_LOCAL_PARTITION", str),
}


def _apply_env_overrides(base: dict, overrides: dict) -> dict:
    """Return `base` with each field replaced by its env override when set+castable."""
    out = dict(base)
    for key, (envname, caster) in overrides.items():
        raw = os.environ.get(envname)
        if raw not in (None, ""):
            try:
                out[key] = caster(raw)
            except (TypeError, ValueError):
                pass
    return out


def nextflow_config() -> dict:
    """Resolve the site's Nextflow wiring, broadest-default first:

      hpc.yaml `nextflow:` block  →  ABA_NEXTFLOW_* env overrides  →  these defaults.

    Keys:
      module                 Lmod module the Slurm head job `module load`s so
                             `nextflow` is on PATH (e.g. "nextflow/24.10.6"). None
                             → assume nextflow is already on PATH or conda-install it.
      profiles               profiles APPENDED to the caller's -profile (e.g. ["cbe"]).
      singularity_cachedir   NXF_SINGULARITY_CACHEDIR (shared image cache).
      workdir_root           base for Nextflow's -work-dir; a per-run subdir is
                             appended. None → use the run's own scratch dir.
      head                   the head-job Slurm resources {cores, mem_gb, qos,
                             partition, walltime_h} — modest CPU/mem, LONG walltime.
    """
    cfg: dict = {}
    try:
        from core.jobs.hpc_config import hpc_config
        cfg = dict((hpc_config() or {}).get("nextflow") or {})
    except Exception:  # noqa: BLE001 — config is optional
        cfg = {}

    def _csv(v):
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return [t.strip() for t in str(v or "").split(",") if t.strip()]

    module = os.environ.get("ABA_NEXTFLOW_MODULE", cfg.get("module"))
    profiles = _csv(os.environ.get("ABA_NEXTFLOW_PROFILES")) or _csv(cfg.get("profiles"))
    cachedir = os.environ.get("ABA_NEXTFLOW_CACHEDIR", cfg.get("singularity_cachedir"))
    workdir_root = os.environ.get("ABA_NEXTFLOW_WORKDIR", cfg.get("workdir_root"))
    # A site config (`-c <file>`) — e.g. an ABA-pinned cbe-derived profile that sets
    # the slurm executor/queue/QOS + singularity, WITHOUT nf-core/configs' stale
    # `process.module` loads. Applied to every run when set.
    config_file = os.environ.get("ABA_NEXTFLOW_CONFIG", cfg.get("config_file"))
    # Standalone Nextflow on shared FS (slim SIF / cluster-personal) as an ALTERNATIVE to `module`:
    # a dir (or the launcher path) PREPENDED to the head's PATH so it runs a self-installed NF (e.g.
    # ≥25.04, past the module's version ceiling). Used ONLY when set → `module` stays the path for
    # fat SIF / personal installs, which are unaffected. See misc/nfcore.md §7d.
    bin_ = os.environ.get("ABA_NEXTFLOW_BIN", cfg.get("bin"))
    # Persistent NXF_HOME (plugins/assets/scm). None → the per-run scratch `.nextflow` (today's
    # behavior; re-fetches plugins every run). Set it to share plugins across a user's runs.
    home = os.environ.get("ABA_NEXTFLOW_HOME", cfg.get("home"))
    # JAVA_HOME for the head: modern nf-core pipelines pull plugins (nf-schema) compiled
    # for Java 17+, but a cluster's `nextflow` module may pin an older Java. Point the head
    # at a Java ≥17 here (the run sets JAVA_HOME, which Nextflow honors). None → use whatever
    # the module/PATH provides.
    java_home = os.environ.get("ABA_NEXTFLOW_JAVA_HOME", cfg.get("java_home"))
    # Per-field admin overrides for the head/local Slurm footprint (env wins over site config).
    # walltime_h is a scheduling knob: a head that requests a very long window backfills poorly
    # (the scheduler needs a guaranteed free slot ≥ walltime), so on a busy cluster a shorter
    # walltime + auto-resume schedules far faster. cores/mem/qos/partition let a site pin the
    # allocation. The `local` block is the bigger allocation used in execution="local".
    head = _apply_env_overrides({**_DEFAULT_HEAD, **(cfg.get("head") or {})}, _HEAD_ENV_OVERRIDES)
    local = _apply_env_overrides({**_DEFAULT_LOCAL, **(cfg.get("local") or {})}, _LOCAL_ENV_OVERRIDES)
    # Default execution mode (per-run param overrides this): slurm fan-out unless a site opts
    # into local (e.g. a single-node deploy) via ABA_NEXTFLOW_EXECUTION=local.
    execution = (os.environ.get("ABA_NEXTFLOW_EXECUTION") or cfg.get("execution") or "slurm").lower()
    if execution not in ("slurm", "local"):
        execution = "slurm"
    return {"module": module or None, "profiles": profiles,
            "singularity_cachedir": cachedir or None,
            "workdir_root": workdir_root or None, "config_file": config_file or None,
            "java_home": java_home or None, "head": head, "local": local,
            "bin": bin_ or None, "home": home or None,
            "execution": execution}


def nextflow_bin_dir(bin_value: Optional[str]) -> Optional[str]:
    """Directory to PREPEND to PATH for a self-installed Nextflow (ABA_NEXTFLOW_BIN), or None.
    Accepts the launcher's dir OR the launcher file itself. Shared by the inline head env AND the
    Slurm head's job.sh — the single source of truth for the module-vs-bin choice."""
    if not bin_value:
        return None
    return os.path.dirname(bin_value) if os.path.isfile(bin_value) else bin_value


def merged_profile(caller_profile: Optional[str], site_profiles: list[str]) -> Optional[str]:
    """Caller's -profile tokens first (they win in Nextflow), then any site
    profiles not already present. Returns a comma string, or None if empty."""
    out: list[str] = []
    for t in [t.strip() for t in (caller_profile or "").split(",") if t.strip()]:
        if t not in out:
            out.append(t)
    for t in site_profiles:
        if t not in out:
            out.append(t)
    return ",".join(out) or None


def local_executor_config(cores: int, mem_gb: int) -> str:
    """Groovy config (passed as a second `-c`, so it overrides the cbe config's
    executor=slurm) that runs tasks on the LOCAL executor — i.e. as subprocesses on
    the head's own node — instead of fanning each out as a Slurm job. The executor
    pool is bounded to the head allocation, and resourceLimits clamps each task's
    request to that pool so an oversized process still schedules (just slower). The
    cbe config's singularity cache + igenomes settings still apply."""
    c, m = int(cores), int(mem_gb)
    return (
        "// ABA: force local execution (tasks run on the head node, not fanned out to Slurm)\n"
        "process {\n"
        "    executor = 'local'\n"
        "    clusterOptions = null\n"
        "    queue = null\n"
        f"    resourceLimits = [ cpus: {c}, memory: {m}.GB, time: 14.d ]\n"
        "}\n"
        "executor {\n"
        f"    cpus = {c}\n"
        f"    memory = '{m} GB'\n"
        "}\n"
    )


def head_timeout_s(head: Optional[dict] = None) -> int:
    """App-level kill timeout for the Nextflow *head* process: its generous Slurm
    walltime plus a margin. The head's wall-clock is dominated by UNPREDICTABLE task
    queue waits, not compute, so this tracks the walltime (the real bound — a walltime
    kill is a `slurm_terminal_fail` that auto-resumes), NOT a runtime estimate. Sizing
    the head off an estimate is the bug that self-kills a head whose tasks are merely
    queued on a busy cluster."""
    h = head if head is not None else nextflow_config()["head"]
    return int(h.get("walltime_h") or 24) * 3600 + 1800


def java_env(java_home: Optional[str], base: Optional[dict] = None) -> dict:
    """Env overrides so a module-loaded Nextflow head runs on `java_home` (Java ≥17,
    required by the nf-schema plugin) instead of the older Java its module pins.

    Setting JAVA_HOME alone is *not* enough: a cluster's `nextflow` module typically
    puts the old JDK's lib dir on LD_LIBRARY_PATH, so the newer `java` then loads that
    old `libnet.so` (first on the path) and dies with
    "libnio.so: undefined symbol: ipv4_available". We prepend our Java's own lib (and
    bin) dirs so its native libraries win, leaving the rest of the path intact.
    Returns {} when no java_home is configured (use whatever the module/PATH provides).
    """
    if not java_home:
        return {}
    env = os.environ if base is None else base
    jbin, jlib = f"{java_home}/bin", f"{java_home}/lib:{java_home}/lib/server"
    cur_path, cur_ld = env.get("PATH", ""), env.get("LD_LIBRARY_PATH", "")
    return {
        "JAVA_HOME": java_home,
        "PATH": f"{jbin}:{cur_path}" if cur_path else jbin,
        "LD_LIBRARY_PATH": f"{jlib}:{cur_ld}" if cur_ld else jlib,
    }


# ── pure helpers ─────────────────────────────────────────────────────────────
def nextflow_command(pipeline: str, *, revision=None, profile=None, outdir: str,
                     params: dict | None = None, work_dir: Optional[str] = None,
                     reports_dir: Optional[str] = None, resume: bool = True,
                     config_file: Optional[str] = None, extra_configs=None,
                     extra_args=None) -> list[str]:
    """Build the `nextflow run …` argv. Pure function — unit-tested.
    `extra_configs` are additional `-c` files appended AFTER config_file (later `-c`
    wins), e.g. the local-executor override (see local_executor_config)."""
    cmd = ["nextflow", "run", pipeline]
    if revision:
        cmd += ["-r", str(revision)]
    if config_file:
        cmd += ["-c", str(config_file)]
    for ec in (extra_configs or []):
        cmd += ["-c", str(ec)]
    if profile:
        cmd += ["-profile", str(profile)]
    cmd += ["-ansi-log", "false"]
    if work_dir:
        cmd += ["-work-dir", str(work_dir)]
    if reports_dir:
        rd = Path(reports_dir)
        cmd += ["-with-trace", str(rd / "trace.txt"),
                "-with-report", str(rd / "report.html"),
                "-with-timeline", str(rd / "timeline.html")]
    if resume:
        cmd += ["-resume"]
    cmd += ["--outdir", str(outdir)]
    for k, v in (params or {}).items():
        cmd += [f"--{k}", str(v)]
    cmd += list(extra_args or [])
    return cmd


def clear_stale_reports(reports_dir) -> None:
    """Delete any `-with-trace/-report/-timeline` files left from a prior run in this
    reports dir. Nextflow ABORTS AT STARTUP if these exist (no `*.overwrite` set when
    enabled via CLI) — which would break every `-resume` / auto-resume into the same
    run dir. The reports are regenerated each run (cached tasks just show as CACHED),
    so removing the stale ones is safe."""
    rd = Path(reports_dir)
    for pat in ("trace.txt*", "report.html*", "timeline.html*", "dag.*"):
        for p in rd.glob(pat):
            try:
                p.unlink()
            except OSError:
                pass


def parse_trace_rows(trace_path) -> list[dict]:
    """Read a `-with-trace` TSV into a list of header-keyed row dicts. [] on any
    miss. Nextflow updates this file live as tasks change state, so it doubles as
    the running-progress source (trace_progress) and the post-hoc summary source."""
    try:
        lines = Path(trace_path).read_text().splitlines()
    except Exception:  # noqa: BLE001
        return []
    if len(lines) < 2:
        return []
    header = lines[0].split("\t")
    rows: list[dict] = []
    for ln in lines[1:]:
        parts = ln.split("\t")
        if len(parts) >= len(header):
            rows.append({header[i]: parts[i] for i in range(len(header))})
    return rows


def _size_mb(s: str) -> float:
    """Parse a Nextflow size string ('2 MB', '1.5 GB', '512 KB', '0') → MB. 0 on miss."""
    try:
        s = (s or "").strip()
        if not s or s in ("-", "0"):
            return 0.0
        m = re.match(r"([0-9.]+)\s*([KMGT]?)B?", s, re.I)
        if not m:
            return 0.0
        val = float(m.group(1)); unit = (m.group(2) or "").upper()
        return val * {"": 1.0 / 1024, "K": 1.0 / 1024, "M": 1.0, "G": 1024.0, "T": 1024.0 * 1024}.get(unit, 1.0)
    except Exception:  # noqa: BLE001
        return 0.0


def _proc(name: str) -> str:
    """The process name without Nextflow's per-task '(N)' / tag suffix."""
    return re.sub(r"\s*\(.*\)$", "", (name or "").strip()) or (name or "")


def trace_summary(rows: list[dict]) -> dict:
    """Post-hoc per-task rollup for a finished run: counts by status, the failed
    tasks (name + exit), and the peak-memory task. Surfaced on the Run + the
    workflow exec record so a pipeline isn't just an opaque stdout blob."""
    from collections import Counter
    if not rows:
        return {}
    by_status = Counter((r.get("status") or "?").upper() for r in rows)
    failed = [{"process": _proc(r.get("name", "")), "exit": r.get("exit"),
               "status": (r.get("status") or "").upper()}
              for r in rows
              if (r.get("status") or "").upper() in ("FAILED", "ABORTED")
              or (r.get("exit") not in (None, "", "-", "0"))]
    peak_mb, peak_proc = 0.0, None
    for r in rows:
        mb = _size_mb(r.get("peak_rss", ""))
        if mb > peak_mb:
            peak_mb, peak_proc = mb, _proc(r.get("name", ""))
    return {"total_tasks": len(rows), "status_counts": dict(by_status),
            "failed": failed[:25], "peak_rss_mb": round(peak_mb, 1),
            "peak_process": peak_proc}


def trace_progress(rows: list[dict]) -> dict:
    """Compact LIVE progress from the trace (read while the run is in flight):
    how many tasks are done/running/failed and what's currently running."""
    if not rows:
        return {}
    done = running = submitted = failed = 0
    current: list[str] = []
    for r in rows:
        st = (r.get("status") or "").upper()
        if st in ("COMPLETED", "CACHED"):
            done += 1
        elif st == "RUNNING":
            running += 1
            current.append(_proc(r.get("name", "")))
        elif st in ("SUBMITTED", "PENDING"):
            submitted += 1
        elif st in ("FAILED", "ABORTED"):
            failed += 1
    total = len(rows)
    return {"total": total, "completed": done, "running": running,
            "submitted": submitted, "failed": failed,
            "pct": round(100.0 * done / total, 1) if total else 0.0,
            "current": sorted(set(current))[:8]}


def _tc_key(pipeline, revision, profile) -> str:
    return f"{(pipeline or '').strip()}|{(revision or '').strip()}|{(profile or '').strip()}"


def _task_count_path():
    from core.config import RUNTIME_DIR
    return Path(str(RUNTIME_DIR)) / "nf_task_counts.json"


def record_task_count(pipeline, revision, profile, total) -> None:
    """Remember a COMPLETED run's task count per (pipeline, revision, profile). Nextflow doesn't
    expose a task total mid-run (the DAG is dynamic), so a future run's progress bar uses this
    learned value to show a real done/expected fraction instead of a bare spinner. Best-effort."""
    try:
        total = int(total or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return
    try:
        p = _task_count_path()
        d = json.loads(p.read_text()) if p.exists() else {}
        d[_tc_key(pipeline, revision, profile)] = total
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(d))
    except Exception:  # noqa: BLE001
        pass


def expected_task_count(pipeline, revision, profile):
    """Typical task total for this (pipeline, revision, profile) from a prior completed run, or
    None if we've never finished one. An ESTIMATE (varies with the samplesheet) — the UI caps the
    bar below 100% until the run is actually done, and it self-corrects as runs complete.

    Fallback: if the exact (pipeline, revision, profile) key misses, reuse a total learned under a
    DIFFERENT profile of the same pipeline+revision. The profile mainly changes the input data, not
    the process graph, so e.g. a `-profile test` run's count is a fine first estimate for a real
    run — better than a bare spinner. This run then records its own exact key on completion."""
    try:
        p = _task_count_path()
        if not p.exists():
            return None
        d = json.loads(p.read_text()) or {}
        exact = d.get(_tc_key(pipeline, revision, profile))
        if exact:
            return exact
        # profile-agnostic: any completed run of the same pipeline+revision. Prefer the largest
        # (closest to a full run; the UI still caps <100% and self-corrects).
        pref = f"{(pipeline or '').strip()}|{(revision or '').strip()}|"
        cands = [v for k, v in d.items()
                 if k.startswith(pref) and isinstance(v, int) and v > 0]
        return max(cands) if cands else None
    except Exception:  # noqa: BLE001
        return None


def nextflow_job_progress(job: dict) -> dict:
    """Live progress for a Nextflow job, read from its incrementally-written trace.txt:
    {total, completed, running, submitted, failed, pct, current[], latest}. Powers the Jobs-card
    readout so an inline/local head shows task counts + the running stage instead of a bare
    spinner. {} when the job isn't a pipeline or has no trace yet. Cheap: one file read + parse.
    Works for inline AND Slurm heads (both write the trace under the run's project scratch)."""
    params = job.get("params") or {}
    if job.get("kind") != "run_nextflow" and not params.get("pipeline"):
        return {}
    run_id = params.get("run_id") or job.get("id")
    pid = params.get("project_id") or job.get("project_id") or "_workspace"
    if not run_id:
        return {}
    try:
        from core.data.workspace import project_work_dir
        trace = project_work_dir(str(pid)) / str(run_id) / "nf_reports" / "trace.txt"
    except Exception:  # noqa: BLE001
        return {}
    rows = parse_trace_rows(trace)
    prog = trace_progress(rows)
    if prog and rows:
        # 'current' (RUNNING rows) is the best "what's happening now", but Nextflow flushes the
        # trace lazily so it's often empty; the last row's process is a reliable stage fallback.
        prog["latest"] = _proc(rows[-1].get("name", ""))
    if prog:
        # A learned total (from a prior completed run) lets the card show a real done/expected
        # fraction; None → the UI falls back to an indeterminate bar + live counts.
        prog["total_expected"] = expected_task_count(
            params.get("pipeline"), params.get("revision"), params.get("profile"))
    return prog


# ── inline-pipeline hang watchdog (misc/inplace_submission.md) ─────────────────────────────
# An INLINE head runs in ABA's own allocation with NO per-task walltime, so a wedged container
# task (the Apptainer-cache-on-NFS deadlock) would stall the whole run silently forever. The
# watchdog (runner._inline_watchdog_loop) detects that from the FILESYSTEM + /proc — never from
# in-memory monitor state — so it survives an ABA restart: a fresh instance just re-derives every
# running head's health and keeps watching. These helpers are that verdict, kept pure/cheap.
_INLINE_STALL_MIN = float(os.environ.get("ABA_INLINE_STALL_MIN") or 20)   # whole-run silence budget
_STALL_CPU_SAMPLE_S = float(os.environ.get("ABA_INLINE_STALL_CPU_SAMPLE_S") or 3)
_STALL_CPU_MIN_JIFFIES = 5   # >~50ms of CPU across the resample ⇒ still computing, not deadlocked


def nextflow_work_dir(job: dict) -> Optional[Path]:
    """The Nextflow -work-dir for a job (where task <hash>/.command.* live), recomputed EXACTLY
    as run_nextflow_code does: <workdir_root>/<run_id>, else the run scratch's work/. None if
    unresolvable. Pure path math — safe to call from the watchdog every tick."""
    params = job.get("params") or {}
    run_id = params.get("run_id") or job.get("id")
    if not run_id:
        return None
    try:
        cfg = nextflow_config()
        if cfg.get("workdir_root"):
            return Path(os.path.expandvars(cfg["workdir_root"])) / str(run_id)
        from core.data.workspace import scratch_dir
        pid = params.get("project_id") or job.get("project_id") or "_workspace"
        return Path(scratch_dir(str(pid), str(run_id))) / "work"
    except Exception:  # noqa: BLE001
        return None


def _run_cpu_jiffies(run_dir: Optional[Path], run_id: str) -> Optional[int]:
    """Sum utime+stime (clock ticks) over /proc for processes whose cmdline names this run's
    work-dir or run_id — the Nextflow head + its task/container children. None if /proc is
    unavailable (non-Linux) or nothing matches (⇒ no live process for this run)."""
    tokens = [t for t in ((str(run_dir) if run_dir else ""), str(run_id)) if t]
    if not tokens:
        return None
    total = 0
    matched = False
    try:
        proc_iter = list(Path("/proc").glob("[0-9]*"))
    except OSError:
        return None
    for pdir in proc_iter:
        try:
            cmd = (pdir / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if not any(t in cmd for t in tokens):
            continue
        try:
            fields = (pdir / "stat").read_text().rsplit(")", 1)[1].split()   # after "(comm)"
            total += int(fields[11]) + int(fields[12])                       # utime, stime
            matched = True
        except (OSError, IndexError, ValueError):
            continue
    return total if matched else None


def nextflow_inline_silence(job: dict, *, now: Optional[float] = None) -> Optional[dict]:
    """FS-only gate for the hang watchdog: a suspect dict when an inline pipeline has ≥1 RUNNING
    task (a task dir with .command.begin but no .exitcode) AND nothing in the WHOLE run's work-dir
    has changed for _INLINE_STALL_MIN minutes — else None. Conservative on purpose: while ANY task
    still writes output the run is progressing, so a single quiet task can't trip it. The CPU
    cross-check (deadlock vs quiet-but-busy compute) is the loop's job — kept separate so it can
    `await` the resample instead of blocking the event loop."""
    params = job.get("params") or {}
    if job.get("kind") != "run_nextflow" and not params.get("pipeline"):
        return None
    wd = nextflow_work_dir(job)
    if not wd or not wd.exists():
        return None
    begins = list(wd.glob("*/*/.command.begin"))
    if not begins:
        return None
    task_dirs = {b.parent for b in begins}
    running = [d for d in task_dirs if not (d / ".exitcode").exists()]
    if not running:
        return None                       # nothing running → finalize handles it, not the watchdog
    newest = 0.0
    for d in task_dirs:
        for fn in (".command.log", ".command.out", ".command.err", ".exitcode"):
            try:
                newest = max(newest, (d / fn).stat().st_mtime)
            except OSError:
                continue
    if newest == 0.0:
        return None
    idle_s = (now if now is not None else time.time()) - newest
    if idle_s < _INLINE_STALL_MIN * 60:
        return None                       # something advanced recently → progressing
    return {"idle_min": round(idle_s / 60, 1), "running_tasks": len(running), "work_dir": str(wd)}


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def parse_failure(stderr: str, rows: list[dict], log_path: Optional[str] = None) -> dict:
    """On a non-zero pipeline, surface WHAT failed so the agent can ACT, not just an exit code:
      - failed_processes: FAILED/ABORTED tasks from the trace (a real compute failure);
      - error_excerpt:    Nextflow's 'Error executing process' / 'ERROR ~' block;
      - abort_cause:      the 'Session aborted -- Cause: …' reason for a PRE-TASK abort — bad
                          params, a missing/404 `--input`, a plugin/config error. Nextflow writes
                          those DETAILS only to .nextflow.log (stderr just says 'check the log'),
                          so we ALWAYS read the log too, not only when stderr is empty.
    A pre-task abort has no failed task, so without abort_cause the caller could only say
    'unknown process'; with it the agent sees e.g. '--input … does not exist' and can fix it."""
    failed = [f"{_proc(r.get('name',''))} (exit {r.get('exit')})"
              for r in rows if (r.get("status") or "").upper() in ("FAILED", "ABORTED")]
    stderr = stderr or ""
    log_text = ""
    if log_path:
        try:
            log_text = Path(log_path).read_text()
        except Exception:  # noqa: BLE001
            log_text = ""

    def _find(text: str, needles, n: int = 800) -> str:
        for needle in needles:
            i = text.find(needle)
            if i != -1:
                return _ANSI_RE.sub("", text[i:i + n]).strip()
        return ""

    # A task-level error block (a process actually ran and failed).
    block = (_find(stderr, ("Error executing process", "ERROR ~"))
             or _find(log_text, ("Error executing process", "ERROR ~")))
    # A pre-task abort cause — the reason lives after 'Session aborted -- Cause:' in the log;
    # trim the Groovy/thread stack that follows so the agent gets just the human message.
    abort = ""
    for src in (log_text, stderr):
        i = src.find("Session aborted -- Cause:")
        if i == -1:
            continue
        seg = _ANSI_RE.sub("", src[i + len("Session aborted -- Cause:"): i + 1400])
        for marker in ("\nThread[", "\n\tat ", "\n  java", "\n\n\n"):
            j = seg.find(marker)
            if j != -1:
                seg = seg[:j]
        abort = seg.strip()
        break
    return {"failed_processes": failed[:10], "error_excerpt": block, "abort_cause": abort}


def parse_trace_containers(trace_path) -> list[str]:
    """Unique container images from a `-with-trace` TSV (`container` column) — the
    per-process engine env for workflow provenance. [] on any miss."""
    try:
        lines = Path(trace_path).read_text().splitlines()
        if not lines:
            return []
        header = lines[0].split("\t")
        if "container" not in header:
            return []
        ci = header.index("container")
        seen: list[str] = []
        for ln in lines[1:]:
            parts = ln.split("\t")
            if len(parts) > ci:
                c = parts[ci].strip()
                if c and c not in ("-", "") and c not in seen:
                    seen.append(c)
        return seen
    except Exception:  # noqa: BLE001
        return []


# MultiQC general-stats headers carry a colour `scale` that encodes each metric's quality
# direction — the *module authors* set it, so reading it gives us "higher is better/worse" for
# free across every pipeline, with no per-metric or per-pipeline table. Green-at-high diverging
# scales (RdYlGn) mean higher-is-better; warm/red-at-high scales (OrRd, YlOrRd, Reds…) mean
# higher-is-worse. Neutral sequential/diverging scales (Blues, RdBu, GnBu…) imply no direction.
_SCALE_DIR = {"rdylgn": 1, "orrd": -1, "ylorrd": -1, "reds": -1, "oranges": -1, "ylorbr": -1}


def _scale_direction(scale):
    """+1 higher-is-better, -1 higher-is-worse, None if the scale implies no quality direction.
    Honors MultiQC's '-rev' suffix (which reverses the colour order → flips the direction)."""
    s = (scale or "").strip().lower()
    if not s:
        return None
    rev = s.endswith("-rev")
    base = s[:-4] if rev else s
    d = _SCALE_DIR.get(base)
    if d is None:
        return None
    return -d if rev else d


def parse_multiqc(outdir) -> dict:
    """Parse a finished pipeline's MultiQC output into a structured QC summary the
    agent can INTERPRET — the General Statistics table (per-sample headline metrics +
    their titles/descriptions), which tools contributed, and statistical outliers.
    Finds ``multiqc_data/multiqc_data.json`` anywhere under ``outdir``; {} if none.

    We deliberately don't hardcode pass/fail QC thresholds (those are metric- and
    study-specific) — we surface the legible table + generic >2σ outliers and let the
    agent judge ('sample X has high duplication')."""
    op = Path(outdir)
    found = sorted(op.rglob("multiqc_data.json"), key=lambda p: len(p.parts))
    if not found:
        return {}
    try:
        data = json.loads(found[0].read_text())
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(data, dict):
        return {}

    # General-stats data + headers. MultiQC emits these as EITHER a LIST of per-module blocks
    # (older MultiQC) OR a DICT keyed by module (newer / nf-schema era) — the inner block shape
    # is the same. Normalize to (module, block) pairs and key every metric by "<module>:<col>",
    # so a column that repeats across modules (e.g. FastQC raw vs trimmed both 'percent_gc')
    # stays distinct instead of one silently overwriting the other in the per-sample table.
    def _modules(x):
        if isinstance(x, dict):
            return list(x.items())                        # {module: block}
        if isinstance(x, list):
            return list(enumerate(x))                     # [block, …] → module = index (parallel)
        return []

    headers: dict[str, dict] = {}                         # "mod:col" -> metadata
    for mod, hblock in _modules(data.get("report_general_stats_headers")):
        if not isinstance(hblock, dict):
            continue
        for col, meta in hblock.items():
            if isinstance(meta, dict):
                headers[f"{mod}:{col}"] = {"title": (meta.get("title") or col),
                                           "description": (meta.get("description") or "").strip(),
                                           "namespace": (meta.get("namespace") or "").strip(),
                                           "scale": (meta.get("scale") or "").strip()}
    per_sample: dict[str, dict] = {}                      # sample -> {"mod:col": val}
    for mod, dblock in _modules(data.get("report_general_stats_data")):
        if not isinstance(dblock, dict):
            continue
        for sample, vals in dblock.items():
            if isinstance(vals, dict):
                bag = per_sample.setdefault(str(sample), {})
                for col, val in vals.items():
                    bag[f"{mod}:{col}"] = val

    samples = sorted(per_sample)[:100]
    metric_keys = (list(headers) or sorted({k for v in per_sample.values() for k in v}))[:30]
    title = lambda k: (headers.get(k, {}).get("title") or k.split(":", 1)[-1])
    # A metric title can still repeat across modules (FastQC vs Picard both "% Dups"). Assign a
    # UNIQUE display title per metric: bare title when unique, else + module namespace, else a
    # numeric suffix — guaranteeing 1:1 so the per-sample table never collides.
    _tcount: dict[str, int] = {}
    for k in metric_keys:
        _tcount[title(k)] = _tcount.get(title(k), 0) + 1
    _disp: dict[str, str] = {}
    _used: set = set()
    for k in metric_keys:
        t = title(k)
        ns = (headers.get(k, {}).get("namespace") or "").strip()
        cand = t if _tcount.get(t, 0) <= 1 else (f"{t} ({ns})" if ns else t)
        if cand in _used:
            n = 2
            while f"{cand} #{n}" in _used:
                n += 1
            cand = f"{cand} #{n}"
        _used.add(cand)
        _disp[k] = cand

    def disp(k: str) -> str:
        return _disp.get(k, title(k))

    # Directionality (higher-is-better/worse) harvested from MultiQC's colour `scale` — lets the
    # agent flag concerns on the BAD side and not cry wolf over an outlier in the good direction.
    def _dir(k):
        return _scale_direction(headers.get(k, {}).get("scale"))
    _lbl = {1: "higher_better", -1: "higher_worse"}
    metrics = [{"key": k, "title": disp(k), "description": headers.get(k, {}).get("description", ""),
                "direction": _lbl.get(_dir(k))} for k in metric_keys]
    rows: dict[str, dict] = {}
    for s in samples:
        rows[s] = {disp(k): per_sample[s][k] for k in metric_keys if k in per_sample[s]}

    # Generic, metric-agnostic outliers via the modified z-score (Iglewicz–Hoaglin): median +
    # MAD, flag |z| ≥ 3.5. Robust to the outliers themselves (a single bad sample doesn't
    # inflate the dispersion the way mean+stdev does). Needs ≥4 numeric samples.
    outliers: list[dict] = []
    for k in metric_keys:
        pairs = [(s, per_sample[s][k]) for s in samples
                 if isinstance(per_sample[s].get(k), (int, float)) and not isinstance(per_sample[s].get(k), bool)]
        nums = [v for _, v in pairs]
        if len(nums) < 4:
            continue
        d = _dir(k)                         # metric directionality (or None)
        med = statistics.median(nums)
        devs = [abs(v - med) for v in nums]
        mad = statistics.median(devs)
        meanad = statistics.fmean(devs)
        if mad == 0 and meanad == 0:
            continue                        # every sample identical → no spread, nothing to flag
        # Deviations under 1% of the median magnitude are noise, not outliers. This gate stops
        # a near-zero MAD (majority of samples tied) from blowing the z-score up and flagging
        # trivially-different samples — the failure mode the plain MAD form had. It only affects
        # the auto-flagged list; the full per-sample table is still surfaced for the agent.
        min_dev = 0.01 * abs(med)
        for s, v in pairs:
            dev = v - med
            if abs(dev) < min_dev:
                continue
            # MAD is the preferred robust scale; when it collapses to 0 (majority tied), fall
            # back to the mean abs deviation — Iglewicz–Hoaglin's own MAD==0 remedy.
            z = 0.6745 * dev / mad if mad > 0 else dev / (1.253314 * meanad)
            if abs(z) >= 3.5:
                # concern = outlier on the metric's BAD side (opposite its good direction);
                # None when direction is unknown — the agent then judges from the value + docs.
                concern = None if d is None else ((d > 0) != (dev > 0))
                outliers.append({"sample": s, "metric": disp(k), "value": v, "z": round(z, 1),
                                 "side": "high" if dev > 0 else "low", "concern": concern})
    tools = sorted((data.get("report_data_sources") or {}).keys())
    report = next((str(p.relative_to(op)) for p in op.rglob("multiqc_report.html")), None)
    return {"n_samples": len(per_sample), "tools": tools, "metrics": metrics,
            "samples": rows, "outliers": outliers[:25], "report": report}


def publish_multiqc_report(outdir, project_id: str, run_id: str) -> Optional[str]:
    """Copy the run's self-contained `multiqc_report.html` into the project artifacts store
    under a deterministic name, so it's servable + clickable at `/artifacts/<pid>/<name>`.
    Returns that URL, or None if there's no report. MultiQC's default HTML embeds all its
    assets, so the single file renders standalone. Idempotent (overwrites on re-run). This is
    what lets the agent hand the user an openable report instead of a dead `file://` path."""
    try:
        op = Path(outdir)
        found = sorted(op.rglob("multiqc_report.html"), key=lambda p: len(p.parts))
        if not found:
            return None
        from core.config import project_artifacts_dir
        dest_dir = project_artifacts_dir(str(project_id))
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = f"multiqc-{run_id}.html"
        shutil.copy2(found[0], dest_dir / name)
        return f"/artifacts/{project_id}/{name}"
    except Exception:  # noqa: BLE001 — best-effort; a missing report just means no link
        return None


# ── the reusable execution+harvest function ──────────────────────────────────
def run_nextflow_code(pipeline: str, *, project_id: str, run_id: Optional[str] = None,
                      revision: Optional[str] = None, profile: Optional[str] = None,
                      params: Optional[dict] = None, outdir: Optional[str] = None,
                      work_dir: Optional[str] = None, timeout_s: int = 3600,
                      execution: Optional[str] = None, local_resources: Optional[dict] = None,
                      cancel_token=None, stream: bool = False) -> dict:
    """Run a Nextflow pipeline head process in the project workspace, harvest its
    --outdir, and return the standard background result_obj (returncode/stdout/
    stderr/plots/tables/files/cwd) plus a `workflow` provenance block. Resolves
    `nextflow` from PATH (a Slurm head job `module load`s it; see slurm_submitter)
    and otherwise conda-installs it for the off-cluster local path."""
    import uuid as _uuid
    from core.data.workspace import scratch_dir
    from core.exec import MaterializingExecutor, Provisioning
    from core.exec.run import harvest_artifacts
    from core.exec.output_cap import snip_middle

    run_id = run_id or _uuid.uuid4().hex
    scratch = scratch_dir(str(project_id), str(run_id))
    cfg = nextflow_config()
    prof = merged_profile(profile, cfg["profiles"])
    outdir = outdir or str(Path(scratch) / "results")
    # Nextflow's work-dir (large, churny intermediates) → fast scratch if the site
    # gave a root; else under the run scratch. A per-run subdir keeps runs isolated.
    if work_dir is None:
        if cfg["workdir_root"]:
            work_dir = str(Path(os.path.expandvars(cfg["workdir_root"])) / str(run_id))
        else:
            work_dir = str(Path(scratch) / "work")
    reports = Path(scratch) / "nf_reports"
    reports.mkdir(parents=True, exist_ok=True)
    clear_stale_reports(reports)   # else a -resume aborts on the prior run's trace/report files
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    ex = MaterializingExecutor()
    # Get `nextflow` on PATH. The Slurm head's job.sh already `module load`ed it (shutil.which
    # finds it); an INLINE run (LocalSubmitter, no job.sh) has NOT — so if a module is
    # configured, capture its env overlay in-process. Conda-install only off-cluster (no module,
    # not on PATH).
    module_overlay: dict = {}
    if not shutil.which("nextflow"):
        _nf_bin = nextflow_bin_dir(cfg.get("bin"))
        if _nf_bin:
            # Self-installed Nextflow on shared FS (slim SIF / personal) — prepend its dir to PATH.
            module_overlay = {"PATH": _nf_bin + os.pathsep + os.environ.get("PATH", "")}
        elif cfg.get("module"):
            try:
                from core.exec.modules import module_env_overlay
                module_overlay = module_env_overlay(cfg["module"])
            except Exception:  # noqa: BLE001
                module_overlay = {}
    try:
        if shutil.which("nextflow") or module_overlay:
            menv = ex.materialize(Provisioning(), cancel_token=cancel_token)
        else:
            menv = ex.materialize(Provisioning(conda={"channel": "bioconda", "spec": "nextflow"}),
                                  cancel_token=cancel_token)
    except Exception as e:  # noqa: BLE001
        return {"error": f"Could not provision nextflow: {e}"}

    # Persistent NXF_HOME (plugins/assets/scm) when configured (shared-FS deploys share plugins
    # across runs); else the per-run scratch .nextflow (today's behavior). The -resume cache +
    # work-dir stay per-run (work_dir/cwd), so a persistent NXF_HOME never causes cross-run races.
    _nxf_home = cfg.get("home") or str(Path(scratch) / ".nextflow")
    try:
        Path(_nxf_home).mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        _nxf_home = str(Path(scratch) / ".nextflow")   # fall back to per-run if the shared dir isn't writable
    env_vars = {"NXF_HOME": _nxf_home}
    # Nextflow 25.10+/26.x default to the strict v2 config parser, which rejects
    # `manifest`/`validation` references still used by much of the current nf-core
    # catalog (e.g. rnaseq 3.21.0 → 6 "is not defined" compile errors; the run dies
    # before task 1). Pin the legacy parser so those pipelines parse on our modern
    # engine. This is a *native* Nextflow var, set here alongside NXF_HOME — NOT a new
    # ABA_NEXTFLOW_* knob (no added config surface); an explicit ambient NXF_SYNTAX_PARSER
    # still wins if a v2-only pipeline ever needs it.
    env_vars["NXF_SYNTAX_PARSER"] = os.environ.get("NXF_SYNTAX_PARSER", "v1")
    env_vars.update(module_overlay)          # nextflow (+ its module's java/deps) onto PATH for inline
    if cfg["singularity_cachedir"]:
        env_vars["NXF_SINGULARITY_CACHEDIR"] = cfg["singularity_cachedir"]
    # Apptainer/Singularity hang INDEFINITELY when their working tmp/cache sit on this cluster's
    # NFS home (file-lock stall — reproduced on CBE clip nodes: even `apptainer exec <img> true`
    # never returns and ignores SIGKILL). The default APPTAINER_CACHEDIR is $HOME/.apptainer/cache
    # and the inherited env had APPTAINER_TMPDIR on $HOME. Pin both to node-local /tmp (fast, off
    # NFS, present on every node) so container tasks actually run — inline AND Slurm (sbatch
    # inherits this env). This is what makes containerized local execution work here. Overridable.
    _ap = (os.environ.get("ABA_APPTAINER_TMPDIR")
           or f"/tmp/aba-apptainer-{os.environ.get('USER') or os.environ.get('LOGNAME') or 'u'}")
    try:
        Path(_ap).mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        pass
    for _k in ("APPTAINER_TMPDIR", "SINGULARITY_TMPDIR", "APPTAINER_CACHEDIR", "SINGULARITY_CACHEDIR"):
        env_vars[_k] = _ap
    # Nextflow head runs on this Java (≥17 for nf-schema); compose ON TOP of the module overlay
    # so java/21 wins over the module's pinned java/11 (see java_env()).
    env_vars.update(java_env(cfg.get("java_home"), base={**os.environ, **module_overlay}))

    # Execution mode: "local" runs tasks on the head node (one allocation); "slurm" fans out.
    mode = (execution or cfg.get("execution") or "slurm").lower()
    extra_configs: list[str] = []
    if mode == "local":
        loc = cfg.get("local") or {}
        lr = local_resources or {}            # estimate-derived (sized to the heaviest task)
        cores = lr.get("cores") or loc.get("cores") or 8
        mem_gb = lr.get("mem_gb") or loc.get("mem_gb") or 32
        lc = Path(scratch) / "local_executor.config"
        lc.write_text(local_executor_config(cores, mem_gb))
        extra_configs.append(str(lc))

    cmd = nextflow_command(pipeline, revision=revision, profile=prof, outdir=outdir,
                           params=params, work_dir=work_dir, reports_dir=str(reports),
                           config_file=cfg.get("config_file"), extra_configs=extra_configs)
    res = ex.exec(menv, cmd, cwd=str(scratch), cancel_token=cancel_token,
                  timeout_s=timeout_s, env_vars=env_vars, stream=stream)

    if getattr(res, "timed_out", False):
        return {"error": f"nextflow run timed out ({timeout_s}s limit)"}
    if getattr(res, "cancelled", False):
        return {"status": "cancelled",
                "note": "Nextflow run was cancelled by the user. No further work happened."}

    plots, tables, files, out_files = [], [], [], []
    op = Path(outdir)
    if op.exists():
        try:
            plots, tables, files, _warns = harvest_artifacts(op, project_id=str(project_id))
        except Exception:  # noqa: BLE001 — harvest is best-effort
            plots, tables, files = [], [], []
        out_files = sorted(str(p.relative_to(op)) for p in op.rglob("*") if p.is_file())[:100]
    # P1: also harvest the run reports (report.html / timeline.html live in the
    # reports dir, not --outdir) so they attach to the Run as pinnable artifacts.
    try:
        _rp, _rt, rep_files, _ = harvest_artifacts(reports, project_id=str(project_id))
        files = list(files) + list(rep_files)
    except Exception:  # noqa: BLE001
        pass

    # P1: monitoring. Parse the trace once → a per-task summary; diagnose failures.
    trace_rows = parse_trace_rows(reports / "trace.txt")
    summary = trace_summary(trace_rows)
    failure = {}
    if res.returncode != 0:
        failure = parse_failure(res.stderr or "", trace_rows, str(Path(scratch) / ".nextflow.log"))
    # P3b: interpret the QC. Parse the pipeline's MultiQC general-stats into a per-sample
    # summary + outliers the agent reasons over (best-effort; {} if no MultiQC).
    multiqc = {}
    if op.exists():
        try:
            multiqc = parse_multiqc(op)
        except Exception:  # noqa: BLE001
            multiqc = {}
        # Publish the report to a servable /artifacts URL so the agent can hand the user a
        # CLICKABLE report (not a dead file:// path). Kept alongside the parsed QC.
        try:
            _report_url = publish_multiqc_report(op, str(project_id), str(run_id))
            if _report_url:
                multiqc["report_url"] = _report_url
        except Exception:  # noqa: BLE001
            pass

    nf_ver = ""
    try:
        vr = ex.exec(menv, ["nextflow", "-version"], cwd=str(scratch), timeout_s=30)
        m = re.search(r"version\s+([0-9][0-9.]*)", (getattr(vr, "stdout", "") or "")
                      + (getattr(vr, "stderr", "") or ""))
        nf_ver = m.group(1) if m else ""
    except Exception:  # noqa: BLE001
        nf_ver = ""

    cmd_str = " ".join(cmd)
    reports_rel = {"trace": "nf_reports/trace.txt", "report": "nf_reports/report.html",
                   "timeline": "nf_reports/timeline.html"}
    out: dict = {
        "returncode": res.returncode,
        "stdout": snip_middle(res.stdout or ""),
        "stderr": snip_middle(res.stderr or ""),
        "plots": plots, "tables": tables, "files": files,
        "cwd": str(scratch),
        "outdir": outdir, "outputs": out_files,
        "task_summary": summary,          # surfaced to the agent in the tool result
        "multiqc": multiqc,               # P3b: per-sample QC the agent interprets
        "execution_mode": "workflow",
        # consumed by runner._write_exec_record_for_job (kind:workflow branch)
        "workflow": {
            "engine": {"name": "nextflow", "version": nf_ver},
            "per_process_images": parse_trace_containers(reports / "trace.txt"),
            "pipeline": pipeline, "revision": revision, "profile": prof,
            "params": params or {}, "outputs": out_files[:50], "command": cmd_str,
            "task_summary": summary, "reports": reports_rel, "multiqc": multiqc,
        },
        "command": cmd_str,
    }
    # An actionable failure diagnosis → _finalize_job's failed-path note + the
    # continuation, instead of a bare non-zero exit.
    if res.returncode != 0:
        out["workflow"]["failure"] = failure
        procs = failure.get("failed_processes") or []
        excerpt = failure.get("error_excerpt") or ""
        abort = failure.get("abort_cause") or ""
        if procs:
            out["error"] = (f"Nextflow pipeline failed (exit {res.returncode}). Failed: "
                            f"{', '.join(procs)}." + (f"\n{excerpt}" if excerpt else ""))
        elif abort:
            # No task ran — a params/input/config problem, NOT a compute failure. Tell the agent
            # so it fixes the params (or the profile/input) instead of retrying the same command.
            out["error"] = (f"Nextflow aborted before running any task (exit {res.returncode}) — a "
                            f"parameter/input/config problem, not a compute failure:\n{abort}")
        else:
            out["error"] = (f"Nextflow pipeline failed (exit {res.returncode})."
                            + (f"\n{excerpt}" if excerpt else ""))
    else:
        # Learn this pipeline's task count so the NEXT run's progress bar shows a real fraction.
        record_task_count(pipeline, revision, profile, (summary or {}).get("total_tasks"))
    return out
