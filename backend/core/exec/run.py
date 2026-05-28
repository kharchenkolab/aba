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

    plots = []
    for png in scratch.glob("*.png"):
        dest_name = f"{uuid.uuid4().hex}.png"
        shutil.move(str(png), str(ARTIFACTS_DIR / dest_name))
        plots.append({"url": f"/artifacts/{dest_name}", "original_name": png.name})
    tables = []
    for csv in scratch.glob("*.csv"):
        dest_name = f"{uuid.uuid4().hex}.csv"
        shutil.move(str(csv), str(ARTIFACTS_DIR / dest_name))
        tables.append({"url": f"/artifacts/{dest_name}", "original_name": csv.name})

    return {
        "stdout": (result.stdout or "")[:4000],
        "stderr": (result.stderr or "")[:2000],
        "returncode": result.returncode,
        "plots": plots,
        "tables": tables,
    }
