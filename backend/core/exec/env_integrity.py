"""Environment-integrity primitives (env_refactor.md P0).

Closes the gap that let corrupt installs be reported "ready": the old check was
``PathFinder.find_spec`` (does a spec EXIST), not a real import. A package
compiled against the wrong numpy ABI, a half-written package, or one missing a
system lib all HAVE a spec but **fail to load** — the tensorflow/scipy incidents
(2026-06-23/24). These helpers actually import the thing, on the real runtime
sys.path, in a throwaway subprocess.

Ground truth stays on the filesystem; these just probe it honestly. Language-
symmetric: ``verify_python_imports`` mirrors run_python's path assembly,
``verify_r_library`` wraps the existing ``r_has_package`` library()-load check.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


def verify_python_imports(
    import_names: Sequence[str],
    *,
    extra_paths: Optional[Sequence[str]] = None,
    python_exe: Optional[str] = None,
    timeout_s: int = 180,
) -> tuple[bool, str]:
    """Actually import each name in a fresh subprocess on the runtime sys.path
    (base interpreter + the pylib overlay **appended**, matching the run_python
    preamble). Returns ``(ok, detail)``.

    ``ok=False`` means present-but-unloadable — ABI mismatch, partial install,
    missing system lib — i.e. the exact "find_spec says yes, import explodes"
    case. ``detail`` carries the traceback tail for the agent/operator.

    ``extra_paths`` overrides the overlay paths (default: ``pylib_paths()``); use
    it to verify against a temp install prefix before merging it (transactional
    installs). ``python_exe`` defaults to the base interpreter.
    """
    names = [n for n in (import_names or []) if n]
    if not names:
        return True, ""
    exe = python_exe or sys.executable
    if extra_paths is None:
        # Mirror run_python's sys.path: shared overlay THEN the current project's
        # overlay (env_refactor.md P1), so verify sees exactly what a run_python
        # cell would import.
        from core.exec.materialize import pylib_paths, project_pylib_paths
        from core import projects
        extra_paths = ([str(p) for p in pylib_paths()]
                       + [str(p) for p in project_pylib_paths(projects.current())])
    # append (not prepend) so the base wins, exactly like the run_python preamble
    appends = "".join(f"sys.path.append({str(p)!r})\n" for p in (extra_paths or []))
    names_lit = ", ".join(repr(n) for n in names)
    script = (
        "import sys\n"
        f"{appends}"
        "import importlib\n"
        f"for _n in [{names_lit}]:\n"
        "    importlib.import_module(_n)\n"
        "print('ABA_IMPORT_OK')\n"
    )
    try:
        proc = subprocess.run(
            [exe, "-c", script], capture_output=True, text=True, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        return False, f"import verification timed out after {timeout_s}s"
    except Exception as e:  # noqa: BLE001
        return False, f"could not launch import verification: {e}"
    if proc.returncode == 0 and "ABA_IMPORT_OK" in (proc.stdout or ""):
        return True, ""
    detail = ((proc.stderr or "") + (proc.stdout or "")).strip()
    return False, detail[-1400:]


def base_constraints_path() -> Path:
    """Where the cached base-constraints (pip-freeze pin of the install-wide
    base) lives — next to the overlay it guards."""
    from core.exec.materialize import ENVS_DIR
    return Path(ENVS_DIR) / "base-constraints.txt"


def ensure_base_constraints(*, force: bool = False,
                            python_exe: Optional[str] = None) -> Optional[Path]:
    """Generate (cached) a pip **constraints** file pinning the install-wide base
    so an overlay install can't move numpy / scipy / etc. or pull an
    incompatible-ABI wheel — it must satisfy the pin or fail loudly. The pin is
    ``pip freeze`` of the base interpreter, filtered to clean ``name==version``
    lines (drops editable / URL / VCS entries that are invalid as constraints).

    Returns the path, or ``None`` if it couldn't be produced — in which case the
    caller falls back to an unconstrained install with a logged warning (the
    guard is best-effort; never block a real install on constraint generation).
    Re-lock the base by calling with ``force=True`` after a deliberate base
    change (the env_refactor.md "re-solve + re-lock" operation)."""
    path = base_constraints_path()
    if path.exists() and not force:
        return path
    exe = python_exe or sys.executable
    try:
        proc = subprocess.run([exe, "-m", "pip", "freeze"],
                              capture_output=True, text=True, timeout=120)
    except Exception:  # noqa: BLE001
        return None
    if proc.returncode != 0:
        return None
    pin = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*==")
    lines = [ln for ln in (proc.stdout or "").splitlines() if pin.match(ln)]
    if not lines:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001
        return None
    return path


def verify_r_library(libname: str, project_id: Optional[str] = None) -> tuple[bool, str]:
    """R analog of verify_python_imports: does ``library(libname)`` actually
    load (in the project's .libPaths + the shared base)? Wraps the existing
    ``r_has_package`` (Rscript + ``library()``), so R already has real
    load-verification — this just gives it the same (ok, detail) shape."""
    if not libname:
        return True, ""
    try:
        from core.exec.r import r_has_package
        ok = bool(r_has_package(libname, project_id=project_id))
        return ok, "" if ok else f"library({libname}) does not load on .libPaths()"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:400]
