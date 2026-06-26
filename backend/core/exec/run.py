"""Shared Python execution core (capdat_impl.md P5).

One implementation behind both the synchronous `run_python` tool and the
background job runner, so a backgrounded run inherits the same project scratch
workspace, the materialized-library overlay (pylib on sys.path), the conda
tools env (on PATH), killpg cancellation, and png/csv artifact harvest. Before
P5 the background path was an older parallel copy that saw none of P1–P4.
"""
from __future__ import annotations
import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional

from core.config import ARTIFACTS_DIR, DATA_DIR
from core.data.workspace import scratch_dir
from core.exec import MaterializingExecutor, Provisioning
from core.exec.materialize import project_pylib_paths


def run_python_code(
    code: str,
    *,
    project_id: str,
    run_id: Optional[str] = None,
    timeout_s: int = 90,
    cancel_token=None,
    env: Optional[str] = None,
) -> dict:
    """Run `code` in the project's scratch workspace and return the run_python
    result shape ({stdout, stderr, returncode, plots, tables} | {error} |
    {status: cancelled}). Kept outputs are harvested to the artifact store; the
    on_post_tool / on_job_complete hook registers them as entities."""
    timeout_s = max(5, min(int(timeout_s or 90), 1800))
    run_id = run_id or uuid.uuid4().hex
    scratch = scratch_dir(str(project_id), str(run_id))

    # §11: an ISOLATED env (run_python(env=…, background=True)) runs STANDALONE —
    # its OWN python, no project overlay — matching the interactive isolated-env
    # kernel. bind the project so ensure_env_built / env_python resolve THIS
    # project's env (the background worker doesn't pin a project, and a Slurm node
    # shares the FS + lock so it can use or rebuild the env).
    interp: Optional[str] = None
    if env:
        from core.exec import isolated_env as iso
        from core import projects as _projects
        try:
            with _projects.bind(str(project_id)):
                iso.ensure_env_built(env)       # rebuild from lock if a GC reclaimed it
        except Exception:  # noqa: BLE001
            pass
        py = iso.env_python(env, str(project_id))
        if not py.exists():
            return {"error": f"isolated env {env!r} is not available (project {project_id})."}
        interp = str(py)

    # Preamble: DATA_DIR prepended; for the DEFAULT env the pylib overlay is
    # appended (the .venv wins, overlay fills gaps). An isolated env is
    # standalone — skip the overlay so its own site-packages are authoritative.
    from core.config import current_project_id, project_data_dir
    _pid = str(project_id) if env else current_project_id()
    _data_dir = project_data_dir(_pid)
    lines = [f"DATA_DIR = {str(_data_dir)!r}", "import sys as _sys"]
    if not env:
        # §11.4: project overlay PREPENDED (project wins); shared overlay folded into base.
        for _p in reversed(list(project_pylib_paths(_pid))):
            lines.append(f"_sys.path.insert(0, {str(_p)!r})")
    (scratch / "script.py").write_text("\n".join(lines) + "\n" + code)

    ex = MaterializingExecutor()
    menv = ex.materialize(Provisioning())         # base venv + tools-env PATH overlay
    # Env-var parity with the interactive kernel (jupyter.py _kernel_env):
    # the agent's code routinely reads WORK_DIR / DATA_DIR / ARTIFACTS_DIR via
    # `os.environ[...]` since they're set up that way for run_python (kernel
    # path). Background jobs run through the SAME script the agent writes, so
    # the same env shape must be present — otherwise a backgrounded download
    # crashes with KeyError: 'WORK_DIR' before doing any work (live, 2026-06-03,
    # prj_413593e1 job_53df2f2734). MPLBACKEND=Agg keeps matplotlib headless.
    env_vars = {
        "WORK_DIR": str(scratch),
        "DATA_DIR": str(_data_dir),
        "ARTIFACTS_DIR": str(ARTIFACTS_DIR),
        "MPLBACKEND": "Agg",
    }
    result = ex.exec(
        menv, [interp or menv.python or sys.executable, str(scratch / "script.py")],
        cwd=str(scratch), cancel_token=cancel_token, timeout_s=timeout_s,
        env_vars=env_vars,
    )

    if result.timed_out:
        return {"error": f"Code execution timed out ({timeout_s}s limit)"}
    if result.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}

    plots, tables, files, warns = harvest_artifacts(scratch)
    from core.exec.output_cap import snip_middle
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

    from core.config import current_project_id, project_data_dir
    from core.exec.r import _rscript, libpaths_expr
    _data_dir = project_data_dir(current_project_id())

    rscript = _rscript()
    if not rscript.exists():
        return {"error": "Rscript not provisioned. Run ensure_r_runtime() first."}

    # R preamble — kept short. The agent's own script.R follows verbatim.
    preamble_lines = []
    if env:
        # §11: isolated R env — its lib dir FIRST on .libPaths(), then the base
        # (standalone, NOT the project lib), matching iso.r_run_in. The libdir is
        # project-scoped on the shared FS, so a Slurm compute node sees it.
        from core.exec import isolated_env as iso
        lib = iso.r_env_lib(env, str(project_id))
        if not lib.exists():
            return {"error": f"isolated R env {env!r} is not available (project {project_id})."}
        preamble_lines.append(f'.libPaths(c({str(lib)!r}, .libPaths()))')
    else:
        lib_expr = libpaths_expr(str(project_id))
        if lib_expr:
            preamble_lines.append(lib_expr)
    preamble_lines.append(f'setwd({str(scratch)!r})')
    preamble = "\n".join(preamble_lines)
    (scratch / "script.R").write_text(preamble + "\n" + code)

    ex = MaterializingExecutor()
    env = ex.materialize(Provisioning())
    env_vars = {
        "WORK_DIR": str(scratch),
        "DATA_DIR": str(_data_dir),
        "ARTIFACTS_DIR": str(ARTIFACTS_DIR),
        "ABA_PYTHON": sys.executable,  # let R shell out to Python if needed
    }
    result = ex.exec(
        env, [str(rscript), "--vanilla", str(scratch / "script.R")],
        cwd=str(scratch), cancel_token=cancel_token, timeout_s=timeout_s,
        env_vars=env_vars,
    )

    if result.timed_out:
        return {"error": f"R execution timed out ({timeout_s}s limit)"}
    if result.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}

    plots, tables, files, warns = harvest_artifacts(scratch)
    from core.exec.output_cap import snip_middle
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
    suff = tuple(s.lower() for s in suffixes)
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
        if f.suffix.lower() not in suff:
            continue
        try:
            if f.stat().st_mtime < since_ts:
                continue
        except OSError:
            continue
        yield f


def harvest_artifacts(scratch: Path, since_ts: float = 0.0
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

    BLANK figures are dropped, not shown. A PNG that is a single flat colour
    (matplotlib saved an empty/never-drawn canvas) carries no information;
    surfacing it as a white box misleads the user and the agent both. Such
    files are excluded from `plots` and reported in `warnings` so the agent
    learns the plot FAILED (point-of-use guardrail)."""
    scratch = Path(scratch)
    plots, tables, files, warnings = [], [], [], []
    from core.config import current_project_id, project_artifacts_dir
    pid = current_project_id()
    adir = project_artifacts_dir(pid)

    def _copy_and_record(f: Path, bucket: list, ext: str) -> None:
        dest_name = f"{uuid.uuid4().hex}{ext}"
        shutil.copy2(str(f), str(adir / dest_name))
        # original_name preserves the subdir context so the agent knows
        # WHERE the file lived (e.g. 'pagoda2_GSM.../qc_violin.png'), not
        # just the bare leaf — useful when multiple subdirs each have
        # qc_violin.png and the agent needs to distinguish them.
        try:
            display = str(f.relative_to(scratch))
        except ValueError:
            display = f.name
        bucket.append({"url": f"/artifacts/{pid}/{dest_name}",
                       "original_name": display})

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
            warnings.append(
                f"File '{f.name}' is {st.st_size // (1024*1024)}MB — too "
                f"large to auto-copy; it's still on disk in WORK_DIR but "
                f"won't be linkable from chat."
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
