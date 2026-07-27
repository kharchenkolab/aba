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


# The kernel jobdir is the substrate's OWN working area, and a weft kernel's
# sandbox is also where a block's bare relative writes land — so the harvester
# reads both real outputs and the driver's machinery out of the same directory.
# These names are that machinery (weft `kernels/driver.*` + `runner.sh`), never
# a user output. `current_block` in particular is a HEARTBEAT: written when a
# block starts, removed when it ends — so any block that dies mid-flight leaves
# one behind with a fresh mtime. Live (thr_a1f7f687): an ssh timeout killed a
# block and `current_block` (3 bytes) became the Run card's ONLY recorded
# output. The previous filter covered `blocks/` and `kernel.*` only.
_KERNEL_MACHINERY = frozenset({
    "current_block", "activate.sh", "cmd.sh", "runner.sh", "log",
    "node", "pid", "pid.epoch", "pid.real", "rusage",
    # the block DIRECTORY itself, not just its contents: an inventory lists the
    # dir entry as bare `blocks`, which the `blocks/` prefix below never matches
    # (caught the moment the fake grew a realistic jobdir)
    "blocks",
})
_KERNEL_MACHINERY_PREFIXES = ("blocks/", "kernel.", "driver.")


def _untracked_write_note(sess, lang: str) -> str:
    """Warn when the kernel's cwd has LEFT its sandbox, because everything the
    block writes relatively is then invisible to aba.

    This hazard is NEW, and it arrived with a fix. A weft kernel used to DIE on
    a chdir (its driver wrote `blocks/NNNN.*` relatively), so the failure was at
    least loud. The substrate now anchors those paths absolutely — deliberately
    keeping cwd persistence — so a chdir succeeds and STICKS. The kernel is fine
    and every subsequent `ggsave("umap.png")` lands outside the one directory the
    harvester reads. Nothing fails; the outputs simply never exist as far as the
    Run card, `view_artifact` or the viewer are concerned. That silence is
    exactly how a long analysis finished with 0 recorded outputs.

    Reported at the END of the step, where the agent can still act on it — the
    alternative is discovering it later via an empty Run card. Best-effort: the
    baseline is the cwd observed on the FIRST probe of this session (the kernel
    starts in its sandbox), and no baseline means no claim."""
    cwd = getattr(sess, "_aba_probe_cwd", None)
    if not cwd:
        return ""
    base = getattr(sess, "_aba_sandbox_cwd", None)
    if not base:
        return ""                            # sandbox unknown → no claim
    import os as _os
    if _os.path.normpath(cwd) == _os.path.normpath(base):
        return ""
    call = "setwd()" if lang == "r" else "os.chdir()"
    return (f" WARNING — this kernel's working directory is now {cwd!r}, no "
            f"longer its sandbox ({base!r}). Anything this step wrote with a "
            f"BARE RELATIVE name went to {cwd!r}, which aba does NOT harvest: "
            f"those files will not appear on the Run card, and view_artifact / "
            f"get_viewer_url will not find them by name. Either return with "
            f"{call} to the sandbox and write relatively there, or call "
            f"register_dataset(path=..., site=...) for each result you want "
            f"kept.")


def _remote_site_of(sess) -> str | None:
    """The session's site when it is a REMOTE one, else None. The orientation
    banner keys its write-guidance on this: a local session's outputs already
    land in a controller path the harvester reads."""
    site = getattr(sess, "site", None)
    return site if (site and site != "local") else None


def _is_kernel_machinery(rel: str) -> bool:
    """Is this sandbox entry the kernel driver's own bookkeeping rather than
    something the user's code produced? Coupled to weft's jobdir layout by
    necessity — the two share a directory."""
    return (rel in _KERNEL_MACHINERY
            or rel.startswith(_KERNEL_MACHINERY_PREFIXES))


def _with_cwd_probe(sess, code: str, lang: str, cwd) -> str:
    """Wrap a JUPYTER kernel block with the cwd probe (core.exec.run): the
    prologue records where the block STARTED, the epilogue writes start+final
    to a dot-sentinel in the HARVEST ROOT (fixed, aba-known — the start dir
    itself is what may have drifted). Weft kernels are excluded twice over:
    they cannot chdir (the block protocol breaks loudly) and probe lines
    would pollute their recorded transcript.

    Exclusion is by kernel IDENTITY (`is_weft_kernel`), not by `work_dir` —
    that field is set only for a LOCAL weft kernel, so the old spelling would
    have injected probe lines into every REMOTE weft kernel's transcript."""
    from core.exec.kernels.cwd_guard import is_weft_kernel
    if is_weft_kernel(sess):
        return code
    from core.exec.run import cwd_probe_prologue, cwd_probe_epilogue
    return (cwd_probe_prologue(lang) + code
            + cwd_probe_epilogue(lang, sentinel_dir=str(cwd)))


def _reconcile_kernel_cwd(sess, cwd) -> list:
    """Post-block: read the probe sentinel; warn when the kernel ENDED
    outside the tracked dir (files written there are unharvested — the
    warning names the valid levers), and record the REAL dir into the
    session marker so _ensure_kernel_cwd restores the platform cwd on the
    next block (self-heal instead of a permanently lying marker). Returns
    warning strings; empty when the block died pre-epilogue (no claim).

    Weft kernels are excluded by IDENTITY (`is_weft_kernel`): they carry no cwd
    probe, so there is no sentinel to read — and the `work_dir` spelling missed
    remote ones, which would then read a sentinel that cannot exist."""
    from core.exec.kernels.cwd_guard import is_weft_kernel
    if is_weft_kernel(sess):
        return []
    from core.exec.run import read_cwd_sentinel, cwd_escape_warning
    _start, final = read_cwd_sentinel(cwd)
    if not final:
        return []
    out = []
    esc = cwd_escape_warning(str(cwd), final)
    if esc:
        out.append(esc)
    if final != getattr(sess, "_aba_cwd", None):
        sess._aba_cwd = final
    return out


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

    Marks `sess._aba_cwd_just_switched` so the next run_python/run_r call reads
    + clears the flag and emits a one-shot 'Workspace orientation' preamble
    (see _prior_run_files_preamble) listing registered datasets + prior-run
    files + thread-scratch contents. The agent then knows where bare-filename
    loads will resolve and where files from earlier turns persist.

    The flag fires in TWO cases:
      - cwd genuinely moved (prev != path): one-shot at the moment of switch.
      - kernel just spawned (prev was None): the FRESH KERNEL signal. Live
        bug 2026-06-16 (prj_8143327c thr_80190faf): a backend restart killed
        the R kernel; the respawned kernel's prev was None, so the previous
        guard suppressed the preamble. The agent then tried to use `obj` (gone)
        and guessed wrong paths reloading .rds files. The fresh-kernel marker
        ("__FRESH__") triggers the same preamble plus an extra header so the
        agent recognizes 'in-memory state is gone, paths persist'.
    """
    # A weft kernel CANNOT chdir — its file-block protocol reads/writes
    # `blocks/NNNN.*` relative to cwd, so moving away orphans the protocol and the
    # kernel dies. Its sandbox IS its work dir and aba harvests from there (see
    # _harvest_dir). Skip the chdir; still fire the one-shot orientation preamble
    # on a fresh kernel.
    #
    # Detect the weft kernel by IDENTITY, not by `work_dir`: WeftKernelSession sets
    # work_dir ONLY for a local site (weft.py — a remote kernel's sandbox is a
    # remote path and comes home via retain/collect), so a `work_dir` test read
    # REMOTE weft kernels as chdir-able. They then got `setwd(<controller path>)`
    # executed ON the remote machine, which orphaned the block protocol and killed
    # the kernel on its next write — `writeLines -> file(con, "w") : cannot open
    # the connection`, exit 1, repeatedly (live regression, caught 2026-07-26 on
    # mendel; the remote lane had only just started calling this at all).
    from core.exec.kernels.cwd_guard import is_weft_kernel
    if is_weft_kernel(sess):
        # Gate the one-shot on its OWN flag, not on `_aba_cwd`: a remote kernel
        # has no local cwd to record, so an `_aba_cwd is None` test would re-fire
        # the FRESH banner on every single call.
        if not getattr(sess, "_aba_oriented", False):
            sess._aba_oriented = True
            # work_dir when known (local weft kernel); None for a remote one —
            # the preamble takes cwd as Optional and simply lists less.
            sess._aba_cwd = getattr(sess, "work_dir", None)
            sess._aba_cwd_just_switched = "__FRESH__"
        return
    path = str(cwd)
    prev = getattr(sess, "_aba_cwd", None)
    if prev == path:
        # NOTE the marker is only as true as the last reconcile: agent code
        # can chdir mid-block, so trusting `prev` alone let a desynced marker
        # skip the restore forever (harvest-honesty sweep, 2026-07-26). The
        # per-block probe (_with_cwd_probe/_reconcile_kernel_cwd) records the
        # REAL final dir into the marker after every block, so a drifted
        # kernel reaches this comparison with the truth and gets restored.
        return
    try:
        if lang == "r":
            snippet = (f'setwd({path!r}); Sys.setenv(WORK_DIR={path!r}); WORK_DIR <- {path!r}')
        else:
            snippet = (f'import os as _os; _os.chdir({path!r}); '
                       f'_os.environ["WORK_DIR"]={path!r}; WORK_DIR={path!r}')
        sess.execute(snippet, timeout_s=15)
        sess._aba_cwd = path
        # Mark just-switched on BOTH a real cwd change and a fresh kernel.
        # The sentinel "__FRESH__" distinguishes the cases for the preamble.
        sess._aba_cwd_just_switched = prev if prev is not None else "__FRESH__"
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
    import os as _o; print('__ABA_CWD__', _o.getcwd())
    print('__ABA_SANDBOX__', _o.environ.get('WORK_DIR', ''))
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
  cat('__ABA_CWD__ ', getwd(), '\n', sep='')
  cat('__ABA_SANDBOX__ ', Sys.getenv('WORK_DIR'), '\n', sep='')
})
"""


def _kernel_namespace_preview(sess, lang: str) -> list[str]:
    try:
        res = sess.execute(_NS_PROBE_R if lang == "r" else _NS_PROBE_PY, timeout_s=15)
        out = (res.stdout or "") + "\n" + (res.stderr or "")
        if "__ABA_CWD__" in out:
            # free ride on a probe that already runs: knowing WHERE the kernel
            # ended the block is what tells us whether its relative writes are
            # still landing in the harvested sandbox
            try:
                sess._aba_probe_cwd = out.split("__ABA_CWD__", 1)[1].splitlines()[0].strip()
            except Exception:  # noqa: BLE001
                pass
        if "__ABA_SANDBOX__" in out:
            # The sandbox is KNOWN, not remembered: the setup block binds
            # WORK_DIR to the kernel's own directory at start and nothing we do
            # rebinds it. Deriving the baseline from the first probe instead was
            # wrong for the commonest shape — an agent that chdirs in its FIRST
            # block made the drifted directory the baseline, so the warning
            # never fired (caught live on mendel).
            try:
                sess._aba_sandbox_cwd = (
                    out.split("__ABA_SANDBOX__", 1)[1].splitlines()[0].strip()
                    or getattr(sess, "_aba_sandbox_cwd", None))
            except Exception:  # noqa: BLE001
                pass
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


# File-open error patterns that should re-fire the path orientation
# preamble on the SAME tool_result. From prj_a6f40e94 2026-06-19 we saw
# `Error in open.connection(file): cannot open the connection` and
# `Error in nrow(seurat_...)` repeatedly with no path hint — exactly
# when the agent needed one.
import re as _re                                                          # noqa: E402
_FILE_ERROR_PATTERNS = _re.compile(
    r"(cannot open the connection|"
    r"FileNotFoundError|"
    r"No such file or directory|"
    r"file\.exists.*FALSE|"
    r"\[Errno 2\]|"
    r"could not find function .* in file|"
    r"object .* not found.*file)",
    _re.IGNORECASE,
)


def _maybe_force_preamble_on_file_error(sess, stderr: str, stdout: str) -> bool:
    """Flip sess._aba_cwd_just_switched to 'FILE_ERR' so the next-call
    block at the end of run_python/run_r emits the preamble — except
    we also call that block on THIS call, since the agent will pay the
    same cost either way and seeing the hint with the error is more
    useful than seeing it on the retry.

    Idempotent within a turn: tracks `sess._aba_recent_err_preamble` so
    we don't re-prepend if we already did in the last 3 calls (avoids
    spamming the agent if it can't figure out the path).
    """
    if not (stderr or stdout):
        return False
    blob = (stderr or "") + "\n" + (stdout or "")
    if not _FILE_ERROR_PATTERNS.search(blob):
        return False
    cooldown = int(getattr(sess, "_aba_recent_err_preamble", 0))
    if cooldown > 0:
        sess._aba_recent_err_preamble = cooldown - 1
        return False
    # Set the flag so the end-of-call preamble block fires and tag it
    # FILE_ERR so a future tester can tell why it fired.
    sess._aba_cwd_just_switched   = "FILE_ERR"
    sess._aba_recent_err_preamble = 3
    return True


def _sibling_language_sandboxes(thread_id: str, site: str, lang: str) -> list[tuple]:
    """[(lang, absolute sandbox path)] for this thread's OTHER live kernels on
    the SAME site.

    One kernel = one sandbox, so a language switch changes the directory that
    bare relative filenames resolve against. The banner promises bare names
    "land in this kernel's sandbox", which is true and still misleading: an
    agent handing a table from R to Python on one machine reads the promise as a
    per-machine working directory.

    Live (2026-07-27, orbtest): R wrote `x_squared.csv` with a bare name, Python
    read it with a bare name on the same site, FileNotFoundError. The agent
    recovered in three extra calls (find_files, then a glob under the other
    kernel's directory) — and the recovery is itself the untracked-write shape,
    because it reaches across sandboxes by absolute path. The file was never
    lost; only its location was unsayable.
    """
    try:
        from core.exec.kernels.pool import get_pool
        out = []
        for s in get_pool().sessions_for_thread(str(thread_id)):
            if not getattr(s, "alive", False):
                continue
            if (getattr(s, "site", "local") or "local") != site:
                continue
            other = (getattr(s, "lang", None) or "").lower()
            box = getattr(s, "_aba_sandbox_cwd", None)
            if other and other != lang and box:
                out.append((other, box))
        return out
    except Exception:  # noqa: BLE001 — orientation is best-effort
        return []


def _prior_run_files_preamble(project_id: str, thread_id: str,
                              current_run_id: str | None,
                              max_runs: int = 4, max_files: int = 12,
                              max_scratch_files: int = 12,
                              cwd: str | None = None,
                              fresh_kernel: bool = False,
                              remote_site: str | None = None,
                              lang: str | None = None) -> str:
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

        # (1) Registered datasets — name + path + layout_hint (if recorded)
        # + WHERE the bytes live (surfacing census 2026-07-26: path-only
        # lines read as "local"; a remote home is named on every surface).
        datasets: list[tuple[str, str, str, str]] = []   # (title, path, hint, loc)
        try:
            from content.bio.data_location import location_suffix
            for d in list_entities(type_filter="dataset", include_archived=False):
                ap = d.get("artifact_path") or ""
                if not ap: continue
                title = (d.get("title") or d.get("id") or "").strip()
                md = d.get("metadata") or {}
                hint = (md.get("layout_hint") or "").strip()
                datasets.append((title, ap, hint, location_suffix(d)))
        except Exception:  # noqa: BLE001
            pass

        # (2) Prior-run files — grouped by Run so the agent has a stable
        # lexical anchor ("Run ana_…") to reference in chat, not just a flat
        # file list. Dedup by filename across Runs (older copies hide behind
        # the most recent), so a recurring artifact name doesn't clutter the
        # block.
        run_groups: list[tuple[str, str, list[tuple[str, str]]]] = []   # (run_id, title, files)
        seen_names: set[str] = set()
        scanned = 0
        total_files = 0
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
            kept: list[tuple[str, str]] = []
            for f in files:
                if f.name in seen_names: continue
                seen_names.add(f.name)
                kept.append((f.name, str(f)))
                total_files += 1
                if total_files >= max_files: break
            if kept:
                run_groups.append((e["id"], (e.get("title") or "").strip(), kept))
            if total_files >= max_files or scanned >= max_runs: break

        # (2b) Files in the CURRENT cwd. The default preamble path skips
        # this (the cwd contents are reachable by bare filename so the
        # agent doesn't need a path), but on a fresh kernel the agent
        # doesn't know what's already in the cwd from previous turns —
        # this listing closes that gap.
        cwd_mapped: list[tuple[str, str]] = []
        if fresh_kernel and cwd:
            try:
                cp = Path(cwd)
                if cp.is_dir():
                    cands = []
                    for entry in cp.iterdir():
                        if not _keep(entry.name): continue
                        cands.append(entry)
                    cands.sort(key=lambda f: f.stat().st_mtime if f.exists() else 0,
                               reverse=True)
                    for f in cands[:max_files]:
                        if f.name in seen_names: continue
                        seen_names.add(f.name)
                        suffix = "/" if f.is_dir() else ""
                        cwd_mapped.append((f.name + suffix, str(f)))
            except Exception:  # noqa: BLE001
                pass

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
                    if f.is_dir() and any(str(f) == ap for _, ap, *_ in datasets):
                        continue
                    seen_names.add(f.name)
                    suffix = "/" if f.is_dir() else ""
                    scratch_mapped.append((f.name + suffix, str(f)))
                    if len(scratch_mapped) >= max_scratch_files: break
        except Exception:  # noqa: BLE001
            pass

        # Early-bail historically: skip the preamble when nothing useful
        # to surface AND not a fresh-kernel signal. But the P2/P3 force-
        # preamble triggers (FILE_ERR, RUN_OPEN) want at least the cwd
        # line on a brand-new project — without that, a "cannot open the
        # connection" error gets no hint at all (prj_da58dbab live-drive
        # 2026-06-20). So emit a minimal preamble whenever cwd is set,
        # even with no datasets/runs/scratch.
        # NB: variable was `run_mapped` here historically, which never
        # existed — the function built `run_groups` instead. That
        # NameError silently bailed every non-fresh-kernel call out via
        # the outer try/except, which is why prj_a6f40e94 saw 1
        # preamble in 37 tool_results (only the FRESH path ever
        # rendered). Fixed during the 2026-06-20 P2/P3 work.
        if (not datasets and not run_groups and not scratch_mapped
                and not fresh_kernel and not cwd):
            return ""
        header = ("── Fresh kernel — workspace orientation ──"
                  if fresh_kernel
                  else "── Workspace orientation (cwd just shifted) ──")
        lines: list[str] = [header]
        if fresh_kernel:
            # The agent's most likely next move on a fresh kernel is to
            # reach for a variable that no longer exists. Spell it out
            # so 'object obj not found' surfaces as 'reload from disk'
            # rather than 'guess at the path'.
            lines.append(
                "In-memory state (R/Python objects, loaded libraries) is GONE. "
                "Files saved to disk in previous turns persist; reload them "
                "with readRDS()/load_h5ad()/read_csv() etc. from the absolute "
                "paths listed below.")
            # Do NOT promise "prebuilt conda/bioconda binaries" here: where the
            # base is an adopted read-only mount the conda lane refuses and
            # ensure_capability falls through to a source build, so the banner
            # named a mechanism that wasn't operative and left the agent with
            # no read on the compile failure that followed (live 2026-07-22).
            # The reader of THIS banner is composing R: bare `tool(name)`
            # syntax reads as an R function and gets inlined into the cell
            # (live thr_ee54c469: rc=1). Name the namespace boundary.
            lines.append(
                "Need a library that isn't loaded? Call the ensure_capability tool FIRST (a platform tool, not an R function — never write it inside R code) "
                "— and do NOT install.packages()/BiocManager::install()/pip install "
                "(they bypass the project env and aren't recorded). If it fails "
                "in configure/compile on a missing header or system library, "
                "that's the one thing the project env CANNOT add: build an "
                "isolated env instead (make_isolated_env(..., language='r'), "
                "then run_r(env=...)), which does a full solve.")
        if cwd:
            lines.append(f"cwd: {cwd}  (bare filenames in your code land here — "
                         f"an EPHEMERAL sandbox, swept when this kernel stops: "
                         f"when the user wants a produced file KEPT, call the "
                         f"keep_outputs tool; register_dataset is only for "
                         f"real datasets). Do NOT chdir/setwd outside this "
                         f"dir: files written elsewhere are NOT tracked, NOT "
                         f"on the Run card, and NOT keepable — if you must "
                         f"write elsewhere, register_dataset the absolute "
                         f"path afterwards.")
        # Surface the RESOLVED DATA_DIR + the input files actually present (incl.
        # SUBDIRS) so the agent doesn't conclude "no data — ask the user to
        # upload" when files are on disk but unregistered (forensic: coloc/foci).
        try:
            from core.config import project_data_dir as _pdd
            _dd = _pdd(str(project_id))
            _df = [p for p in sorted(_dd.rglob("*")) if p.is_file() and _keep(p.name)][:max_files]
            if _df:
                lines.append(f"Input data present ({_dd}) — refer to files by NAME; "
                             f"find_files('<name>') locates anything anywhere:")
                for _p in _df:
                    lines.append(f"  - {_p.relative_to(_dd).as_posix()}")
        except Exception:  # noqa: BLE001
            pass
        lines.append("")
        if datasets:
            lines.append("Registered datasets in this project (canonical paths — use verbatim):")
            for title, path, hint, loc in datasets:
                label = title or path.rsplit("/", 1)[-1]
                tail = f"  [{hint}]" if hint else ""
                lines.append(f"  - {label} → {path}{tail}{loc}")
                # List a few representative filenames inside directory-
                # shaped datasets so the agent sees layout patterns
                # (sample prefixes, 10x triplet roles, etc.) without
                # having to os.listdir(). prj_61bb79a0 friction
                # 2026-06-20: agent burned 3 calls discovering the
                # GSM5746259_… prefix on a flat-files 10x bundle.
                try:
                    dp = Path(path)
                    if dp.is_dir():
                        kids = sorted(
                            (e for e in dp.iterdir() if _keep(e.name)),
                            key=lambda f: (f.is_dir(), f.name),
                        )
                        for k in kids[:5]:
                            suffix = "/" if k.is_dir() else ""
                            lines.append(f"      {k.name}{suffix}")
                        if len(kids) > 5:
                            lines.append(f"      … (+{len(kids)-5} more)")
                except Exception:                                  # noqa: BLE001
                    pass
            lines.append("")
        if cwd_mapped:
            lines.append("Files already in the current cwd (saved earlier in this Run):")
            for name, full in cwd_mapped:
                lines.append(f"  - {name} → {full}")
            lines.append("")
        if run_groups:
            lines.append("Files from prior runs in this thread:")
            for run_id, title, files in run_groups:
                header = f"Run {run_id}" + (f" — \"{title}\"" if title else "")
                lines.append(f"  - {header}:")
                for name, full in files:
                    lines.append(f"      - {name} → {full}")
            lines.append("")
        if scratch_mapped and not remote_site:
            # CONTROLLER-LOCAL paths. Listing them to a REMOTE kernel is worse
            # than useless: they do not exist on that machine, and offering
            # absolute paths as the house style is part of what sends a remote
            # step off to invent its own output directory (below).
            lines.append("Thread shared-scratch (your ad-hoc downloads / intermediates):")
            for name, full in scratch_mapped:
                lines.append(f"  - {name} → {full}")
        if remote_site:
            # WHERE TO WRITE — the guidance this banner never gave a remote
            # kernel. Live (thr_a1f7f687): every output of a long analysis went
            # to a hand-picked absolute dir on the site, so NOTHING was
            # harvested — the Run card recorded zero outputs, `view_artifact`
            # refused all five figures, and each viewer link needed a manual
            # register_dataset first. The banner had listed only remote dataset
            # paths (which the agent imitated) and controller-local scratch dirs
            # (unreachable there), and said nothing about outputs at all.
            lines.append("")
            lines.append(
                f"WRITING OUTPUTS on {remote_site} — use BARE RELATIVE filenames "
                f"(e.g. saveRDS(obj, \"integrated.rds\"), ggsave(\"umap.png\")). "
                f"They land in this kernel's sandbox on {remote_site}, which is "
                f"the ONLY place aba harvests: those files become this Run's "
                f"outputs, appear on the Run card, and are viewable/keepable by "
                f"name. Files written to an absolute path elsewhere on "
                f"{remote_site} are NOT tracked — no Run card entry, no "
                f"view_artifact, no viewer link without registering them by hand. "
                f"If a step must write outside the sandbox, call register_dataset("
                f"path=..., site=\"{remote_site}\") for each result you want to keep.")
            siblings = (_sibling_language_sandboxes(thread_id, remote_site, lang)
                        if lang else [])
            if siblings:
                # The handoff the bare-name rule cannot express: each language
                # has its OWN sandbox on this machine, so a file another
                # language just wrote is not in yours. Name the directory rather
                # than leaving the agent to discover it by failing first.
                lines.append("")
                names = ", ".join(f"{lg.upper()} → {box}" for lg, box in siblings)
                lines.append(
                    f"CROSS-LANGUAGE on {remote_site}: each language has its own "
                    f"sandbox here, so a bare filename does NOT reach the other "
                    f"one's files. This thread's other live sandbox(es) on "
                    f"{remote_site}: {names}. To hand data over, read it from "
                    f"that absolute path (the file is really there), or write it "
                    f"to the OTHER language's sandbox path so it lands where the "
                    f"reader looks. Bare names still keep writes harvestable — "
                    f"use them for the results you want tracked.")
        return "\n".join(lines).rstrip() + "\n"
    except Exception:  # noqa: BLE001
        return ""


def _env_identity(sess) -> dict:
    """Env-identity facts for an exec record's `compute` block, read from the
    session that already resolved them at start (no re-resolution, no substrate
    call). `env_grade` mirrors the job lane's vocabulary: 'node-system' when a
    REMOTE session runs the node's own interpreter (the `env='system'` lever),
    else 'env'. A local session has no weft env and is graded 'local'."""
    env_id = getattr(sess, "env_id", None)
    site = getattr(sess, "site", "local") or "local"
    out: dict = {}
    if env_id:
        out["env_id"] = env_id
    name = getattr(sess, "env_name", None)
    if name:
        out["env_name"] = name
    if site == "local":
        out["env_grade"] = "local"
    else:
        out["env_grade"] = "env" if env_id else "node-system"
    return out


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
        # Per-output HOME site (remote-output lifecycle): the producing step's
        # site, from the same `compute` block written below. Stamped on each
        # produced entry ONLY when non-local, so a downstream resolver has a
        # per-output "home = <site>" signal instead of inferring it from the run.
        # Additive — consumers may read it but must not require it (older records
        # lack it, and a local step omits it).
        _site = getattr(sess, "site", "local") or "local"
        _site_kw = {"site": _site} if _site != "local" else {}
        # `size` (from harvest's recorded bytes) rides along for every kind so the
        # durable Files panel shows real sizes, not 0, for normally-copied files too.
        for i, p in enumerate(plots or []):
            produced.append({"kind": "figure", "idx": i,
                             "url": p.get("url"), "size": p.get("bytes"),
                             "name": p.get("original_name") or p.get("name"),
                             **_site_kw})
        for i, t in enumerate(tables or []):
            produced.append({"kind": "table", "idx": i,
                             "url": t.get("url"), "size": t.get("bytes"),
                             "name": t.get("original_name") or t.get("name"),
                             **_site_kw})
        for i, f in enumerate(files or []):
            produced.append({"kind": "file", "idx": i,
                             "url": f.get("url"), "size": f.get("bytes"),
                             "name": f.get("original_name") or f.get("name"),
                             # link-only (oversize) files carry no served url;
                             # the marker rides along so retention/UI see them.
                             **({"link_only": True} if f.get("link_only") else {}),
                             **_site_kw})

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

        # Inputs + seed (provenance §3.2/§3.3) — identity-level, best-effort. The
        # focused entity + datasets the code references; a seed only if the code set
        # one (we don't inject on the interactive path — that would change results).
        from core.graph.run_inputs import resolve_inputs, detect_seed
        inputs = resolve_inputs(code or "", (ctx or {}).get("focus_entity_id"))
        # Recipe/pipeline the agent read THIS turn (recipe_ctx tracks Skill/read_skill
        # uptake). Descriptive only — attributes the run to a method it likely used.
        recipes: list[str] = []
        _rc = (ctx or {}).get("recipe_ctx")
        if isinstance(_rc, dict) and _rc.get("read"):
            recipes = sorted(_rc["read"])

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
                # The weft kernel that ran this cell — carried so the artifact
                # registration hook can record it on the (lazily-created) Run for
                # retention, even on a single-turn Run where active_run_id was still
                # None when the cell executed (misc/output_durability.md §A2).
                "weft_target": getattr(sess, "kernel_id", None),
                # PLACEMENT PROVENANCE (provenance.md): record WHERE this cell ran
                # into the graph, so a Result traces back to its machine WITHOUT
                # relying on the ephemeral conversation. The background/detached
                # lane already writes a `compute` block (weft_submitter._compute_block);
                # the interactive/sync lane must too, else a synchronous remote step
                # leaves only an opaque kernel id and the site is unrecoverable
                # (found by mn_provenance_after_chain, block-4 reassessment).
                "compute": {
                    "substrate": ("weft" if type(sess).__name__ == "WeftKernelSession"
                                  else "local"),
                    "site": getattr(sess, "site", "local"),
                    **({"kernel_id": sess.kernel_id}
                       if getattr(sess, "kernel_id", None) else {}),
                    # ENV IDENTITY — job-lane parity (weft_submitter._compute_block
                    # records env_id/grade; this lane recorded neither, so 21 of 24
                    # remote steps in one live session could not answer "which
                    # interpreter ran this" from the graph at all). The session
                    # already resolved it at start (WeftKernelSession.env_id): a
                    # named env's EnvID or the project snapshot's. No env_id on a
                    # REMOTE session means the node's own interpreter — the
                    # `env='system'` lever — which is exactly the fact worth
                    # recording, so grade it rather than leaving a hole.
                    **_env_identity(sess),
                },
                "kind": "script",
                "language": lang,
                "language_version": lang_ver,
                "package_versions": pkg,
                "env_fingerprint": ef,
                "inputs": inputs,
                "seed": detect_seed(code or ""),
                "recipe_id": recipes[0] if recipes else None,
                "recipes": recipes or None,
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


def _is_default_env(env) -> bool:
    """env_refactor.md §11.2 — None/'' and the reserved names all mean the
    project's normal served stack; any other name is a named isolated env."""
    from core.compute.named_envs import RESERVED_ENV_NAMES
    return (env or "").strip().lower() in ("", *RESERVED_ENV_NAMES)


def _run_in_named_env(env: str, code: str, lang: str, timeout_s: int) -> dict:
    """run_python/run_r(env=<name>) → the named (weft) env, one-shot. The
    interactive python path uses the per-env persistent kernel instead (below);
    this is the stateless lane (R named envs + kernels-disabled fallback)."""
    from core.compute import named_envs
    from core import projects
    env = env.strip()
    pid = str(projects.current() or "default")
    if named_envs.resolve(pid, env) is None:
        return {"status": "error", "env": env, "language": lang,
                "note": f"No isolated env '{env}'. Create it with make_isolated_env("
                        f"name='{env}'" + (", language='r'" if lang == "r" else "") + ")."}
    r = named_envs.run_in(pid, env, code, timeout_s=timeout_s)
    return {"status": "ok" if r.get("ok") else "error", "env": env, "language": lang,
            "stdout": r.get("stdout", ""), "stderr": r.get("stderr", ""),
            "execution_mode": "isolated"}


def _background_timeout_s(input_: dict, est_min: float) -> int:
    """Size a BACKGROUND job's timeout CEILING. Background work is long, so it does
    NOT use the interactive 300s default / 30-min cap (live incident 2026-06-28: a
    40-min STAR index build was killed at 300s). Honor an explicit `timeout_s`,
    else derive from the agent's `estimated_runtime_min` (estimates are rough → 2x
    margin), else a 1 h default; bounded only by the 24 h hung-job backstop."""
    from core.jobs.runner import BACKGROUND_DEFAULT_TIMEOUT_S, BACKGROUND_MAX_TIMEOUT_S
    explicit = input_.get("timeout_s")
    if explicit:
        base = int(explicit)
    elif est_min and est_min > 0:
        base = int(est_min * 60 * 2)
    else:
        base = BACKGROUND_DEFAULT_TIMEOUT_S
    return max(60, min(base, BACKGROUND_MAX_TIMEOUT_S))


def bg_submit_kwargs(input_: dict, project_id: str) -> dict:
    """The submit_python_job kwargs a BACKGROUND run_python must carry beyond
    code/title/ids/run_id: the agent's resource ESTIMATE (so a Slurm deployment can
    size the partition/QoS and pick a GPU node), the EXECUTION target, the isolated
    ENV, and an estimate-sized TIMEOUT. Shared by run_python() below AND guide.py's
    background-submit intercept, so NEITHER path drops the placement estimate — the
    est_gpu-silently-dropped-on-the-intercept bug (prj_6d986f40): the agent passed
    est_gpu=true but the intercept never forwarded it, so the job couldn't be
    GPU-placed."""
    est_min = float(input_.get("estimated_runtime_min") or 0)
    est = {"runtime_min": est_min, "cores": input_.get("est_cores"),
           "mem_gb": input_.get("est_mem_gb"), "gpu": input_.get("est_gpu")}
    from core.compute.named_envs import resolve_env
    env_name = resolve_env(str(project_id), "python", explicit=input_.get("env"))
    return {"estimate": est, "execution": input_.get("execution"),
            "site": input_.get("site") or None,   # detached lane (misc/detached_compute.md)
            "env": env_name, "timeout_s": _background_timeout_s(input_, est_min)}


def _kernel_sandbox_inventory(kernel_id: str) -> dict:
    """{relpath: mtime} of the kernel's LIVE sandbox on its site — kernel ids
    are first-class weft inventory targets, whatever machine holds them."""
    try:
        from core.compute.adapter import get_compute
        inv = get_compute().sync_call("run_inventory", kernel_id, live=True)
        return {e["path"]: (e.get("mtime") or 0) for e in (inv.get("entries") or [])}
    except Exception:  # noqa: BLE001 — no inventory just means no fetch this call
        return {}


# run_file_read is a preview channel hard-capped at 8 MB — bigger outputs stay
# on the site (kept-addressable via the recorded kernel target; bring-back for
# a local copy), exactly like the detached job lane.
_REMOTE_KERNEL_FETCH_BYTES = 8 * 1024 * 1024


def _fetch_new_kernel_files(kernel_id: str, inv0: dict, project_id: str,
                            thread_id: str) -> tuple:
    """Diff the kernel sandbox against the pre-exec inventory and fetch new /
    changed SMALL files over the data plane into a fresh local dir under the
    thread scratch (the standard harvester then runs over the copies).
    Returns (fetch_dir | None, remote_only_names). Protocol files (blocks/,
    kernel.*) are the kernel's own machinery — never outputs."""
    import base64
    import os
    import time as _time
    from core.compute.adapter import get_compute
    from core.data.workspace import scratch_dir
    from content.bio.lifecycle.runs import _STORE_DIR_SUFFIXES
    inv1 = _kernel_sandbox_inventory(kernel_id)
    new = [(rel, mt) for rel, mt in inv1.items()
           if not _is_kernel_machinery(rel)
           and (rel not in inv0 or mt > inv0.get(rel, 0))]
    if not new:
        return None, []
    # A directory-shaped store (a chunked-array/columnar directory) is ONE output,
    # not a bag of independent small files. It must NOT be brought back piecemeal
    # here: (a) the per-file `new[:200]` cap truncates the lexicographically-last
    # entry — which for a store is its root metadata/index file — so the
    # copy lands with subtrees but no root; (b) a fresh per-turn dir never
    # re-materializes a root written in an EARLIER turn (not in this turn's delta),
    # yielding the same rootless store; and that incomplete copy then SHADOWS the
    # resolver's correct whole-store bring-back (_materialize_store → data-plane
    # fetch, digest-revalidated) because it sorts newest-mtime. Skip store members
    # entirely — the store stays kept-addressable via the recorded kernel target
    # and is fetched as a complete unit on demand (live 2026-07-21, BRINGBACK-DROPS).
    def _store_root(rel: str):
        parts = rel.split("/")
        for i, p in enumerate(parts[:-1]):        # a MEMBER: some ANCESTOR dir is a store
            if p.lower().endswith(_STORE_DIR_SUFFIXES):
                return "/".join(parts[:i + 1])
        return None
    store_roots: set = set()
    kept: list = []
    for rel, mt in new:
        root = _store_root(rel)
        if root is not None:
            store_roots.add(root)
        else:
            kept.append((rel, mt))
    new = kept
    dest = None
    remote_only: list[str] = []
    comp = get_compute()
    for rel, _mt in new[:200]:
        try:
            st = comp.sync_call("run_file_stat", kernel_id, rel)
            if (st.get("bytes") or 0) > _REMOTE_KERNEL_FETCH_BYTES:
                remote_only.append(rel)
                continue
            out = comp.sync_call("run_file_read", kernel_id, rel,
                                 max_bytes=_REMOTE_KERNEL_FETCH_BYTES)
            if out.get("truncated"):
                remote_only.append(rel)
                continue
            data = base64.b64decode(out.get("bytes_b64") or "")
        except Exception:  # noqa: BLE001 — a single unfetchable file stays remote
            remote_only.append(rel)
            continue
        if dest is None:
            dest = str(scratch_dir(project_id, f"thread-{thread_id}")
                       / f"remote-kernel-{int(_time.time())}")
            os.makedirs(dest, exist_ok=True)
        target = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as fh:
            fh.write(data)
    # store dirs stay on the site as whole units — advertise them as
    # kept-addressable (the resolver brings each back complete on open)
    remote_only.extend(sorted(store_roots))
    return dest, remote_only


def _run_remote_kernel(input_: dict, ctx: dict | None, project_id: str,
                       thread_id: str, site: str, lang: str = "python"):
    """Persistent interactive session ON a remote site (P1, misc/bug1.md): the
    same kernel-pool contract as the local lane — variables persist between
    calls — with outputs fetched over the weft data plane. Returns None when
    no remote kernel can be established (caller falls back to the one-shot
    sync lane); once a session EXISTS, results and errors are returned from
    it — its state is the point.

    `lang` selects the interpreter; the pool + setup are already language-
    and site-generic, so both interpreter lanes reach a persistent remote
    session (the fresh-process-per-call lane was what forced agent code to
    chdir out of the tracked sandbox to keep cross-step state — 2026-07-26)."""
    import time as _time
    from datetime import datetime as _dt, timezone as _tz
    from pathlib import Path
    from core.data.workspace import scratch_dir
    from core.exec.run import harvest_artifacts
    from core.exec.kernels import get_pool, KernelCapacityError
    from core.exec.output_cap import snip_middle

    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 300), 1800))
    cancel_token = (ctx or {}).get("cancel_token")
    from core.compute.named_envs import resolve_env
    env_name = resolve_env(project_id, lang, explicit=input_.get("env"))
    if env_name and env_name.lower() in ("system", "none"):
        env_name = "system"   # bare kernel: node interpreter, no realization
    elif env_name:
        from core.compute.named_envs import resolve as _env_resolve
        if _env_resolve(project_id, env_name) is None:
            return {"status": "error", "env": env_name,
                    "note": f"No isolated env '{env_name}'. Create it with "
                            f"make_isolated_env(name='{env_name}')."}
    scope_key = f"{thread_id}@{site}" + (f"::env::{env_name}" if env_name else "")
    start_ts = _time.time()
    started_iso = _dt.now(_tz.utc).isoformat()
    try:
        sess = get_pool().get_or_start(
            scope_key, lang,
            cwd=str(scratch_dir(project_id, f"thread-{thread_id}")),
            env_name=env_name, site=site)
    except KernelCapacityError as cap:
        return {"error": str(cap), "at_capacity": True}
    except Exception as e:  # noqa: BLE001 — classified: env failure vs no session
        from core.compute.errors import describe as _describe, is_env_resolution_failure
        # untyped_is_env=False: this try wraps the WHOLE kernel start (transport,
        # kernel protocol, capacity), not just env resolution, so only a TYPED
        # env verdict may cancel the legitimate one-shot fallback. And env='system'
        # resolves no project env at all — a start failure there is always a
        # "no kernel here" condition.
        if env_name != "system" and is_env_resolution_failure(
                e, untyped_is_env=False):
            # The env could not be resolved — NOT a "no kernel here" condition.
            # Falling through to the one-shot lane used to end with the step
            # running on the node's own interpreter (that lane swallowed the
            # same failure), so the agent got a silently different stack. Say
            # the cause instead; the one-shot lane would only fail identically.
            print(f"[run_{lang}] env unresolved for {site} "
                  f"({getattr(e, 'code', type(e).__name__)}) — refusing to "
                  f"relocate the step to the node interpreter", flush=True)
            return {"status": "error", "site": site,
                    "error": getattr(e, "code", "env.unresolved"),
                    "note": (f"The project's {lang} environment could not be "
                             f"resolved for {site}: {_describe(e, limit=400)} "
                             f"Nothing was run. This step was NOT relocated to "
                             f"the node's own interpreter — that would silently "
                             f"swap the project's environment for an arbitrary "
                             f"one. Levers: make_isolated_env(...) + env=<name>; "
                             f"env='system' for the node's bare interpreter on "
                             f"purpose; or repair the project environment if an "
                             f"install made it unsolvable."),
                    **({"hints": e.hints} if getattr(e, "hints", None) else {})}
        print(f"[run_{lang}] remote kernel unavailable on {site} "
              f"({type(e).__name__}: {e}); falling back to one-shot", flush=True)
        return None
    from content.bio.lifecycle.runs import (record_weft_target, active_run_id,
                                            note_run_site)
    _rid_now = active_run_id(str(thread_id))
    record_weft_target(_rid_now, getattr(sess, "kernel_id", None))
    # Placement stamps at DISPATCH, not completion: the stamp used to land
    # only when the step's result registered, so for the whole duration of a
    # long remote step the Run card claimed "ran locally" and only later
    # flipped (live UX finding 2026-07-26). The session's site is the truth
    # of where this step is EXECUTING — stamp it before execute() blocks.
    note_run_site(_rid_now, getattr(sess, "site", None))
    # Fresh-kernel orientation, on the REMOTE lane too. Only the two LOCAL
    # lanes called this, so a brand-new remote kernel produced a bare
    # "object 'x' not found" while the note below cheerfully said variables
    # persist — the agent then hunted a variable that never existed (live,
    # 2026-07-26). A weft kernel cannot chdir (its sandbox IS the work dir), and
    # _ensure_kernel_cwd already knows that: it skips the chdir and only marks
    # the fresh sentinel, which is exactly what we want here.
    _ensure_kernel_cwd(sess, lang,
                       scratch_dir(project_id, f"thread-{thread_id}"))
    inv0 = _kernel_sandbox_inventory(sess.kernel_id)
    res = sess.execute(code, cancel_token=cancel_token, timeout_s=timeout_s)
    if res.timed_out:
        return {"error": f"Code execution timed out ({timeout_s}s limit)"}
    if res.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}
    fetch_dir, remote_only = _fetch_new_kernel_files(
        sess.kernel_id, inv0, project_id, str(thread_id))
    plots, tables, files, warns = (harvest_artifacts(Path(fetch_dir), since_ts=0)
                                   if fetch_dir else ([], [], [], []))
    _fresh = getattr(sess, "_aba_cwd_just_switched", None) == "__FRESH__"
    if _fresh:
        # Do not promise persistence backwards on a kernel that was just born:
        # the honest statement is that state starts HERE.
        note = (f"ran on {site} in a NEW persistent session (nothing was in "
                f"memory before this call — reload objects from disk); "
                f"variables now persist for your next run_{lang}(site={site!r}) call")
    else:
        note = (f"ran on {site} in a persistent session there — variables persist "
                f"for your next run_{lang}(site={site!r}) call")
    if env_name == "system":
        note += (" (env='system': the node's own interpreter — no environment "
                 "realized, nothing installable)")
    if res.returncode == 0 and not (res.stdout or "").strip():
        # A silent successful block. This note has now been wrong TWICE, in
        # opposite directions, and both times for the same reason: it restated
        # the substrate's behaviour from memory instead of relaying it. First
        # it accused weft of a stdout capture race
        # (misc/bug2_weft_kernel_stdout.md) and sent the agent to write results
        # to a FILE — live (thr_a1f7f687) that cost three remote round trips
        # for one directory listing. The replacement swung the other way, to
        # "the kernel does not auto-print a bare top-level expression": true
        # when written, false one weft commit later (01fb968 gave the drivers
        # native REPL display), so it shipped a stale claim with CI green.
        #
        # So this wording deliberately says NOTHING about how the driver
        # displays values. That is weft's to know and weft's to report — the
        # rest of aba's substrate-facing text already relays a typed verdict
        # rather than restating one (see discovery.py). What is left is true of
        # any driver: the block ran, it printed nothing, and the reasons are
        # language-level (a side-effecting call, an invisible/None value).
        # The last sentence is the only part that has been right throughout;
        # it is what makes a REAL capture fault reportable.
        # Guarded by test_remote_kernel_lane.py::
        # test_silent_block_note_makes_no_claim_about_driver_echo.
        note += (". NOTE: this block produced no stdout (exit 0 — it DID run). "
                 "Usually nothing was printed: the code only assigned, or ran "
                 "for its side effects, or its final value was one the language "
                 + ("does not show (invisible(), an assignment)"
                    if lang == "r" else "does not show (None)")
                 + ". If you expected to see a value, print it explicitly — "
                 + ("cat()/print()" if lang == "r" else "print()")
                 + ". If you DID print explicitly and still see nothing, say "
                 "so: that is a capture fault worth reporting, not normal.")
    if remote_only:
        note += (f". {len(remote_only)} larger output(s) remain on {site} — still "
                 f"yours by NAME (find_files finds them; opening fetches on demand): "
                 + ", ".join(remote_only[:5])
                 + ("…" if len(remote_only) > 5 else ""))
    out = {"stdout": snip_middle(res.stdout or ""),
           "stderr": snip_middle(res.stderr or ""),
           "returncode": res.returncode, "plots": plots, "tables": tables,
           "files": files, "execution_mode": "remote-session",
           "compute": {"substrate": "weft", "kernel_id": sess.kernel_id,
                       "site": site},
           "note": note}
    if env_name:
        out["env"] = env_name
    # sidecar goes to LOCAL thread scratch — the kernel's work_dir is a
    # REMOTE path; passing its "site:krn_x" label as cwd mkdir'd literal
    # `site:krn_*` dirs under the backend process cwd (found as droppings
    # in the repo after the live studies)
    _eid = _write_exec_record(
        lang=lang, ctx=ctx, code=code,
        cwd=str(scratch_dir(str(project_id), f"thread-{thread_id}")),
        sess=sess,
        started_iso=started_iso, started_ts=start_ts, res=res,
        plots=plots, tables=tables, files=files,
    )
    if _eid:
        out["exec_id"] = _eid
    if warns:
        out["figure_warnings"] = warns
    if res.returncode == 0:
        ns = _kernel_namespace_preview(sess, lang)
        if ns:
            out["namespace"] = ns
        # The probe just told us where the kernel ended up. If that is no longer
        # the sandbox, this step's relative writes are untracked — say so NOW,
        # while the agent can still register or relocate them.
        _drift = _untracked_write_note(sess, lang)
        if _drift:
            out["note"] = (out.get("note") or "") + _drift
            out["untracked_writes"] = True
    # Same orientation block the local lanes render: WORK_DIR, registered
    # datasets and files already on disk, with the fresh-kernel header when the
    # session was just created. Also re-fires when the block died on a missing
    # path — the remote lane is where wrong-path guessing actually costs a
    # round trip to another machine.
    _maybe_force_preamble_on_file_error(sess, res.stderr or "", res.stdout or "")
    if getattr(sess, "_aba_cwd_just_switched", None):
        _was = sess._aba_cwd_just_switched
        preamble = _prior_run_files_preamble(
            str(project_id), str(thread_id), current_run_id=_rid_now,
            cwd=getattr(sess, "_aba_cwd", None),
            fresh_kernel=(_was == "__FRESH__"),
            remote_site=site, lang=lang)
        sess._aba_cwd_just_switched = None
        if preamble:
            out["stdout"] = preamble + "\n" + (out["stdout"] or "")
    return out


def _run_remote_sync(input_: dict, ctx: dict | None, project_id: str,
                     thread_id: str, kind: str) -> dict:
    """Synchronous remote run (misc/detached_compute.md): placement is
    ORTHOGONAL to duration — a short step on a declared machine behaves
    exactly like a local call (submit, wait in-tool, return the result),
    just executed THERE in a fresh process. Long steps use background=True
    (deferred + continuation), same contract as a cluster deployment.

    The job ROW is still created (Jobs panel visibility, durable state,
    cancel path) but marked `sync` so the weft poll loop leaves it to us —
    completion here returns a NORMAL tool result, and the standard post-tool
    registration attaches figures/tables to the Run like any local call."""
    import time as _time
    from core.compute.errors import ComputeError
    from core.jobs.submit import submit_python_job, submit_r_job
    from core.jobs.weft_submitter import WeftSubmitter
    from core.graph.jobs import get_job, update_job
    from content.bio.lifecycle.runs import active_run_id

    site = input_["site"]
    timeout_s = max(5, min(int(input_.get("timeout_s") or 300), 1800))
    submit = submit_r_job if kind == "run_r" else submit_python_job
    # Env identity — SAME rules as every lane: env=None follows the project's
    # active env for the step's LANGUAGE (resolve_env); 'default'/reserved →
    # None (pack snapshot); a NAMED env must exist (the detached submitter
    # would otherwise silently fall back to the node system runtime).
    from core.compute.named_envs import resolve_env
    env_name = resolve_env(project_id, "r" if kind == "run_r" else "python",
                           explicit=input_.get("env"))
    if env_name and env_name.lower() in ("system", "none"):
        env_name = "system"   # P2 lever: node interpreter, no pack realization
    elif env_name:
        from core.compute.named_envs import resolve as _env_resolve
        if _env_resolve(project_id, env_name) is None:
            return {"status": "error", "env": env_name,
                    "note": f"No isolated env '{env_name}'. Create it with "
                            f"make_isolated_env(name='{env_name}')."}
    try:
        job = submit(input_.get("code", ""),
                     title=input_.get("title") or f"Remote step on {site}",
                     focus_entity_id=(ctx or {}).get("focus_entity_id"),
                     project_id=project_id, thread_id=thread_id,
                     run_id=active_run_id(thread_id),
                     estimate={"runtime_min": float(input_.get("estimated_runtime_min") or 0),
                               "cores": input_.get("est_cores"),
                               "mem_gb": input_.get("est_mem_gb"),
                               "gpu": input_.get("est_gpu")},
                     env=env_name, site=site, timeout_s=timeout_s,
                     sync=True)  # BORN sync — before the substrate submit,
                                 # so the poll loop never adopts this row
    except ValueError as e:          # unknown site / substrate offline
        return {"status": "error", "note": str(e)}
    except ComputeError as e:        # substrate submit died; row marked failed
        from core.compute.errors import describe
        return {"status": "error",
                "note": f"could not submit to {site}: {describe(e)}"}
    sub = WeftSubmitter(site=site)
    cancel_token = (ctx or {}).get("cancel_token")

    def _kill():
        # cancel the FRESH row — the stale submit-return dict has no weft_id
        # (written by _submit_detached AFTER submit returns), so cancelling it
        # is a silent no-op that orphans the remote task (review Defect 1)
        sub.cancel(get_job(job["id"], project_id=project_id) or job)

    t0 = _time.time()
    while _time.time() - t0 < timeout_s + 60:
        if cancel_token is not None and getattr(cancel_token, "cancelled", False):
            _kill()
            update_job(job["id"], project_id=project_id, status="cancelled")
            return {"status": "cancelled",
                    "note": f"remote step on {site} cancelled"}
        row = get_job(job["id"], project_id=project_id)
        res = sub.poll(row)
        if res is not None:
            # a substrate-cancelled task returns {status: cancelled} with no
            # error/returncode — must NOT be read as success (review Defect 2)
            if res.get("status") == "cancelled":
                update_job(job["id"], project_id=project_id, status="cancelled")
                return {"status": "cancelled", "compute": res.get("compute"),
                        "note": f"the remote step on {site} was cancelled on the "
                                f"compute substrate"}
            ok = "error" not in res and res.get("returncode", 0) == 0
            update_job(job["id"], project_id=project_id,
                       status="done" if ok else "failed",
                       # success must never leave a stale failure string behind
                       **({"error": None} if ok else {}),
                       log_tail=(res.get("stdout") or res.get("error") or "")[-1500:])
            if not ok:
                return {"status": "error",
                        "error": (res.get("error") or res.get("stdout") or "")[-2000:],
                        "compute": res.get("compute"),
                        "note": f"the step FAILED on {site} — see error; fix and retry"}
            out = {"status": "ok", "stdout": res.get("stdout", ""),
                   "plots": res.get("plots", []), "tables": res.get("tables", []),
                   "files": res.get("files", []), "compute": res.get("compute"),
                   "cwd": str(sub._run_dir(row)),
                   "execution_mode": "remote-sync",
                   "note": f"ran on {site} in a fresh process there "
                           f"(no interactive state); outputs harvested back"}
            # warnings were silently DROPPED on this lane (blank figures, size
            # caps, and now the cwd-escape signal) — pass them through like
            # every other lane does
            if res.get("warnings"):
                out["output_warnings"] = res["warnings"]
            # Provenance parity with the background lane: write the exec record
            # (code + produced + the weft placement block "ran on <site>") and
            # inject exec_id, so the on_post_tool hook links artifacts to it and
            # a pinned figure traces back to where it actually ran.
            try:
                from core.jobs.runner import _write_exec_record_for_job
                _write_exec_record_for_job(row, out, project_id, project_id)
            except Exception:  # noqa: BLE001 — provenance is best-effort
                pass
            return out
        _time.sleep(1.5)
    _kill()      # fresh-row cancel (Defect 1): the task walltime outlives our
                 # timeout_s+60 loop, so it IS still running here
    update_job(job["id"], project_id=project_id, status="failed",
               error=f"timed out after {timeout_s}s")
    return {"status": "error",
            "note": f"the remote step exceeded {timeout_s}s and was cancelled — "
                    f"for long work use background=True (you'll be resumed when "
                    f"it finishes)"}


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
    from core.config import KERNEL_ENABLED
    from core import projects

    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 300), 1800))
    cancel_token = (ctx or {}).get("cancel_token")
    project_id = projects.current() or "default"
    thread_id = (ctx or {}).get("thread_id") or "default"

    # Placement FIRST (misc/detached_compute.md): an explicit site= must never
    # fall into the local lanes below — the named-env block realizes the env
    # LOCALLY (minutes of waste; the site realizes its own copy via weft) and
    # the kernels-off fallback would run the code HERE while the agent believes
    # it ran remotely. run_r routes the same way.
    if input_.get("site") and not input_.get("background"):
        # SYNC remote run. Prefer the PERSISTENT session on the site (P1:
        # variables survive between calls — multi-step remote work stops
        # reloading state from disk every step); a kernel that can't start
        # falls back to the one-shot fresh-process lane, never to local.
        site = str(input_["site"]).strip()
        if (site and site != "local" and KERNEL_ENABLED
                and not input_.get("fresh") and not input_.get("_kernel_fallback")):
            out = _run_remote_kernel(input_, ctx, str(project_id),
                                     str(thread_id), site)
            if out is not None:
                return out
        return _run_remote_sync(input_, ctx, str(project_id), str(thread_id),
                                "run_python")

    # §11.3: env=<named isolated env> runs in THAT env's persistent kernel — the
    # same interactive kernel path below, just a distinct scope-key + the env's
    # python (so state persists + plots harvest, unlike the old one-shot). Default/
    # reserved → the normal served stack. Kernel disabled → stateless fallback.
    # §11.2: env=None follows the project's active python env; an explicit value
    # (including 'default') overrides it.
    from core.compute.named_envs import resolve_env
    env_name = resolve_env(project_id, "python", explicit=input_.get("env"))
    if env_name and env_name.lower() in ("system", "none"):
        # env='system' = a machine's BARE interpreter — meaningful for a
        # placed step (site=...); the local served stack IS this machine's
        # python, so a local bare run would only lose the project's packages.
        return {"status": "error", "env": "system",
                "note": "env='system' applies to placed steps (site=...): it "
                        "runs on that machine's own interpreter with no "
                        "environment realized. For a local step, omit env — "
                        "the project environment is already here."}
    if env_name:
        from core.compute import named_envs
        from core.compute.errors import ComputeError
        # weft rebuilds a GC-reclaimed env from its lock transparently at
        # realization (the old §11.6 story).
        row = named_envs.resolve(str(project_id), env_name)
        if row is None:
            return {"status": "error", "env": env_name,
                    "note": f"No isolated env '{env_name}'. Create it with "
                            f"make_isolated_env(name='{env_name}')."}
        # Realize HERE, before the kernel pool — get_or_start holds the pool
        # lock across kernel startup, and a first-use realization (minutes)
        # under that lock would wedge every kernel acquisition process-wide.
        # ensure_READY (strategy-blind): a squashfs env has no raw prefix and we
        # need none here — the kernel lane activates the EnvID through weft.
        # A site= job skips both: weft realizes the EnvID at the SITE, and the
        # kernels-off fallback must not hijack a remote-targeted background run.
        if not input_.get("site"):
            try:
                named_envs.ensure_ready(row["env_id"])
            except ComputeError as ce:
                return {"status": "error", "env": env_name, "error": ce.to_payload(),
                        "note": f"env '{env_name}' could not be realized: "
                                f"{ce.detail or ce.code}"}
            if not KERNEL_ENABLED:   # kernels off → stateless one-shot fallback
                return _run_in_named_env(env_name, code, "python", timeout_s)

    # Lane selection (kernels.md §7): background > fresh > interactive.
    # - background: stateless job, deferred result the guide loop resumes from.
    # - fresh: stateless one-shot subprocess (isolated/reproducible; no session).
    # - interactive (default): the thread's persistent kernel (state persists).
    # timeout_s is a CEILING, not an estimate; routing to background keys on the
    # agent's estimated_runtime_min so a defensive timeout doesn't mis-background.
    override = "background" if input_.get("background") else None
    est_min = float(input_.get("estimated_runtime_min") or 0)
    est = {"runtime_min": est_min, "cores": input_.get("est_cores"),
           "mem_gb": input_.get("est_mem_gb"), "gpu": input_.get("est_gpu")}
    from core.exec.compute_env import compute_env
    from core.exec.router import decide
    choice = decide(env=compute_env(), estimate=est, override=override)
    if choice.location == "background":
        from core.compute.errors import ComputeError
        from core.jobs.runner import submit_python_job
        from content.bio.lifecycle.runs import active_run_id
        # Carry the agent's estimate + execution + isolated env + estimate-sized
        # timeout so a Slurm deployment can size partition/QoS + pick a GPU node.
        # bg_submit_kwargs is the SINGLE source shared with guide.py's background
        # intercept — neither path drops the placement estimate.
        try:
            job = submit_python_job(code, title=input_.get("title") or "Background analysis",
                                    focus_entity_id=(ctx or {}).get("focus_entity_id"),
                                    project_id=str(project_id), thread_id=str(thread_id),
                                    run_id=active_run_id(str(thread_id)),
                                    **bg_submit_kwargs(input_, project_id))
        except ValueError as e:      # unknown site= / substrate offline
            return {"status": "error", "note": str(e)}
        except ComputeError as e:    # substrate submit died; row marked failed
            from core.compute.errors import describe
            return {"status": "error",
                    "note": f"background submit failed: {describe(e)}"}
        return {
            "deferred": True, "deferred_id": job["id"], "job_id": job["id"],
            "status": "submitted",
            "note": f"Submitted as background job {job['id']} ({choice.rationale}). "
                    f"It runs in a FRESH process (no interactive state). You'll be resumed "
                    f"automatically when it finishes, and the user can watch its live output "
                    f"in the Jobs panel meanwhile — so end your turn here rather than polling "
                    f"its status.",
        }

    # Interactive persistent kernel — the default. State persists across calls
    # within this thread, so the agent reuses loaded data / fitted models.
    if KERNEL_ENABLED and (env_name or not input_.get("fresh")):
        try:
            from datetime import datetime as _dt, timezone as _tz
            from core.exec.kernels import get_pool
            from core.data.workspace import scratch_dir
            # cwd = the active Run's own output dir (so a pipeline's files land in
            # one browsable bundle), else the shared thread scratch dir.
            cwd = _run_scratch_cwd(str(project_id), str(thread_id))
            start_ts = _time.time()
            started_iso = _dt.now(_tz.utc).isoformat()
            # §11.3: an isolated env gets its own persistent kernel (distinct
            # scope-key + the env's python); the shared thread scratch cwd is
            # reused so files hand off to/from the default kernel.
            scope_key = str(thread_id) if not env_name else f"{thread_id}::env::{env_name}"
            # W3.0: base-pack default lane — resolve BEFORE the pool lock (a
            # first-use base solve/realize under it would wedge every kernel
            # acquisition; a lazy session itself is cheap — it runs from the
            # base realization until first install).
            if not env_name:
                from core.compute import base_env, project_env
                from core.compute.errors import ComputeError
                try:
                    base_env.require("python")   # weft-only: no served-base fallback
                    project_env.ensure(str(project_id), "python")
                except ComputeError as ce:
                    return {"status": "error", "error": ce.to_payload(),
                            "note": f"the python environment pack is not "
                                    f"available: {ce.detail or ce.code}"}
                except RuntimeError as re_:
                    return {"status": "error", "note": str(re_)}
            from core.exec.kernels import KernelCapacityError
            try:
                sess = get_pool().get_or_start(scope_key, "python",
                                               cwd=str(scratch_dir(str(project_id), f"thread-{thread_id}")),
                                               env_name=env_name)
            except KernelCapacityError as _cap:
                return {"error": str(_cap), "at_capacity": True}
            _ensure_kernel_cwd(sess, "python", cwd)
            # Persist the weft target on the Run so retention can name it after the
            # kernel stops (run_inventory/run_retain/run_forget). No-op for jupyter.
            from content.bio.lifecycle.runs import record_weft_target, active_run_id
            record_weft_target(active_run_id(str(thread_id)), getattr(sess, "kernel_id", None))
            res = sess.execute(_with_cwd_probe(sess, code, "python", cwd),
                               cancel_token=cancel_token, timeout_s=timeout_s)
            if res.timed_out:
                return {"error": f"Code execution timed out ({timeout_s}s limit)"}
            if res.cancelled:
                return {"status": "cancelled",
                        "note": f"Run was cancelled by the user "
                                f"({getattr(cancel_token, 'reason', '')}). No further work happened."}
            # weft kernels write into their sandbox (sess.work_dir), not aba scratch
            # — they can't chdir. Harvest from there; jupyter falls back to cwd.
            # GAP WINDOW (harvest honesty): scan from the END of the previous
            # harvest, not this block's start — a background writer's files
            # landing BETWEEN blocks used to fall in the gap and vanish; now
            # they attach to the next block (tracked beats lost).
            _hts = _time.time()
            plots, tables, files, warns = harvest_artifacts(
                getattr(sess, "work_dir", None) or cwd,
                since_ts=getattr(sess, "_aba_last_harvest_ts", None) or start_ts)
            sess._aba_last_harvest_ts = _hts
            warns = [*warns, *_reconcile_kernel_cwd(sess, cwd)]
            # Session-derived: reproduction needs this thread's ordered cells,
            # not the single cell alone (kernels.md §8.1).
            from core.exec.output_cap import snip_middle
            out = {"stdout": snip_middle(res.stdout or ""), "stderr": snip_middle(res.stderr or ""),
                   "returncode": res.returncode, "plots": plots, "tables": tables,
                   "files": files, "execution_mode": "session"}
            if env_name:
                out["env"] = env_name
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
            # Re-fire path orientation when the run errored on a missing
            # file/path (prj_a6f40e94 friction). Sets the just_switched
            # flag, which the existing block below renders.
            _maybe_force_preamble_on_file_error(
                sess, res.stderr or "", res.stdout or "")
            if getattr(sess, "_aba_cwd_just_switched", None):
                from content.bio.lifecycle.runs import active_run_id as _arid
                _was = sess._aba_cwd_just_switched
                preamble = _prior_run_files_preamble(str(project_id), str(thread_id),
                                                    current_run_id=_arid(str(thread_id)),
                                                    cwd=getattr(sess, "_aba_cwd", None),
                                                    fresh_kernel=(_was == "__FRESH__"),
                                                    remote_site=_remote_site_of(sess),
                                                    lang=getattr(sess, "lang", None))
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
                                 timeout_s=timeout_s, cancel_token=cancel_token)
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
    from core.config import KERNEL_ENABLED
    from core import projects

    code = input_.get("code", "")
    timeout_s = max(5, min(int(input_.get("timeout_s") or 600), 1800))
    cancel_token = (ctx or {}).get("cancel_token")
    project_id = projects.current() or "default"
    thread_id = (ctx or {}).get("thread_id") or "default"

    # §11.2: env=<named isolated R env>; env=None follows the project's active
    # R env (set_active_env(..., language='r') — how a package needing SYSTEM
    # libraries the base lacks becomes ambient for bare run_r). Route FIRST so
    # a backgrounded run+env goes to a queued/Slurm job that runs IN that env
    # (its lib first on .libPaths()) — not short-circuited to the sync one-shot.
    from core.compute.named_envs import resolve_env
    env_name = resolve_env(str(project_id), "r", explicit=input_.get("env"))

    if input_.get("site") and not input_.get("background"):
        # SYNC remote run. Parity with run_python: prefer the PERSISTENT
        # session on the site (state survives between calls — multi-step
        # remote work stops reloading from disk each step, the shape that
        # forced cwd-escapes); a kernel that can't start falls back to the
        # one-shot fresh-process lane, never to local.
        site = str(input_["site"]).strip()
        if (site and site != "local" and KERNEL_ENABLED
                and not input_.get("fresh") and not input_.get("_kernel_fallback")):
            out = _run_remote_kernel(input_, ctx, str(project_id),
                                     str(thread_id), site, lang="r")
            if out is not None:
                return out
        return _run_remote_sync(input_, ctx, str(project_id), str(thread_id),
                                "run_r")
    override = "background" if input_.get("background") else None
    est_min = float(input_.get("estimated_runtime_min") or 0)
    est = {"runtime_min": est_min, "cores": input_.get("est_cores"),
           "mem_gb": input_.get("est_mem_gb"), "gpu": input_.get("est_gpu")}
    from core.exec.compute_env import compute_env
    from core.exec.router import decide
    choice = decide(env=compute_env(), estimate=est, override=override)
    if choice.location == "background":
        from core.compute.errors import ComputeError
        from core.jobs.runner import submit_r_job
        from content.bio.lifecycle.runs import active_run_id
        # Background jobs get a timeout sized from the estimate, NOT the interactive
        # 600s/30-min ceiling that `timeout_s` (above) carries — mirrors run_python
        # (the 2026-06-28 STAR-build incident). Without this, an R job with a 30-min
        # `estimated_runtime_min` but no explicit `timeout_s` was killed at the 600s
        # default (the IntegrateLayers retry that timed out).
        bg_timeout_s = _background_timeout_s(input_, est_min)
        try:
            job = submit_r_job(code, title=input_.get("title") or "Background R analysis",
                               focus_entity_id=(ctx or {}).get("focus_entity_id"),
                               timeout_s=bg_timeout_s, project_id=str(project_id),
                               thread_id=str(thread_id), run_id=active_run_id(str(thread_id)),
                               estimate=est, env=env_name, execution=input_.get("execution"),
                               site=input_.get("site") or None)
        except ValueError as e:      # unknown site= / substrate offline
            return {"status": "error", "note": str(e)}
        except ComputeError as e:    # substrate submit died; row marked failed
            from core.compute.errors import describe
            return {"status": "error",
                    "note": f"background submit failed: {describe(e)}"}
        return {
            "deferred": True, "deferred_id": job["id"], "job_id": job["id"],
            "status": "submitted",
            "note": f"Submitted as background R job {job['id']} ({choice.rationale}). "
                    f"Runs in a FRESH process (load inputs from disk); figures register on "
                    f"completion. You'll be resumed automatically when it finishes, and the "
                    f"user can watch its live output in the Jobs panel meanwhile — so end your "
                    f"turn here rather than polling its status.",
        }

    # Synchronous named R env (not backgrounded): the env's own PERSISTENT
    # kernel — parity with the python lane (new named R envs bake r-irkernel;
    # a pre-parity env whose kernel can't start degrades to the stateless
    # one-shot in the self-heal below, loudly, naming the one-time remedy).
    if env_name:
        from core.compute import named_envs
        from core.compute.errors import ComputeError as _CE
        _row = named_envs.resolve(str(project_id), env_name)
        if _row is None:
            return {"status": "error", "env": env_name, "language": "r",
                    "note": f"No isolated env '{env_name}'. Create it with "
                            f"make_isolated_env(name='{env_name}', language='r')."}
        if not KERNEL_ENABLED:      # kernels off → stateless one-shot fallback
            return _run_in_named_env(env_name, code, "r", timeout_s)
        # Realize before the pool lock (see the python lane: a first-use
        # realization under the pool lock would wedge every acquisition).
        try:
            named_envs.ensure_ready(_row["env_id"], language="r")
        except _CE as ce:
            return {"status": "error", "env": env_name, "error": ce.to_payload(),
                    "note": f"env '{env_name}' could not be realized: "
                            f"{ce.detail or ce.code}"}
    elif not KERNEL_ENABLED:
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
        # §11.3 parity: an isolated env gets its own persistent R kernel
        # (distinct scope-key + the env's R); default lane shares the thread
        # kernel over the base pack.
        scope_key = str(thread_id) if not env_name else f"{thread_id}::env::{env_name}"
        if not env_name:
            # W3.0: base-pack R lane — realize before the pool lock (see
            # python lane). An isolated env needs no base pack: its EnvID was
            # ensure_ready'd above.
            from core.compute import base_env, project_env
            from core.compute.errors import ComputeError
            try:
                base_env.require("r")       # weft-only: no served-base fallback
                project_env.ensure(str(project_id), "r")
            except ComputeError as ce:
                return {"status": "error", "error": ce.to_payload(),
                        "note": f"the R environment pack is not available: "
                                f"{ce.detail or ce.code}"}
            except RuntimeError as re_:
                return {"status": "error", "note": str(re_)}
        from core.exec.kernels import KernelCapacityError
        try:
            sess = get_pool().get_or_start(scope_key, "r",
                                           cwd=str(scratch_dir(str(project_id), f"thread-{thread_id}")),
                                           env_name=env_name)
        except KernelCapacityError as _cap:
            return {"error": str(_cap), "at_capacity": True}
        _ensure_kernel_cwd(sess, "r", cwd)
        from content.bio.lifecycle.runs import record_weft_target, active_run_id
        record_weft_target(active_run_id(str(thread_id)), getattr(sess, "kernel_id", None))
        res = sess.execute(_with_cwd_probe(sess, code, "r", cwd),
                           cancel_token=cancel_token, timeout_s=timeout_s)
    except Exception as e:  # noqa: BLE001
        # Parity with run_python's kernel self-heal: a transient failure (slow
        # first IRkernel boot on a fresh install) gets a hard reset + ONE
        # retry, then degrades to the stateless Rscript one-shot with a LOUD
        # warning — run_r previously returned a hard error on the first
        # hiccup while run_python healed itself.
        _ktries = int(input_.get("_kernel_tries", 0))
        print(f"[run_r] kernel attempt {_ktries + 1} failed: {e}")
        try:
            from core.exec.kernels import get_pool as _gp
            _gp().restart(str(thread_id) if not env_name
                          else f"{thread_id}::env::{env_name}", "r")
        except Exception:  # noqa: BLE001
            pass
        if _ktries < 1:
            return run_r({**input_, "_kernel_tries": _ktries + 1}, ctx)
        if env_name:
            # The env's kernel won't start (typically a pre-parity env with no
            # r-irkernel baked). Degrade to the env's OWN stateless one-shot —
            # never the default stack — and name the one-time remedy.
            result = _run_in_named_env(env_name, code, "r", timeout_s)
            if isinstance(result, dict):
                result["kernel_warning"] = (
                    f"⚠ Ran as a STATELESS one-shot in env '{env_name}' — its "
                    f"persistent kernel could not start (envs created before "
                    f"kernel parity lack r-irkernel; fix once with "
                    f"ensure_capability('r-irkernel', env='{env_name}')). "
                    f"Objects from earlier run_r calls are NOT available here.")
            return result
        from core.exec.run import run_r_code
        _rid = ((ctx or {}).get("run_id")
                or getattr(cancel_token, "run_id", None) or uuid.uuid4().hex)
        try:
            result = run_r_code(code, project_id=str(project_id),
                                run_id=str(_rid), timeout_s=timeout_s,
                                cancel_token=cancel_token)
        except Exception as e2:  # noqa: BLE001
            return {"error": f"R kernel error: {e}; "
                             f"stateless fallback also failed: {e2}"}
        if isinstance(result, dict):
            result["kernel_warning"] = (
                "⚠ Ran WITHOUT the persistent R session (it was temporarily "
                "unavailable). Objects and libraries from earlier run_r calls "
                "are NOT available here, and the working directory is a fresh "
                "per-run scratch dir — define everything in THIS call and use "
                "absolute paths for files you want to keep.")
        return result
    if res.timed_out:
        return {"error": f"R code timed out ({timeout_s}s limit)"}
    if res.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}
    # gap window + cwd reconcile: parity with the python kernel lane above
    _hts = _time.time()
    plots, tables, files, warns = harvest_artifacts(
        getattr(sess, "work_dir", None) or cwd,
        since_ts=getattr(sess, "_aba_last_harvest_ts", None) or start_ts)
    sess._aba_last_harvest_ts = _hts
    warns = [*warns, *_reconcile_kernel_cwd(sess, cwd)]
    from core.exec.output_cap import snip_middle
    out = {"stdout": snip_middle(res.stdout or ""), "stderr": snip_middle(res.stderr or ""),
           "returncode": res.returncode, "plots": plots, "tables": tables,
           "files": files, "execution_mode": "session"}
    if env_name:
        out["env"] = env_name          # the promoted/named env this session runs in
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
    # Re-fire path orientation when the run errored on a missing file/path.
    _maybe_force_preamble_on_file_error(sess, res.stderr or "", res.stdout or "")
    if getattr(sess, "_aba_cwd_just_switched", None):
        from content.bio.lifecycle.runs import active_run_id as _arid
        _was = sess._aba_cwd_just_switched
        preamble = _prior_run_files_preamble(str(project_id), str(thread_id),
                                             current_run_id=_arid(str(thread_id)),
                                             cwd=getattr(sess, "_aba_cwd", None),
                                             fresh_kernel=(_was == "__FRESH__"),
                                             remote_site=_remote_site_of(sess),
                                             lang=getattr(sess, "lang", None) or "r")
        sess._aba_cwd_just_switched = None
        if preamble:
            out["stdout"] = preamble + "\n" + (out["stdout"] or "")
    return out
