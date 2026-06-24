"""Isolated environments (env_refactor.md P4) — the escape hatch + the agent's
troubleshooting sandbox.

When a requirement is UNSAT against the install-wide base (a conflicting numpy,
tensorflow, an ABI-incompatible wheel) — or the agent simply needs to resolve a
conflict its own way — it gets a FULL, independent environment it OWNS. Isolation
contains any mess to that env; the shared base is never touched. This is the
"graduated control by blast radius" tier: full control over what the agent
creates, guarded requests against shared state.

Engine: ``uv`` when available (hardlink cache → cheap create/install), else
stdlib ``venv`` + the env's own pip. The surface is engine-agnostic, so adopting
uv later is a pure speedup with no API change.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence


def _isolated_root() -> Path:
    from core.exec.materialize import ENVS_DIR
    return Path(ENVS_DIR) / "isolated"


def env_dir(name: str) -> Path:
    return _isolated_root() / str(name)


def env_python(name: str) -> Path:
    return env_dir(name) / "bin" / "python"


def uv_path() -> Optional[str]:
    """uv on PATH or beside the base interpreter — None if not installed (then we
    fall back to stdlib venv)."""
    found = shutil.which("uv")
    if found:
        return found
    cand = Path(sys.executable).parent / "uv"
    return str(cand) if cand.exists() else None


def engine() -> str:
    return "uv" if uv_path() else "venv"


def create_env(name: str, *, with_base_site_packages: bool = False,
               timeout_s: int = 300) -> dict:
    """Create (idempotently) an isolated env the agent owns. uv if available
    (fast), else stdlib venv. ``with_base_site_packages`` shares the base's
    packages (use only for the *additive* case — a conflict needs its own copy,
    so leave it False). Returns {name, python, engine, created}."""
    d = env_dir(name)
    if env_python(name).exists():
        return {"name": name, "python": str(env_python(name)), "created": False, "engine": "existing"}
    d.parent.mkdir(parents=True, exist_ok=True)
    uv = uv_path()
    if uv:
        cmd = [uv, "venv", str(d), "--python", sys.executable]
        if with_base_site_packages:
            cmd.append("--system-site-packages")
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
        if proc.returncode != 0:
            raise RuntimeError(f"uv venv failed: {(proc.stderr or proc.stdout)[-600:]}")
        eng = "uv"
    else:
        import venv as _venv
        _venv.create(d, with_pip=True, system_site_packages=with_base_site_packages)
        eng = "venv"
    return {"name": name, "python": str(env_python(name)), "created": True, "engine": eng}


def install_into(name: str, specs: Sequence[str], *, timeout_s: int = 1200,
                 verify_imports: Optional[Sequence[str]] = None) -> dict:
    """Install specs into the isolated env with FULL resolution control (any
    versions, conflicting with the base — that's the point). uv pip if uv, else
    the env's pip. Optionally import-verify afterwards (the same honesty as the
    overlay path). Returns {name, ok, installed, error, verified}."""
    if not env_python(name).exists():
        create_env(name)
    py = env_python(name)
    uv = uv_path()
    if uv:
        cmd = [uv, "pip", "install", "--python", str(py), *specs]
    else:
        cmd = [str(py), "-m", "pip", "install", *specs]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"name": name, "ok": False, "installed": list(specs),
                "error": f"install timed out after {timeout_s}s", "verified": None}
    ok = proc.returncode == 0
    out: dict = {"name": name, "ok": ok, "installed": list(specs),
                 "error": None if ok else (proc.stderr or proc.stdout or "")[-1400:],
                 "verified": None}
    if ok and verify_imports:
        v = run_in(name, "import " + ", ".join(verify_imports) + "\nprint('ABA_VERIFY_OK')")
        out["verified"] = ("ABA_VERIFY_OK" in (v.get("stdout") or ""))
        if not out["verified"]:
            out["ok"] = False
            out["error"] = "installed but import-verify failed:\n" + (v.get("stderr") or "")[-800:]
    return out


def run_in(name: str, code: str, *, timeout_s: int = 600) -> dict:
    """Run Python code inside the isolated env. Returns {ok, stdout, stderr}."""
    py = env_python(name)
    if not py.exists():
        return {"ok": False, "stdout": "", "stderr": f"isolated env {name!r} does not exist"}
    try:
        proc = subprocess.run([str(py), "-c", code], capture_output=True, text=True,
                              timeout=timeout_s)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stdout": "", "stderr": f"run timed out after {timeout_s}s"}
    return {"ok": proc.returncode == 0, "stdout": (proc.stdout or "")[-4000:],
            "stderr": (proc.stderr or "")[-2000:]}


def list_envs() -> list[str]:
    root = _isolated_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if (p / "bin" / "python").exists())


def remove_env(name: str) -> bool:
    d = env_dir(name)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
        return True
    return False


# ── R isolated environments (env_refactor.md P3) ─────────────────────────────
# R has no separate interpreter to isolate (one R, many library dirs), so an
# "isolated R env" is a standalone library dir installed into + run with that
# dir FIRST on .libPaths(). NOTE: R's *per-project* lib is already prepended to
# .libPaths() (libpaths_expr → project wins over base), so a project ALREADY
# overrides base package versions — the case-(i) escape hatch Python needed a
# venv for. These isolated R libs are for case-(ii)/symmetry: a fully separate,
# project-independent lib for a one-off conflicting install.

def r_env_lib(name: str) -> Path:
    return _isolated_root() / f"r-{name}"


def r_create_env(name: str) -> dict:
    lib = r_env_lib(name)
    created = not lib.exists()
    lib.mkdir(parents=True, exist_ok=True)
    return {"name": name, "lib": str(lib), "created": created,
            "engine": "r-libdir", "language": "r"}


def r_run_in(name: str, code: str, *, timeout_s: int = 600) -> dict:
    """Run R code with the isolated lib FIRST on .libPaths() (so its packages
    win), then the shared base. Returns {ok, stdout, stderr}."""
    lib = r_env_lib(name)
    if not lib.exists():
        return {"ok": False, "stdout": "", "stderr": f"isolated R env {name!r} does not exist"}
    from core.exec.r import _run_rscript
    expr = f".libPaths(c({str(lib)!r}, .libPaths()))\n{code}"
    proc = _run_rscript(expr, timeout_s)
    return {"ok": proc.returncode == 0,
            "stdout": (getattr(proc, "stdout", "") or "")[-4000:],
            "stderr": (getattr(proc, "stderr", "") or "")[-2000:]}


def r_install_into(name: str, packages: Sequence[str], *, timeout_s: int = 1800,
                   verify: bool = True) -> dict:
    """Install R packages into the isolated lib (binary via PPM where available).
    The isolated lib is the install target AND first on .libPaths(), so shared
    deps resolve from the base but the named packages live isolated. Returns
    {name, ok, installed, error, verified}."""
    lib = r_env_lib(name)
    lib.mkdir(parents=True, exist_ok=True)
    from core.exec.r import _run_rscript, cran_repo
    pkgs = "c(" + ", ".join(repr(p) for p in packages) + ")"
    expr = (f".libPaths(c({str(lib)!r}, .libPaths()))\n"
            f"install.packages({pkgs}, lib={str(lib)!r}, repos={cran_repo()!r})")
    proc = _run_rscript(expr, timeout_s)
    ok = proc.returncode == 0
    out: dict = {"name": name, "ok": ok, "installed": list(packages),
                 "error": None if ok else
                 ((getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "")[-1400:]),
                 "verified": None}
    if ok and verify:
        v = r_run_in(name, f"ok <- all(vapply({pkgs}, requireNamespace, logical(1), "
                           f"quietly=TRUE)); cat(if (ok) 'ABA_VERIFY_OK' else 'ABA_VERIFY_FAIL')")
        out["verified"] = "ABA_VERIFY_OK" in (v.get("stdout") or "")
        if not out["verified"]:
            out["ok"] = False
            out["error"] = "installed but library() verification failed:\n" + (v.get("stderr") or "")[-600:]
    return out


def remove_r_env(name: str) -> bool:
    lib = r_env_lib(name)
    if lib.exists():
        shutil.rmtree(lib, ignore_errors=True)
        return True
    return False
