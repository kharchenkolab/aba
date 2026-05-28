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
    lines = [f"DATA_DIR = {str(DATA_DIR)!r}", "import sys as _sys"]
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

    plots, tables = harvest_artifacts(scratch)
    return {
        "stdout": (result.stdout or "")[:4000],
        "stderr": (result.stderr or "")[:2000],
        "returncode": result.returncode,
        "plots": plots,
        "tables": tables,
        # Self-contained one-shot script — its producing_code reproduces it alone.
        "execution_mode": "stateless",
    }


def harvest_artifacts(scratch: Path, since_ts: float = 0.0) -> tuple[list, list]:
    """Copy kept outputs (*.png/*.csv) from a working dir into the artifact
    store, returning (plots, tables). `since_ts` harvests only files
    created/modified at-or-after that time — needed for a PERSISTENT kernel cwd
    where earlier cells' files remain (avoids re-harvesting them). Copies (not
    moves) so the agent can still read its own output file in a later cell."""
    scratch = Path(scratch)
    plots, tables = [], []
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
            dest_name = f"{uuid.uuid4().hex}.{ext}"
            shutil.copy2(str(f), str(ARTIFACTS_DIR / dest_name))
            bucket.append({"url": f"/artifacts/{dest_name}", "original_name": f.name})
    return plots, tables
