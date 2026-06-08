"""Bio run-exec cluster (WU-3): `run_python`, `run_r`, and the
kernel-CWD / namespace-preview / prior-run-preamble helpers they share.

Extracted from bio/tools/__init__.py to keep the package legible. Other
modules in the cluster (e.g. open_run_tool in __init__.py) import these
helpers; the package __init__ re-exports them so any cross-module
caller using `from content.bio.tools import _run_scratch_cwd` keeps
working unchanged.

All non-stdlib imports are kept lazy inside the functions — matches the
pattern in the rest of bio/tools and keeps module-import cost low.

Stage 1 exec records (misc/exec_records_and_versioning.md): on each
successful kernel-path call, `_write_exec_record` writes one row to
`execution_records` + a JSON sidecar at `<cwd>/.exec/<exec_id>.json`.
Failure to write is logged but never blocks the tool result — provenance
is best-effort, not a precondition for serving the user.
"""
from __future__ import annotations
import logging
import uuid
from typing import Optional

_log = logging.getLogger(__name__)


def _run_scratch_cwd(project_id: str, thread_id: str):
    """Working dir for this run_python/run_r cell: the active Run's own output
    directory (so a pipeline's files group into one browsable bundle), else the
    shared per-thread scratch dir. The Run's dir is recorded as its artifact_path
    by runs.open_run / the ambient analysis."""
    from pathlib import Path
    from core.data.workspace import scratch_dir
    try:
        from content.bio.lifecycle.runs import active_run_id
        from core.graph.entities import get_entity
        rid = active_run_id(str(thread_id))
        if rid:
            ap = (get_entity(rid) or {}).get("artifact_path")
            if ap:
                p = Path(ap)
                p.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:  # noqa: BLE001 — fall back to the thread dir
        pass
    return scratch_dir(str(project_id), f"thread-{thread_id}")


def _ensure_kernel_cwd(sess, lang: str, cwd) -> None:
    """Switch the persistent kernel into `cwd` (the active Run's output dir) and
    re-point the WORK_DIR variable/env — only when it changed, so it's a no-op on
    repeat cells. Relative writes (savefig('x.png'), saveRDS(o,'x.rds')) then land
    in the Run's folder, captured as that Run's outputs.

    Sets `sess._aba_cwd_just_switched` to the PREVIOUS cwd when the cwd actually
    moves — the next run_python/run_r call reads + clears that flag and emits a
    one-shot 'Files from prior runs' preamble so bare filenames the agent learned
    in the old cwd are recoverable as absolute paths. (Fix B for #324 session
    drift: less-invasive variant — cwd architecture untouched.)"""
    path = str(cwd)
    prev = getattr(sess, "_aba_cwd", None)
    if prev == path:
        return
    try:
        if lang == "r":
            snippet = (f'setwd({path!r}); Sys.setenv(WORK_DIR={path!r}); WORK_DIR <- {path!r}')
        else:
            snippet = (f'import os as _os; _os.chdir({path!r}); '
                       f'_os.environ["WORK_DIR"]={path!r}; WORK_DIR={path!r}')
        sess.execute(snippet, timeout_s=15)
        sess._aba_cwd = path
        if prev is not None:                       # genuine switch, not first-time set
            sess._aba_cwd_just_switched = prev
    except Exception:  # noqa: BLE001 — best-effort; the run still works in the kernel's prior cwd
        pass


# ── A: kernel namespace preview ────────────────────────────────────────────────
# Run a probe cell after the user's code to enumerate user-defined objects.
# Output marked with __ABA_NS_BEGIN__/END__ delimiters so probe stdout can't be
# confused with the user's. Probe failure → empty list (the real result must
# survive).

_NS_PROBE_PY = """
def __aba_ns_probe():
    out = []
    skip = {'In','Out','exit','quit','get_ipython','DATA_DIR','WORK_DIR','ARTIFACTS_DIR'}
    for k, v in list(globals().items()):
        if k.startswith('_') or k in skip: continue
        try:
            t = type(v)
            if callable(v) or isinstance(v, type) or t.__name__ == 'module': continue
            mod = getattr(t, '__module__', '') or ''
            info = ''
            try:
                if hasattr(v, 'shape'):
                    info = ' ' + 'x'.join(str(x) for x in v.shape)
                elif hasattr(v, '__len__'):
                    info = f' len={len(v)}'
            except Exception: pass
            lab = t.__name__ if mod in ('','builtins') else f"{mod}.{t.__name__}"
            out.append(f"{k}: {lab}{info}")
        except Exception: continue
        if len(out) >= 30: out.append('...'); break
    print('__ABA_NS_BEGIN__'); print(*out, sep='\\n'); print('__ABA_NS_END__')
__aba_ns_probe(); del __aba_ns_probe
"""

_NS_PROBE_R = r"""
local({
  vars <- ls(envir=globalenv())
  vars <- vars[!startsWith(vars,'.') & !(vars %in% c('DATA_DIR','WORK_DIR'))]
  out <- character(0)
  for (n in vars) {
    v <- tryCatch(get(n, envir=globalenv()), error=function(e) NULL)
    if (is.null(v) || is.function(v)) next
    cls <- tryCatch(class(v)[1], error=function(e) 'unknown')
    sz  <- tryCatch({
      if (!is.null(dim(v))) paste0(dim(v), collapse='x')
      else if (!is.null(length(v))) paste0('len=', length(v))
      else ''
    }, error=function(e) '')
    out <- c(out, paste0(n, ': ', cls, if (nzchar(sz)) paste0(' ', sz) else ''))
    if (length(out) >= 30) { out <- c(out, '...'); break }
  }
  cat('__ABA_NS_BEGIN__\n'); for (l in out) cat(l,'\n',sep=''); cat('__ABA_NS_END__\n')
})
"""


def _kernel_namespace_preview(sess, lang: str) -> list[str]:
    try:
        res = sess.execute(_NS_PROBE_R if lang == "r" else _NS_PROBE_PY, timeout_s=15)
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        if "__ABA_NS_BEGIN__" not in out: return []
        chunk = out.split("__ABA_NS_BEGIN__", 1)[1].split("__ABA_NS_END__", 1)[0]
        return [ln.strip() for ln in chunk.splitlines() if ln.strip()]
    except Exception:  # noqa: BLE001
        return []


# ── B (less-invasive): one-shot prior-run files preamble on cwd switch ────────
# When the kernel cwd just moved to a different Run's dir, list the most-recent
# N files from EARLIER runs in this thread with their absolute paths — so bare
# filenames the agent learned in the old cwd ("GSM5746268_processed.h5ad")
# resolve again ("→ /workspace/.../ana_40e84b23/GSM5746268_processed.h5ad").
# Returns "" when there's nothing to surface.

# Skip noise from cache/aux/build files in prior-run dirs (these clutter the
# listing without ever being inputs the agent would want to re-open).
_PREAMBLE_SKIP_SUFFIXES = (".log", ".pyc", ".cache", ".tmp", ".lock", ".swp")
_PREAMBLE_SKIP_PREFIXES = (".", "_")


def _prior_run_files_preamble(project_id: str, thread_id: str,
                              current_run_id: str | None,
                              max_runs: int = 4, max_files: int = 12,
                              max_scratch_files: int = 12,
                              cwd: str | None = None) -> str:
    """Inject a small, focused orientation block at the moment the cwd shifts
    (a new run opens, the kernel restarts, etc.). Lists what's reachable from
    the new cwd that ISN'T in it, so bare-filename loads recover gracefully.
    Three sources, kept visually distinct:

      1. Registered datasets in this project (canonical paths + layout
         hints when known). Earlier confusion: agents guessed
         `DATA_DIR/<dataset>` instead of using the registered path.
      2. Files written inside any of this thread's prior Runs.
      3. Files in the thread's SHARED scratch dir (ad-hoc downloads, the
         /tmp/<GSM>-confusion gap).

    Filtering: includes directories (registered dataset roots are dirs); skips
    obvious noise (dotfiles, .log/.pyc/etc.); no extension whitelist, so .gz
    triplets, .mtx, etc. all surface. The block is text appended ABOVE the
    tool's stdout in the SAME tool_result, fires only at cwd shifts.
    """
    try:
        from core.graph.entities import list_entities
        from core.data.workspace import scratch_dir
        from pathlib import Path
        thread_id = str(thread_id or "")
        if not thread_id: return ""

        def _keep(name: str) -> bool:
            if not name: return False
            if name.startswith(_PREAMBLE_SKIP_PREFIXES): return False
            n = name.lower()
            return not any(n.endswith(s) for s in _PREAMBLE_SKIP_SUFFIXES)

        # (1) Registered datasets — name + path + layout_hint (if recorded).
        datasets: list[tuple[str, str, str]] = []   # (title, path, hint)
        try:
            for d in list_entities(type_filter="dataset", include_archived=False):
                ap = d.get("artifact_path") or ""
                if not ap: continue
                title = (d.get("title") or d.get("id") or "").strip()
                md = d.get("metadata") or {}
                hint = (md.get("layout_hint") or "").strip()
                datasets.append((title, ap, hint))
        except Exception:  # noqa: BLE001
            pass

        # (2) Prior-run files.
        run_mapped: list[tuple[str, str]] = []
        seen_names: set[str] = set()
        scanned = 0
        for e in reversed(list_entities(type_filter="analysis", include_archived=False)):
            md = e.get("metadata") or {}
            if md.get("thread_id") != thread_id: continue
            if e["id"] == current_run_id: continue
            ap = e.get("artifact_path") or ""
            if not ap: continue
            p = Path(ap)
            if not p.is_dir(): continue
            scanned += 1
            files = []
            try:
                for f in p.iterdir():
                    if f.is_dir(): continue
                    if not _keep(f.name): continue
                    files.append(f)
            except OSError:
                continue
            files.sort(key=lambda f: f.stat().st_mtime if f.exists() else 0, reverse=True)
            for f in files:
                if f.name in seen_names: continue
                seen_names.add(f.name)
                run_mapped.append((f.name, str(f)))
                if len(run_mapped) >= max_files: break
            if len(run_mapped) >= max_files or scanned >= max_runs: break

        # (3) Thread shared-scratch files + dirs.
        scratch_mapped: list[tuple[str, str]] = []
        try:
            sp = scratch_dir(str(project_id or "default"), f"thread-{thread_id}")
            if sp.is_dir():
                cands = []
                for entry in sp.iterdir():
                    if not _keep(entry.name): continue
                    cands.append(entry)
                cands.sort(key=lambda f: f.stat().st_mtime if f.exists() else 0, reverse=True)
                for f in cands:
                    if f.name in seen_names: continue
                    # Skip directories that are registered datasets — they
                    # already appear in section (1).
                    if f.is_dir() and any(str(f) == ap for _, ap, _ in datasets):
                        continue
                    seen_names.add(f.name)
                    suffix = "/" if f.is_dir() else ""
                    scratch_mapped.append((f.name + suffix, str(f)))
                    if len(scratch_mapped) >= max_scratch_files: break
        except Exception:  # noqa: BLE001
            pass

        if not datasets and not run_mapped and not scratch_mapped: return ""
        lines: list[str] = [
            "── Workspace orientation (cwd just shifted) ──",
        ]
        if cwd:
            lines.append(f"cwd: {cwd}  (bare filenames in your code land here)")
        lines.append("")
        if datasets:
            lines.append("Registered datasets in this project (canonical paths — use verbatim):")
            for title, path, hint in datasets:
                label = title or path.rsplit("/", 1)[-1]
                tail = f"  [{hint}]" if hint else ""
                lines.append(f"  - {label} → {path}{tail}")
            lines.append("")
        if run_mapped:
            lines.append("Files from prior runs in this thread:")
            for name, full in run_mapped:
                lines.append(f"  - {name} → {full}")
            lines.append("")
        if scratch_mapped:
            lines.append("Thread shared-scratch (your ad-hoc downloads / intermediates):")
            for name, full in scratch_mapped:
                lines.append(f"  - {name} → {full}")
        return "\n".join(lines).rstrip() + "\n"
    except Exception:  # noqa: BLE001
        return ""


def _write_exec_record(*, lang: str, ctx: dict | None, code: str, cwd,
                        sess, started_iso: str, started_ts: float,
                        res, plots: list, tables: list, files: list) -> Optional[str]:
    """Write one execution_records row + JSON sidecar after a successful
    kernel-path tool dispatch. Returns the exec_id, or None on any failure
    (logged, swallowed — provenance is best-effort).

    `started_iso` is the ISO timestamp at dispatch start; `started_ts` is
    the matching monotonic time used to compute wall_time_s. `res` is the
    KernelResult from sess.execute. plots/tables/files come straight from
    harvest_artifacts."""
    try:
        import time as _time
        from datetime import datetime, timezone
        from core.graph import exec_records as _er
        from core.exec.fingerprint import (
            code_hash, env_fingerprint, package_versions_for_session,
        )
        from core.exec.output_cap import snip_middle
        from content.bio.lifecycle.runs import active_run_id

        thread_id = str((ctx or {}).get("thread_id") or "default")
        tool_use_id = (ctx or {}).get("tool_use_id")
        # active_run_id needs the same thread_id used elsewhere; resolved
        # via the entities table, so missing → None (scratch).
        run_id_ent = None
        try:
            run_id_ent = active_run_id(thread_id)
        except Exception:  # noqa: BLE001
            pass

        # Package versions + env fingerprint — cached on the session for
        # 10 min so back-to-back calls don't re-probe. Copy the dict before
        # popping the lang-version field so we don't mutate the cache (a
        # mutated cache → empty lang_ver on subsequent calls → different
        # fingerprint, which would break reproduction-drift detection).
        pkg_cached = package_versions_for_session(sess, lang)
        if isinstance(pkg_cached, dict):
            pkg = {k: v for k, v in pkg_cached.items() if k != "__lang_version__"}
            lang_ver = pkg_cached.get("__lang_version__", "")
        else:
            pkg, lang_ver = {}, ""
        ef = env_fingerprint(lang_ver, pkg)

        # Build produced[] in a uniform shape across kinds. The harvester
        # returns three lists; we union them into one stream addressable
        # as <exec_id>:<kind>:<idx>.
        produced: list[dict] = []
        for i, p in enumerate(plots or []):
            produced.append({"kind": "figure", "idx": i,
                             "url": p.get("url"),
                             "name": p.get("original_name") or p.get("name")})
        for i, t in enumerate(tables or []):
            produced.append({"kind": "table", "idx": i,
                             "url": t.get("url"), "name": t.get("name")})
        for i, f in enumerate(files or []):
            produced.append({"kind": "file", "idx": i,
                             "url": f.get("url"), "name": f.get("name")})

        completed_iso = datetime.now(timezone.utc).isoformat()
        wall_s = max(0.0, _time.time() - started_ts)
        # Status from KernelResult — Stage 1 only writes on the success
        # path, so timed_out/cancelled won't get here; we still derive
        # robustly so future expansion is trivial.
        if getattr(res, "timed_out", False):
            status = "timeout"
        elif getattr(res, "cancelled", False):
            status = "cancelled"
        elif (getattr(res, "returncode", 0) or 0) != 0:
            status = "error"
        else:
            status = "ok"

        eid = _er.create(
            thread_id=thread_id,
            run_id=run_id_ent,
            tool_use_id=tool_use_id,
            tool_name=f"run_{lang}",
            status=status,
            code=code or "",
            code_hash=code_hash(code or ""),
            started_at=started_iso,
            completed_at=completed_iso,
            cwd=cwd,
            payload={
                "executor": f"kernel:{lang}",
                "language": lang,
                "language_version": lang_ver,
                "package_versions": pkg,
                "env_fingerprint": ef,
                "produced": produced,
                "stdout_tail": snip_middle(res.stdout or ""),
                "stderr_tail": snip_middle(res.stderr or ""),
                "exit_code": getattr(res, "returncode", 0),
                "wall_time_s": wall_s,
            },
        )
        return eid
    except Exception as e:  # noqa: BLE001 — never block the user-visible result
        _log.warning("exec_records: write failed for run_%s: %s", lang, e)
        return None


def run_python(input_: dict, ctx: dict | None = None) -> dict:
    """Run Python in the project's scratch workspace via the shared executor.

    P0 (data.md / capdat_impl.md): the run executes in a per-run scratch dir
    under WORK_DIR (the agent reads/writes intermediates there freely, by plain
    path) and goes through LocalSubprocessExecutor so the exec + cancellation +
    timeout contract is shared with future executors. Kept outputs (*.png/*.csv)
    are still moved to the content-addressed artifact store and returned as
    plots/tables — the on_post_tool registration hook is unchanged. Scratch
    persists across the run's turns and is GC'd on a TTL; it is NOT deleted
    here, so the agent can revisit its working files."""
    import time as _time
    from core.exec.run import run_python_code, harvest_artifacts
    from core.exec import LocalRouter
    from core.config import KERNEL_ENABLED
    from core import projects

    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 300), 1800))
    cancel_token = (ctx or {}).get("cancel_token")
    project_id = projects.current() or "default"
    thread_id = (ctx or {}).get("thread_id") or "default"

    # Lane selection (kernels.md §7): background > fresh > interactive.
    # - background: stateless job, deferred result the guide loop resumes from.
    # - fresh: stateless one-shot subprocess (isolated/reproducible; no session).
    # - interactive (default): the thread's persistent kernel (state persists).
    # timeout_s is a CEILING, not an estimate; routing to background keys on the
    # agent's estimated_runtime_min so a defensive timeout doesn't mis-background.
    override = "background" if input_.get("background") else None
    est_min = float(input_.get("estimated_runtime_min") or 0)
    choice = LocalRouter().route(estimate={"runtime_min": est_min}, override=override)
    if choice.location == "background":
        from core.jobs.runner import submit_python_job
        from content.bio.lifecycle.runs import active_run_id
        job = submit_python_job(code, title=input_.get("title") or "Background analysis",
                                focus_entity_id=(ctx or {}).get("focus_entity_id"),
                                timeout_s=timeout_s, project_id=str(project_id),
                                thread_id=str(thread_id), run_id=active_run_id(str(thread_id)))
        return {
            "deferred": True, "deferred_id": job["id"], "job_id": job["id"],
            "status": "submitted",
            "note": f"Submitted as background job {job['id']} ({choice.rationale}). "
                    f"I'll continue when it finishes.",
        }

    # Interactive persistent kernel — the default. State persists across calls
    # within this thread, so the agent reuses loaded data / fitted models.
    if KERNEL_ENABLED and not input_.get("fresh"):
        try:
            from datetime import datetime as _dt, timezone as _tz
            from core.exec.kernels import get_pool
            from core.data.workspace import scratch_dir
            # cwd = the active Run's own output dir (so a pipeline's files land in
            # one browsable bundle), else the shared thread scratch dir.
            cwd = _run_scratch_cwd(str(project_id), str(thread_id))
            start_ts = _time.time()
            started_iso = _dt.now(_tz.utc).isoformat()
            sess = get_pool().get_or_start(str(thread_id), "python",
                                           cwd=str(scratch_dir(str(project_id), f"thread-{thread_id}")))
            _ensure_kernel_cwd(sess, "python", cwd)
            res = sess.execute(code, cancel_token=cancel_token, timeout_s=timeout_s)
            if res.timed_out:
                return {"error": f"Code execution timed out ({timeout_s}s limit)"}
            if res.cancelled:
                return {"status": "cancelled",
                        "note": f"Run was cancelled by the user "
                                f"({getattr(cancel_token, 'reason', '')}). No further work happened."}
            plots, tables, files, warns = harvest_artifacts(cwd, since_ts=start_ts)
            # Session-derived: reproduction needs this thread's ordered cells,
            # not the single cell alone (kernels.md §8.1).
            from core.exec.output_cap import snip_middle
            out = {"stdout": snip_middle(res.stdout or ""), "stderr": snip_middle(res.stderr or ""),
                   "returncode": res.returncode, "plots": plots, "tables": tables,
                   "files": files, "execution_mode": "session"}
            # Stage 1 exec record — written after harvest so produced[] is
            # populated. Best-effort: failure here is logged, never blocks
            # the user-visible result. exec_id surfaces in `out` so the
            # caller / UI can reference it (Stage 2 entity creation uses it).
            _eid = _write_exec_record(
                lang="python", ctx=ctx, code=code, cwd=cwd, sess=sess,
                started_iso=started_iso, started_ts=start_ts, res=res,
                plots=plots, tables=tables, files=files,
            )
            if _eid:
                out["exec_id"] = _eid
            if warns:
                out["figure_warnings"] = warns
            # A: namespace preview (only on success — probing a half-broken
            # globals dict is noise). B: one-shot prior-run files preamble when
            # the cwd just shifted, so bare filenames the agent learned in the
            # old cwd resolve again.
            if res.returncode == 0:
                ns = _kernel_namespace_preview(sess, "python")
                if ns:
                    out["namespace"] = ns
            if getattr(sess, "_aba_cwd_just_switched", None):
                from content.bio.lifecycle.runs import active_run_id as _arid
                preamble = _prior_run_files_preamble(str(project_id), str(thread_id),
                                                    current_run_id=_arid(str(thread_id)),
                                                    cwd=getattr(sess, "cwd", None))
                sess._aba_cwd_just_switched = None
                if preamble:
                    out["stdout"] = preamble + "\n" + (out["stdout"] or "")
            return out
        except Exception as e:  # noqa: BLE001
            # Fix 1: don't strand on a TRANSIENT hiccup. A first-start failure
            # leaves no session for get_or_start to "restart", so hard-reset and
            # retry the kernel ONCE before degrading — a fresh start usually
            # succeeds (e.g. a slow first kernel boot on a new install). Only
            # after the retry do we drop to the stateless, cwd-fresh one-shot.
            _ktries = int(input_.get("_kernel_tries", 0))
            print(f"[run_python] kernel attempt {_ktries + 1} failed: {e}")
            try:
                from core.exec.kernels import get_pool
                get_pool().restart(str(thread_id), "python")
            except Exception:  # noqa: BLE001
                pass
            if _ktries < 1:
                return run_python({**input_, "_kernel_tries": _ktries + 1}, ctx)
            input_ = {**input_, "_kernel_fallback": True}

    # Stateless one-shot (fresh=true, kernel disabled, or kernel fallback).
    run_id = ((ctx or {}).get("run_id")
              or getattr(cancel_token, "run_id", None)
              or uuid.uuid4().hex)
    try:
        result = run_python_code(code, project_id=str(project_id), run_id=str(run_id),
                                 timeout_s=timeout_s, cancel_token=cancel_token,
                                 extra_syspath=[])
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}
    # Fix 2: if we degraded to stateless because the kernel was unavailable (NOT
    # because the agent asked for fresh/background), say so LOUDLY. Otherwise the
    # agent assumes a persistent kernel and its define-then-use / relative-path
    # patterns silently break — state and cwd don't carry between stateless runs.
    if input_.get("_kernel_fallback") and isinstance(result, dict):
        result["kernel_warning"] = (
            "⚠ Ran WITHOUT a persistent kernel (it was temporarily unavailable). "
            "Variables, functions, and imports defined in earlier run_python calls are "
            "NOT available here, and the working directory is a fresh per-run scratch dir. "
            "Define everything you need in THIS call, and use ABSOLUTE paths for any files "
            "you want to keep or pass to register_dataset."
        )
    return result


def run_r(input_: dict, ctx: dict | None = None) -> dict:
    """Execute R in the thread's persistent R (IRkernel) session — objects
    persist across calls, and the session shares the thread's working dir with
    run_python for file handoff (CSV/Parquet/RDS). For Bioconductor/DESeq2/
    edgeR/limma/Seurat work.

    background=True (or estimated_runtime_min above the router's threshold)
    routes through the job queue: writes a standalone script.R, runs Rscript,
    harvests artifacts, fires the continuation hook so the agent's plan
    resumes when the job completes. Same machinery as run_python's
    background mode (see B1-B6 design 2026-06-08)."""
    import time as _time
    from core.exec.run import harvest_artifacts
    from core.exec import LocalRouter
    from core.config import KERNEL_ENABLED
    from core import projects

    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 600), 1800))
    cancel_token = (ctx or {}).get("cancel_token")
    project_id = projects.current() or "default"
    thread_id = (ctx or {}).get("thread_id") or "default"

    # Background / long-runtime → job queue. Mirror run_python's routing.
    override = "background" if input_.get("background") else None
    est_min = float(input_.get("estimated_runtime_min") or 0)
    choice = LocalRouter().route(estimate={"runtime_min": est_min}, override=override)
    if choice.location == "background":
        from core.jobs.runner import submit_r_job
        from content.bio.lifecycle.runs import active_run_id
        job = submit_r_job(code, title=input_.get("title") or "Background R analysis",
                           focus_entity_id=(ctx or {}).get("focus_entity_id"),
                           timeout_s=timeout_s, project_id=str(project_id),
                           thread_id=str(thread_id), run_id=active_run_id(str(thread_id)))
        return {
            "deferred": True, "deferred_id": job["id"], "job_id": job["id"],
            "status": "submitted",
            "note": f"Submitted as background R job {job['id']} ({choice.rationale}). "
                    f"Script will run via Rscript; figures register on completion.",
        }

    # Synchronous kernel path (default).
    if not KERNEL_ENABLED:
        return {"error": "R runs in a persistent kernel, which is currently disabled. "
                         "Pass background=True to run as a queued Rscript job instead."}
    try:
        from datetime import datetime as _dt, timezone as _tz
        from core.exec.kernels import get_pool
        from core.data.workspace import scratch_dir
        # cwd = the active Run's own output dir (shared with the Python kernel via
        # the same run-keyed dir), else the thread scratch dir.
        cwd = _run_scratch_cwd(str(project_id), str(thread_id))
        start_ts = _time.time()
        started_iso = _dt.now(_tz.utc).isoformat()
        sess = get_pool().get_or_start(str(thread_id), "r",
                                       cwd=str(scratch_dir(str(project_id), f"thread-{thread_id}")))
        _ensure_kernel_cwd(sess, "r", cwd)
        res = sess.execute(code, cancel_token=cancel_token, timeout_s=timeout_s)
    except Exception as e:  # noqa: BLE001
        return {"error": f"R kernel error: {e}"}
    if res.timed_out:
        return {"error": f"R code timed out ({timeout_s}s limit)"}
    if res.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}
    plots, tables, files, warns = harvest_artifacts(cwd, since_ts=start_ts)
    from core.exec.output_cap import snip_middle
    out = {"stdout": snip_middle(res.stdout or ""), "stderr": snip_middle(res.stderr or ""),
           "returncode": res.returncode, "plots": plots, "tables": tables,
           "files": files, "execution_mode": "session"}
    # Stage 1 exec record (parity with run_python). Best-effort; logged on
    # failure, never blocks the tool result.
    _eid = _write_exec_record(
        lang="r", ctx=ctx, code=code, cwd=cwd, sess=sess,
        started_iso=started_iso, started_ts=start_ts, res=res,
        plots=plots, tables=tables, files=files,
    )
    if _eid:
        out["exec_id"] = _eid
    if warns:
        out["figure_warnings"] = warns
    # A: namespace preview. B: one-shot prior-run files preamble on cwd switch.
    if res.returncode == 0:
        ns = _kernel_namespace_preview(sess, "r")
        if ns:
            out["namespace"] = ns
    if getattr(sess, "_aba_cwd_just_switched", None):
        from content.bio.lifecycle.runs import active_run_id as _arid
        preamble = _prior_run_files_preamble(str(project_id), str(thread_id),
                                             current_run_id=_arid(str(thread_id)),
                                             cwd=getattr(sess, "cwd", None))
        sess._aba_cwd_just_switched = None
        if preamble:
            out["stdout"] = preamble + "\n" + (out["stdout"] or "")
    return out
