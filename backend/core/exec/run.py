"""Shared Python execution core (capdat_impl.md P5).

One implementation behind both the synchronous `run_python` tool and the
background job runner, so a backgrounded run inherits the same project scratch
workspace, the materialized-library overlay (pylib on sys.path), the conda
tools env (on PATH), killpg cancellation, and png/csv artifact harvest. Before
P5 the background path was an older parallel copy that saw none of P1–P4.

Domain-neutral: the only bio-specific input is `extra_syspath` (e.g. the
vendored biomni path), passed by the caller.
"""
from __future__ import annotations
import shutil
import sys
import uuid
from pathlib import Path
from typing import Optional, Sequence

from core.config import ARTIFACTS_DIR, DATA_DIR
from core.data.workspace import scratch_dir
from core.exec import MaterializingExecutor, Provisioning, pylib_dir


def run_python_code(
    code: str,
    *,
    project_id: str,
    run_id: Optional[str] = None,
    timeout_s: int = 90,
    cancel_token=None,
    extra_syspath: Optional[Sequence[str]] = None,
) -> dict:
    """Run `code` in the project's scratch workspace and return the run_python
    result shape ({stdout, stderr, returncode, plots, tables} | {error} |
    {status: cancelled}). Kept outputs are harvested to the artifact store; the
    on_post_tool / on_job_complete hook registers them as entities."""
    timeout_s = max(5, min(int(timeout_s or 90), 1800))
    run_id = run_id or uuid.uuid4().hex
    scratch = scratch_dir(str(project_id), str(run_id))

    # Preamble: DATA_DIR + caller extras (biomni) prepended; the pylib overlay
    # APPENDED so the .venv wins and the overlay only supplies what's missing.
    # DATA_DIR is per-project (post 2026-05-31 reorg).
    from core.config import current_project_id, project_data_dir
    _data_dir = project_data_dir(current_project_id())
    lines = [f"DATA_DIR = {str(_data_dir)!r}", "import sys as _sys"]
    for p in (extra_syspath or []):
        lines.append(f"_sys.path.insert(0, {str(p)!r})")
    lines.append(f"_sys.path.append({str(pylib_dir())!r})")
    (scratch / "script.py").write_text("\n".join(lines) + "\n" + code)

    ex = MaterializingExecutor()
    env = ex.materialize(Provisioning())          # base venv + tools-env PATH overlay
    result = ex.exec(
        env, [env.python or sys.executable, str(scratch / "script.py")],
        cwd=str(scratch), cancel_token=cancel_token, timeout_s=timeout_s,
    )

    if result.timed_out:
        return {"error": f"Code execution timed out ({timeout_s}s limit)"}
    if result.cancelled:
        return {"status": "cancelled",
                "note": f"Run was cancelled by the user "
                        f"({getattr(cancel_token, 'reason', '')}). No further work happened."}

    plots, tables, warns = harvest_artifacts(scratch)
    out = {
        "stdout": (result.stdout or "")[:4000],
        "stderr": (result.stderr or "")[:2000],
        "returncode": result.returncode,
        "plots": plots,
        "tables": tables,
        # Self-contained one-shot script — its producing_code reproduces it alone.
        "execution_mode": "stateless",
    }
    if warns:
        out["figure_warnings"] = warns
    return out


def harvest_artifacts(scratch: Path, since_ts: float = 0.0) -> tuple[list, list, list]:
    """Copy kept outputs (*.png/*.csv) from a working dir into the artifact
    store, returning (plots, tables, warnings). `since_ts` harvests only files
    created/modified at-or-after that time — needed for a PERSISTENT kernel cwd
    where earlier cells' files remain (avoids re-harvesting them). Copies (not
    moves) so the agent can still read its own output file in a later cell.

    BLANK figures are dropped, not shown. A PNG that is a single flat colour
    (matplotlib saved an empty/never-drawn canvas — empty AnnData, missing
    embedding, savefig-before-draw) carries no information; surfacing it as a
    white box in the chat misleads the user and the agent both. Such files are
    excluded from `plots` and reported in `warnings` so the agent learns the
    plot FAILED (point-of-use guardrail) instead of silently 'succeeding'."""
    scratch = Path(scratch)
    plots, tables, warnings = [], [], []
    for src, bucket, ext in (
        (scratch.glob("*.png"), plots, "png"),
        (scratch.glob("*.csv"), tables, "csv"),
    ):
        for f in src:
            try:
                if f.stat().st_mtime < since_ts:
                    continue
            except OSError:
                continue
            if ext == "png" and _png_is_blank(f):
                warnings.append(
                    f"Figure '{f.name}' came out BLANK (one flat colour — no data was "
                    f"drawn) and was dropped. The plot FAILED: check the data isn't empty "
                    f"(n_obs/rows > 0), the embedding/columns you coloured by exist, and "
                    f"you didn't savefig before plotting. Do NOT present it as a result."
                )
                continue
            dest_name = f"{uuid.uuid4().hex}.{ext}"
            # Project-scoped artifacts (post 2026-05-31 reorg): land in
            # projects/<pid>/artifacts/, served at /artifacts/<pid>/<name>.
            # Falls back to the workspace-level ARTIFACTS_DIR when no project
            # is active (background jobs without context).
            from core.config import current_project_id, project_artifacts_dir
            pid = current_project_id()
            adir = project_artifacts_dir(pid)
            shutil.copy2(str(f), str(adir / dest_name))
            bucket.append({"url": f"/artifacts/{pid}/{dest_name}", "original_name": f.name})
    return plots, tables, warnings


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
