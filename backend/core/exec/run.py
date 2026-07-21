"""Shared Python execution core (capdat_impl.md P5).

One implementation behind both the synchronous `run_python` tool and the
background job runner, so a backgrounded run inherits the same project scratch
workspace, the materialized-library overlay (pylib on sys.path), the conda
tools env (on PATH), killpg cancellation, and png/csv artifact harvest. Before
P5 the background path was an older parallel copy that saw none of P1–P4.
"""
from __future__ import annotations
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional

from core.config import ARTIFACTS_DIR, DATA_DIR
from core.data.workspace import scratch_dir
from core.exec import MaterializingExecutor, Provisioning


def run_python_code(
    code: str,
    *,
    project_id: str,
    run_id: Optional[str] = None,
    timeout_s: int = 90,
    cancel_token=None,
    env: Optional[str] = None,
    interp: Optional[str] = None,
    stream: bool = False,
) -> dict:
    """Run `code` in the project's scratch workspace and return the run_python
    result shape ({stdout, stderr, returncode, plots, tables} | {error} |
    {status: cancelled}). Kept outputs are harvested to the artifact store; the
    on_post_tool / on_job_complete hook registers them as entities."""
    timeout_s = max(5, min(int(timeout_s or 90), 1800))
    run_id = run_id or uuid.uuid4().hex
    scratch = scratch_dir(str(project_id), str(run_id))

    # §11: an ISOLATED env (run_python(env=…, background=True)) runs STANDALONE —
    # its OWN python (a weft-realized prefix), no project overlay — matching the
    # interactive isolated-env kernel. weft rebuilds a GC-reclaimed realization
    # from the env's lock transparently at realize time.
    interp = (interp or "").strip() or None    # the param survives; branches below may set it
    _default_rt = None    # default lane: session RUNTIME drives the argv, not a path
    if env:
        from core.compute import named_envs
        from core.compute.errors import ComputeError
        try:
            py = named_envs.interpreter(str(project_id), env)
        except ComputeError as ce:
            return {"error": f"isolated env {env!r} is not available "
                             f"(project {project_id}): {ce.detail or ce.code}"}
        except Exception as e:  # noqa: BLE001
            return {"error": f"isolated env {env!r} is not available "
                             f"(project {project_id}): {e}"}
        interp = str(py)
    elif interp:
        # W3.4: a pre-resolved interpreter (a background job's spec carries the
        # snapshot/session prefix python resolved AT SUBMIT — the entry process
        # has no compute substrate). Standalone.
        pass
    else:
        # W3.5 weft-only: the default lane is the PROJECT's session over the
        # bundle-declared base pack — REQUIRED, no served-base fallback. A
        # deployment with no python pack is misconfigured (loud, structured).
        # The session's RUNTIME (not a bare interpreter path) shapes the exec:
        # a lazy session runs from its base realization in place, and a
        # mount-scoped prefix has no path outside its activation.
        from core.compute import base_env, project_env
        from core.compute.errors import ComputeError
        try:
            base_env.require("python")
            _default_rt = project_env.runtime(str(project_id), "python")
        except (ComputeError, RuntimeError) as ce:
            return {"error": f"the python environment pack is not available: {ce}"}

    # Preamble: DATA_DIR prepended. Every run is now a weft env (isolated named
    # env or the project's base-pack session) — STANDALONE, its own site-packages
    # authoritative; additions layer via extends_env / session_install, never
    # sys.path stacking. (The served-base project pylib overlay is gone.)
    from core.config import project_data_dir
    # `project_id` is authoritative (the job's project). The ambient
    # current_project_id() is the _workspace fallback on a Slurm compute node.
    _pid = str(project_id)
    _data_dir = project_data_dir(_pid)
    lines = [f"DATA_DIR = {str(_data_dir)!r}", "import sys as _sys"]
    # Provenance (provenance.md §3.3): seed the stateless run so a zero-delta
    # re-run is bit-stable; the seed is recorded in the exec record. Guarded —
    # numpy is optional and the user's own seed (if any) overrides this.
    _seed = 0
    lines.append(f"import random as _aba_rnd; _aba_rnd.seed({_seed})")
    lines.append(f"try:\n    import numpy as _aba_np; _aba_np.random.seed({_seed})\nexcept Exception: pass")
    (scratch / "script.py").write_text("\n".join(lines) + "\n" + code)
    # Harvest only what THIS run produced. When run_id is the active Run, the
    # scratch IS the Run's work dir (shared with prior cells), so filter by the
    # script's mtime (same FS as the outputs → no cross-host clock skew) to
    # avoid re-harvesting earlier cells' files as if this run made them.
    _since = (scratch / "script.py").stat().st_mtime

    ex = MaterializingExecutor()
    menv = ex.materialize(Provisioning())         # base-venv subprocess run harness
    # Build the run argv. Default lane: topology-blind argv from the session
    # runtime (direct prefix exec where permitted, activation-wrapped otherwise);
    # isolated-env / job-spec lanes carry a weft-resolved interpreter PATH.
    # Never fall back to the backend venv (sys.executable) for science code.
    # `used_interp` is the direct interpreter path when one exists — consumed by
    # the best-effort env fingerprint below, which skips on activation-only
    # topologies rather than lie.
    if _default_rt is not None:
        from core.compute import project_env as _penv
        run_argv = _penv.argv_for_runtime(_default_rt, "python",
                                          [str(scratch / "script.py")])
        _p = _default_rt.get("prefix")
        used_interp = (str(Path(_p) / "bin" / "python")
                       if (_default_rt.get("direct_exec") and _p) else "")
    else:
        used_interp = str(interp or "").strip()
        if not used_interp:
            return {"error": "no python interpreter resolved for this run (internal)"}
        run_argv = [used_interp, str(scratch / "script.py")]
    # Env-var parity with the interactive kernel (core/exec/kernels/weft.py
    # _weft_setup_code): the agent's code routinely reads WORK_DIR / DATA_DIR /
    # ARTIFACTS_DIR via `os.environ[...]` AND via the bare variable, so BOTH lanes
    # set BOTH forms — the kernel via its setup block, this one-shot lane via
    # env_vars (below) + the DATA_DIR variable prepended to `lines` above. Without
    # parity a backgrounded download crashes with KeyError: 'WORK_DIR' before doing
    # any work (live, 2026-06-03, prj_413593e1 job_53df2f2734); the env-only-in-one-
    # -lane split also bit run_python interactively (live, 2026-07-21). MPLBACKEND=Agg
    # keeps matplotlib headless.
    env_vars = {
        "WORK_DIR": str(scratch),
        "DATA_DIR": str(_data_dir),
        "ARTIFACTS_DIR": str(ARTIFACTS_DIR),
        "MPLBACKEND": "Agg",
        # Unbuffered stdout/stderr so a background job's prints reach job.log AS THEY
        # HAPPEN (Python block-buffers when stdout isn't a TTY, so without this a job's
        # output only lands at process exit — the Jobs-card "live" tail would show
        # nothing until completion regardless of how often the UI polls).
        "PYTHONUNBUFFERED": "1",
    }
    result = ex.exec(
        menv, run_argv,
        cwd=str(scratch), cancel_token=cancel_token, timeout_s=timeout_s,
        env_vars=env_vars, stream=stream,
    )

    if result.timed_out:
        return {"error": f"Code execution timed out ({timeout_s}s limit)"}
    if result.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}

    plots, tables, files, warns = harvest_artifacts(scratch, since_ts=_since,
                                                    project_id=str(project_id))
    from core.exec.output_cap import snip_middle
    # Provenance (provenance.md §3.1): snapshot the env DESCRIPTOR through the
    # interpreter that ran — the background/Slurm analog of the kernel-session
    # probe. Cheap (~0.1s), best-effort, never fails the run.
    from core.exec.fingerprint import package_versions_for_interpreter
    _pkg = package_versions_for_interpreter(used_interp, "python") if used_interp else {}
    _langver = _pkg.pop("__lang_version__", "") if isinstance(_pkg, dict) else ""
    out = {
        "stdout": snip_middle(result.stdout or ""),
        "stderr": snip_middle(result.stderr or ""),
        "returncode": result.returncode,
        "plots": plots,
        "tables": tables,
        "files": files,
        # Self-contained one-shot script — its producing_code reproduces it alone.
        "execution_mode": "stateless",
        "cwd": str(scratch),
        # Provenance fields consumed by _finalize_job → exec record.
        "language": "python",
        "language_version": _langver,
        "package_versions": _pkg,
        "seed": _seed,
    }
    if warns:
        out["figure_warnings"] = warns
    return out


def run_r_code(
    code: str,
    *,
    project_id: str,
    run_id: Optional[str] = None,
    timeout_s: int = 600,
    cancel_token=None,
    env: Optional[str] = None,
    interp: Optional[str] = None,
    stream: bool = False,
) -> dict:
    """Background R execution — mirrors run_python_code's return shape so the
    existing on_job_complete hook + artifact harvester work unchanged.

    Writes a self-contained `script.R` to the project's scratch dir with a
    preamble that:
      - sets .libPaths() to put the project R lib first (so per-project
        installs are visible);
      - sets working dir to scratch (so plot files land here for harvest);
      - exposes WORK_DIR / DATA_DIR / ARTIFACTS_DIR via Sys.getenv parity
        with the IRkernel.

    Then invokes Rscript via the same MaterializingExecutor that handles
    Python, so timeouts / killpg cancellation / progress are unified.
    """
    timeout_s = max(5, min(int(timeout_s or 600), 1800))
    run_id = run_id or uuid.uuid4().hex
    scratch = scratch_dir(str(project_id), str(run_id))

    from core.config import project_data_dir
    # Authoritative project (see run_python_code) — not the _workspace ambient.
    _data_dir = project_data_dir(str(project_id))

    # R preamble — kept short. The agent's own script.R follows verbatim.
    lib_lines: list[str] = []
    _default_rt = None    # default lane: session RUNTIME drives the argv, not a path
    if env:
        # §11: isolated R env — under weft a named R env is a FULL standalone
        # env (its own R + libs, a realized prefix), not a lib dir stacked on
        # the base. Use its Rscript; no .libPaths() juggling.
        from core.compute import named_envs
        from core.compute.errors import ComputeError
        try:
            rscript = named_envs.interpreter(str(project_id), env)
        except ComputeError as ce:
            return {"error": f"isolated R env {env!r} is not available "
                             f"(project {project_id}): {ce.detail or ce.code}"}
        except Exception as e:  # noqa: BLE001
            return {"error": f"isolated R env {env!r} is not available "
                             f"(project {project_id}): {e}"}
    elif interp:
        # W3.4: a pre-resolved Rscript from the job spec (see the python lane).
        from pathlib import Path as _P
        rscript = _P(interp)
    else:
        # W3.5 weft-only: the default R lane is the PROJECT's session over the
        # bundle-declared R base pack — REQUIRED, standalone (its own .libPaths,
        # no stack). No tools-env R fallback; a missing R pack is loud. As with
        # python, the session RUNTIME shapes the exec (lazy sessions run from
        # the base realization; mount-scoped prefixes are activation-only).
        from core.compute import base_env, project_env
        from core.compute.errors import ComputeError
        try:
            base_env.require("r")
            _default_rt = project_env.runtime(str(project_id), "r")
            rscript = None
        except (ComputeError, RuntimeError) as ce:
            return {"error": f"the R environment pack is not available: {ce}"}
    preamble_lines = list(lib_lines)
    preamble_lines.append(f'setwd({str(scratch)!r})')
    preamble_lines.append("set.seed(0)")   # provenance.md §3.3 — bit-stable re-run
    preamble = "\n".join(preamble_lines)
    (scratch / "script.R").write_text(preamble + "\n" + code)
    _since = (scratch / "script.R").stat().st_mtime   # harvest only this run's outputs

    ex = MaterializingExecutor()
    env = ex.materialize(Provisioning())
    env_vars = {
        "WORK_DIR": str(scratch),
        "DATA_DIR": str(_data_dir),
        "ARTIFACTS_DIR": str(ARTIFACTS_DIR),
        "ABA_PYTHON": sys.executable,  # let R shell out to Python if needed
    }
    # R block-buffers stdout to a pipe (no PYTHONUNBUFFERED equivalent), so a background R
    # job's prints wouldn't reach run.log until it exits — the Jobs-card live tail would sit
    # empty until completion. `stdbuf -oL` forces line-buffered stdio → live streaming (Item 2).
    # Only for streaming (background) runs; best-effort (skip if stdbuf is unavailable).
    _pre: list[str] = []
    if stream:
        import shutil as _sh
        _stdbuf = _sh.which("stdbuf")
        if _stdbuf:
            _pre = [_stdbuf, "-oL"]
    if _default_rt is not None:
        from core.compute import project_env as _penv
        r_cmd = _penv.argv_for_runtime(_default_rt, "r",
                                       ["--vanilla", str(scratch / "script.R")],
                                       pre=_pre)
        _p = _default_rt.get("prefix")
        rscript = (Path(_p) / "bin" / "Rscript"
                   if (_default_rt.get("direct_exec") and _p) else None)
    else:
        r_cmd = [*_pre, str(rscript), "--vanilla", str(scratch / "script.R")]
    result = ex.exec(
        env, r_cmd,
        cwd=str(scratch), cancel_token=cancel_token, timeout_s=timeout_s,
        env_vars=env_vars, stream=stream,
    )

    if result.timed_out:
        return {"error": f"R execution timed out ({timeout_s}s limit)"}
    if result.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}

    plots, tables, files, warns = harvest_artifacts(scratch, since_ts=_since,
                                                    project_id=str(project_id))
    from core.exec.output_cap import snip_middle
    # Provenance: snapshot the R env with the SAME .libPaths() the run used.
    # Best-effort — skipped (never faked) when no direct Rscript path exists
    # (activation-only topology).
    from core.exec.fingerprint import package_versions_for_interpreter
    _rpkg = (package_versions_for_interpreter(str(rscript), "r",
                                              r_preamble="\n".join(lib_lines))
             if rscript else {})
    _rver = _rpkg.pop("__lang_version__", "") if isinstance(_rpkg, dict) else ""
    out = {
        "stdout": snip_middle(result.stdout or ""),
        "stderr": snip_middle(result.stderr or ""),
        "returncode": result.returncode,
        "plots": plots,
        "tables": tables,
        "files": files,
        # Self-contained one-shot script.R — its producing_code reproduces it alone.
        "execution_mode": "stateless_r",
        "cwd": str(scratch),
        "language": "r",
        "language_version": _rver,
        "package_versions": _rpkg,
        "seed": 0,
    }
    if warns:
        out["figure_warnings"] = warns
    return out


_FILE_EXTS = (
    ".pdf", ".svg", ".html", ".htm",
    ".rds", ".h5", ".h5ad", ".npy", ".npz",
    ".xlsx", ".parquet",
    ".json", ".yaml", ".yml", ".md", ".txt",
)
_MAX_HARVEST_BYTES = 50 * 1024 * 1024   # 50 MB — bigger files are link-only, not copied


# Directory names harvest never descends into — caches/state that pile up
# during a run but aren't user-facing outputs.
_HARVEST_SKIP_DIRS = frozenset((
    "__pycache__", ".ipynb_checkpoints", ".git", ".cache",
    "node_modules", ".pytest_cache", ".mypy_cache",
))


def _iter_kept(scratch: Path, suffixes: tuple[str, ...], since_ts: float):
    """Walk `scratch` recursively for files whose suffix matches `suffixes`
    (lowercased compare). Yields Path objects mtime-filtered by since_ts;
    skips hidden files, thumb sidecars, and known-transient subdirs.

    Recursive harvest fixes the case where a recipe writes per-sample
    plots into a subdir (e.g. pagoda2's pagoda2_GSM.../qc_*.png) — those
    used to be invisible to the chat tool-result even though they showed
    up in the Run view (2026-06-04)."""
    suff = tuple(s.lower() for s in suffixes) if suffixes else None  # None = ANY suffix
    for f in scratch.rglob("*"):
        # Skip any path under a transient subdir at any depth.
        if any(part in _HARVEST_SKIP_DIRS for part in f.parts):
            continue
        if not f.is_file():
            continue
        if f.name.startswith("."):
            continue
        # Skip rasterized-preview sidecars (PDFs and any future
        # non-raster figure formats). Both the legacy .thumb.png name
        # (pre-2026-06-07) and the current .preview.png name are
        # filtered so a re-harvest doesn't surface caches as content.
        if f.name.endswith(".thumb.png") or f.name.endswith(".preview.png"):
            continue
        if suff is not None and f.suffix.lower() not in suff:
            continue
        try:
            if f.stat().st_mtime < since_ts:
                continue
        except OSError:
            continue
        yield f


def harvest_artifacts(scratch: Path, since_ts: float = 0.0,
                      project_id: Optional[str] = None,
                      max_files: Optional[int] = None
                      ) -> tuple[list, list, list, list]:
    """Copy kept outputs from a working dir into the artifact store and return
    `(plots, tables, files, warnings)`.

    - `plots` — `*.png` / `*.jpg` figures, rendered inline in chat.
    - `tables` — `*.csv` / `*.tsv` — surfaced as data viewers.
    - `files` — anything else useful (PDF, HTML, RDS, h5/h5ad, parquet/xlsx,
      JSON/YAML/Markdown/TXT, NumPy arrays, SVG, TSV). Each gets a hashed copy
      served at `/artifacts/<pid>/<hash><ext>` so chat can link to it by name.
      Caps at 50 MB per file — bigger files are listed in the Files tab but
      not auto-copied (would balloon disk).
    - `warnings` — blank-PNG detections + size-skips.

    Walks the scratch dir RECURSIVELY (rglob) so that recipes which organize
    outputs into per-sample subdirectories (e.g. pagoda2's pagoda2_<sample>/
    qc_*.png + umap_*.png) surface those plots in the chat tool-result, not
    only in the Run view (2026-06-04 fix). Transient dirs (__pycache__ etc.)
    are skipped — see `_HARVEST_SKIP_DIRS`.

    `since_ts` filters to files created/modified at-or-after that time —
    needed for a PERSISTENT kernel cwd where earlier cells' files remain
    (avoids re-harvesting them). Copies (not moves) so the agent can still
    read its own output file in a later cell.

    `max_files` caps how many artifacts are COPIED into the store (None =
    no cap, today's behavior). The external-import path (misc/external_import.md)
    passes a cap: an outside results tree can hold hundreds of per-sample QC
    files, but they stay fully browsable via the Run's manifest (no copy), so we
    only entity-fy a high-signal cap and record how many were skipped.

    BLANK figures are dropped, not shown. A PNG that is a single flat colour
    (matplotlib saved an empty/never-drawn canvas) carries no information;
    surfacing it as a white box misleads the user and the agent both. Such
    files are excluded from `plots` and reported in `warnings` so the agent
    learns the plot FAILED (point-of-use guardrail)."""
    scratch = Path(scratch)
    plots, tables, files, warnings = [], [], [], []
    from core.config import project_artifacts_dir
    from core.projects import current_project_id
    # `project_id` is passed by the background/Slurm runners, where the ambient
    # current_project_id() is the no-project `_workspace` fallback (the compute
    # node never bound the project) — using it would bucket artifacts under
    # /artifacts/_workspace/ instead of the real project, orphaning them from
    # the Run. Interactive callers omit it and keep the ambient project.
    pid = project_id or current_project_id()
    adir = project_artifacts_dir(pid)
    import time as _time
    _harvest_begin = _time.time()   # agent's own writes precede this; our copies follow it
    _created: set = set()           # store names WE record this call (excluded from the off-convention pass)

    _skipped_cap = [0]   # files not copied because max_files was reached (reported in warnings)

    def _copy_and_record(f: Path, bucket: list, ext: str) -> None:
        if max_files is not None and len(_created) >= max_files:
            # Cap reached: DON'T copy into the served store, but still ADVERTISE
            # the file (link-only) so it lands in produced[] → a retain candidate,
            # browsable + downloadable from the sandbox/retained tier via the
            # tier-resolving /file route. The prior behavior only bumped a counter
            # and dropped the entry entirely, so a capped output vanished from the
            # manifest — the agent said "wrote X" and the user had no way to get it
            # (live 2026-07-21). Same shape as the oversize link-only branch.
            _skipped_cap[0] += 1
            try:
                display = str(f.relative_to(scratch))
            except ValueError:
                display = f.name
            try:
                nbytes = f.stat().st_size
            except OSError:
                nbytes = 0
            bucket.append({"url": None, "original_name": display,
                           "bytes": nbytes, "link_only": True})
            return
        # Identity is DERIVED from content, never minted by the copy. The store
        # name is the file's sha256 (truncated to 128 bits — same length/shape
        # as the uuid names it replaces), so: same bytes → same name (re-running
        # a step, or the remote-kernel scrape re-landing an unchanged file, is a
        # no-op instead of a second full copy); different bytes → different name
        # (no clobber); and equality across runs/references is a name compare.
        # The random-uuid scheme this replaces LOOKED content-addressed (flat
        # hex names) but wasn't — every harvest minted fresh identity, so dedup,
        # idempotence, and cross-run equality silently didn't exist.
        import hashlib as _hashlib
        _h = _hashlib.sha256()
        try:
            with open(f, "rb") as _fh:
                for _chunk in iter(lambda: _fh.read(1 << 20), b""):
                    _h.update(_chunk)
        except OSError:
            return                      # vanished mid-harvest — nothing to record
        digest = _h.hexdigest()
        dest_name = f"{digest[:32]}{ext}"
        _created.add(dest_name)
        dest = adir / dest_name
        if not dest.exists():           # dedup: identical bytes already stored
            # hardlink first (instant, no bytes moved — both names share inodes,
            # matching curation's _hardlink_tree idiom); cross-device → full copy.
            try:
                os.link(str(f), str(dest))
            except OSError:
                shutil.copy2(str(f), str(dest))
        # original_name preserves the subdir context so the agent knows
        # WHERE the file lived (e.g. 'pagoda2_GSM.../qc_violin.png'), not
        # just the bare leaf — useful when multiple subdirs each have
        # qc_violin.png and the agent needs to distinguish them.
        try:
            display = str(f.relative_to(scratch))
        except ValueError:
            display = f.name
        try:
            nbytes = f.stat().st_size
        except OSError:
            nbytes = 0
        # record the size so the durable Files panel shows real bytes for
        # normally-copied files too (not just oversize link-only ones).
        # sha256 makes the promised produced[] field real (core.exec.artifacts
        # read it for years; nothing ever wrote it) — locate_run_output's
        # harvested tier gets a genuine content digest.
        bucket.append({"url": f"/artifacts/{pid}/{dest_name}",
                       "original_name": display, "bytes": nbytes,
                       "sha256": digest})

    # 1) Figures
    for f in _iter_kept(scratch, (".png",), since_ts):
        if _png_is_blank(f):
            warnings.append(
                f"Figure '{f.name}' came out BLANK (one flat colour — no data was "
                f"drawn) and was dropped. The plot FAILED: check the data isn't empty "
                f"(n_obs/rows > 0), the embedding/columns you coloured by exist, and "
                f"you didn't savefig before plotting. Do NOT present it as a result."
            )
            continue
        _copy_and_record(f, plots, ".png")

    # 2) Tables
    for f in _iter_kept(scratch, (".csv", ".tsv"), since_ts):
        _copy_and_record(f, tables, f.suffix.lower())

    # 4) Skipped-shape files: anything new whose suffix is outside the
    # harvest keep-lists. Previously these vanished COMPLETELY — no copy, no
    # manifest row, no warning — so a name the agent just wrote stopped being
    # real (the vanishing-name class; found by the producer-fed oversize
    # guard). Record link-only rows: the name stays resolvable via the
    # manifest tier, the file stays a retain candidate, and the search door
    # can answer for it. Same caps discipline as everything else (counted,
    # not silent).
    _known = _FILE_EXTS + (".png", ".csv", ".tsv")
    for f in _iter_kept(scratch, None, since_ts):
        if f.suffix.lower() in _known or f.name in _created:
            continue
        try:
            st = f.stat()
        except OSError:
            continue
        try:
            display = str(f.relative_to(scratch))
        except ValueError:
            display = f.name
        if any(x.get("original_name") == display for x in files):
            continue
        files.append({"url": None, "original_name": display,
                      "bytes": st.st_size, "link_only": True,
                      "skipped_shape": True})

    # 3) Other useful files — PDFs, HTML, RDS, h5ad, etc. Cap each at
    # MAX_HARVEST_BYTES; oversize ones go to warnings so the agent can
    # mention them but they're not auto-copied to /artifacts.
    #
    # PDF special-case: a single-page PDF written by recipe/agent code
    # (typical for figure exports via cairo_pdf, ggsave, matplotlib's
    # PdfPages with one page) is conceptually a FIGURE — it deserves
    # to land in `plots` so it ends up on a Result, gets a preview
    # rasterization, and joins the revisions chain. Multi-page PDFs
    # are reports/manuscripts and stay in `files` where downloads live.
    for f in _iter_kept(scratch, _FILE_EXTS, since_ts):
        try:
            st = f.stat()
        except OSError:
            continue
        if st.st_size > _MAX_HARVEST_BYTES:
            # Too large to copy into the served artifact store (would balloon
            # disk), but NOT dropped: record a link-only entry so it lands in
            # produced[] (→ a retain candidate — weft is its only durable home)
            # and shows in the Files tab. No served `url` — it isn't inline-
            # linkable until retained/fetched. (A0, misc/output_durability.md §9.)
            try:
                display = str(f.relative_to(scratch))
            except ValueError:
                display = f.name
            files.append({"url": None, "original_name": display,
                          "bytes": st.st_size, "link_only": True})
            warnings.append(
                f"File '{display}' is {st.st_size // (1024*1024)}MB — not "
                f"auto-copied (over the size cap) but still YOURS BY NAME: it "
                f"lives in the run sandbox, is retained durably when the run "
                f"settles, and find_files('{display.rsplit('/', 1)[-1]}') "
                f"locates it; opening fetches on demand."
            )
            continue
        suf = f.suffix.lower()
        if suf == ".pdf" and _pdf_page_count(f) == 1:
            _copy_and_record(f, plots, suf)
            # Annotate with a rasterized preview URL so chat-inline
            # rendering can <img src=...> something the browser can
            # actually display. The canonical url stays the PDF — that
            # remains what downloads / pin / "open original" operate on.
            # Without this annotation, the chat shows a broken-image
            # icon for PDF plots (regression 2026-06-07 after the
            # Phase 2 PDF-as-figure promotion).
            from core.exec.previews import ensure_preview
            try:
                pv = ensure_preview(plots[-1]["url"])
                if pv:
                    plots[-1]["preview_url"] = pv
            except Exception:  # noqa: BLE001 — preview is best-effort
                pass
            continue
        _copy_and_record(f, files, suf)

    # Off-convention captures: files the agent saved DIRECTLY into a known project
    # dir instead of the cell's working dir, so they never touch `scratch` and would
    # be orphaned — on disk but unregistered/unpinnable, eventually reaped. Catch
    # files written there DURING this exec (mtime in [since_ts, harvest-begin),
    # excluding our own copies) and register them through the same path. Gated on
    # since_ts (the session/persistent-kernel path) so the one-shot path — fresh
    # scratch, no start stamp — can't over-catch. NON-recursive so we don't re-walk
    # scratch or sibling threads' subdirs.
    def _capture_external(d: Path, note: str) -> None:
        for g in sorted(d.glob("*")):
            if not g.is_file() or g.name in _created:
                continue
            try:
                mt = g.stat().st_mtime
            except OSError:
                continue
            if not (since_ts <= mt < _harvest_begin):
                continue
            suf = g.suffix.lower()
            if suf == ".png":
                if _png_is_blank(g):
                    continue
                _copy_and_record(g, plots, suf)
            elif suf in (".csv", ".tsv"):
                _copy_and_record(g, tables, suf)
            elif suf in _FILE_EXTS:
                try:
                    if g.stat().st_size > _MAX_HARVEST_BYTES:
                        continue
                except OSError:
                    continue
                if suf == ".pdf" and _pdf_page_count(g) == 1:
                    _copy_and_record(g, plots, suf)
                else:
                    _copy_and_record(g, files, suf)
            else:
                continue
            warnings.append(note.format(name=g.name))

    if since_ts:
        # (A+B) the artifacts STORE dir, e.g. savefig('/artifacts/<pid>/tree.png').
        _capture_external(
            adir,
            "'{name}' was written into the artifacts dir directly; I captured + "
            "registered it so it's tracked + pinnable.")
        # (C4) the project WORK dir, e.g. savefig('<project>/work/fig.png') — the
        # PARENT of the per-thread exec cwd. The agent reasonably uses an absolute
        # path to the project work dir; without this its figures are orphaned even
        # though the cell returns rc=0 and the file is on disk. This was the "A2"
        # apparent fabrication: the agent CORRECTLY reports a save (rc=0 + its own
        # "figure saved" print) that the harvest then loses (plots:[]/produced=[]).
        try:
            from core.config import project_work_dir as _pwd
            wdir = _pwd(pid)
            if wdir.resolve() != scratch.resolve():   # else the main scan already covered it
                _capture_external(
                    wdir,
                    "'{name}' was saved to the project work dir (not the cell's working "
                    "dir); I captured + registered it so it's tracked + pinnable.")
        except Exception:  # noqa: BLE001 — work-dir capture is best-effort
            pass

    if _skipped_cap[0]:
        warnings.append(
            f"{_skipped_cap[0]} additional output file(s) were NOT copied to the artifact store "
            f"(hit the max_files={max_files} cap); they remain browsable in the run folder.")
    return plots, tables, files, warnings


def _pdf_page_count(pdf_path: Path) -> int:
    """Return the number of pages in `pdf_path`. Returns 0 if pypdfium2
    isn't available or the file isn't parseable as a PDF. Treats a
    parse failure as "not 1 page" so the file falls through to the
    files bucket (the harvester's safe default)."""
    try:
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
        doc = pdfium.PdfDocument(str(pdf_path))
        return len(doc)
    except Exception:  # noqa: BLE001
        return 0


def _png_is_blank(path: Path) -> bool:
    """True if a PNG is effectively one flat colour across the whole image — a
    matplotlib savefig of an empty/never-drawn canvas. Uses grayscale extrema:
    exact, cheap, and a real plot (axes/ticks/labels/marks) always spans a wide
    range. A figure with one empty panel among several is NOT flagged (the other
    panels give it range). Errs toward 'not blank' on any read error so a real
    figure is never dropped."""
    try:
        from PIL import Image
        with Image.open(path) as im:
            lo, hi = im.convert("L").getextrema()
        return (hi - lo) <= 4
    except Exception:  # noqa: BLE001 — never drop a real figure on a read hiccup
        return False
