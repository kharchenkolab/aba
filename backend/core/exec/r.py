"""Typed R provisioning (r_provisioning.md).

R graduates from ad-hoc `subprocess(['Rscript', …])` to a first-class
provisioning layer with layered libraries via `.libPaths()`:

  - a curated conda **base** (r-base + toolchain + system libs + common
    packages), batch-solved, shared across projects;
  - per-project **native installs** (CRAN / Bioconductor / GitHub via
    install.packages / BiocManager / remotes) into a project-scoped library
    that stacks ahead of the base.

The persistent IRkernel (kernels/jupyter.py) runs the same conda tools-env R,
with the project lib prepended to `.libPaths()`. Decision policy is mechanical:
in the library path → use it; else → project-native install. The shared base is
never mutated per request (curation only).
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

from core.config import ENVS_DIR
from core.exec.materialize import tools_env

# Per-project R libraries — wipeable, under the materialized-tools area.
R_LIBS_ROOT = ENVS_DIR / "r_libs"

# Minimum conda runtime to run R + install from any source (CRAN source compiles
# need the toolchain; GitHub needs remotes; Bioconductor needs BiocManager).
RUNTIME_SPECS = ["r-base", "r-remotes", "r-biocmanager", "compilers", "make", "pkg-config"]

# Identifier validation so a package/ref string can't inject R code via `-e`.
_CRAN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._]*$")          # CRAN/Bioc package name
_GH_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")   # owner/repo
_REF_RE = re.compile(r"^[A-Za-z0-9_./-]+$")                 # tag / branch / sha


def _rscript() -> Path:
    return tools_env() / "bin" / "Rscript"


def r_runtime_ready() -> bool:
    """True if the conda R interpreter is present (cheap path check)."""
    return _rscript().exists()


def project_r_lib(project_id: Optional[str]) -> Path:
    p = R_LIBS_ROOT / str(project_id or "default")
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_r_runtime() -> None:
    """Materialize the minimum conda R runtime (r-base + toolchain + remotes +
    BiocManager) into the shared tools env. One batch solve; idempotent."""
    from core.exec.mamba import run_micromamba, installed_packages
    tenv = tools_env()
    have = installed_packages(tenv)
    missing = [s for s in RUNTIME_SPECS if s not in have]
    if not missing:
        return
    verb = "install" if (tenv / "conda-meta").exists() else "create"
    run_micromamba([verb, "-y", "-p", str(tenv), "-c", "conda-forge", "-c", "bioconda",
                    *RUNTIME_SPECS])


def ensure_r_base(specs: list[str]) -> None:
    """Batch-install a curated set of R packages into the shared base (one solve,
    prioritizing bioconda/conda-forge binaries). Heavy; on-demand/explicit, NOT
    per request. `specs` are conda package names (e.g. 'bioconductor-deseq2')."""
    from core.exec.mamba import run_micromamba, installed_packages
    ensure_r_runtime()
    tenv = tools_env()
    have = installed_packages(tenv)
    todo = [s for s in specs if re.split(r"[=<>!]", s)[0] not in have]
    if not todo:
        return
    run_micromamba(["install", "-y", "-p", str(tenv), "-c", "conda-forge", "-c", "bioconda",
                    *todo])


def libpaths_expr(project_id: Optional[str]) -> str:
    """R expression putting the project lib ahead of the base on .libPaths()."""
    if not project_id:
        return ""
    return f'.libPaths(c({str(project_r_lib(project_id))!r}, .libPaths()))'


def r_has_package(pkg: str, project_id: Optional[str] = None, timeout_s: int = 60) -> bool:
    """True if `pkg` is loadable on the (project + base) library paths."""
    if not _CRAN_RE.match(pkg or "") or not r_runtime_ready():
        return False
    setlib = libpaths_expr(project_id)
    pre = (setlib + "; ") if setlib else ""
    expr = f'{pre}quit(status=if (requireNamespace({pkg!r}, quietly=TRUE)) 0L else 1L)'
    from core.exec.mamba import run_micromamba
    try:
        proc = run_micromamba(["run", "-p", str(tools_env()), "Rscript", "-e", expr],
                              timeout_s=timeout_s, check=False)
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def validate_install(source: str, package: str, ref: Optional[str]) -> Optional[str]:
    """Return an error string if the install spec is unsafe/malformed, else None."""
    if source not in ("cran", "bioconductor", "github"):
        return f"unknown R source {source!r} (cran|bioconductor|github)"
    if source == "github":
        if not _GH_RE.match(package or ""):
            return "github package must be 'owner/repo'"
    elif not _CRAN_RE.match(package or ""):
        return "package name has invalid characters"
    if ref and not _REF_RE.match(ref):
        return "ref has invalid characters"
    return None


def install_command(source: str, package: str, *, lib: str,
                    ref: Optional[str] = None, repos: Optional[str] = None) -> str:
    """Pure builder: the R expression to install `package` from `source` into
    `lib` (project library), prepended ahead of the base on .libPaths()."""
    libq = repr(str(lib))
    setlib = f'.libPaths(c({libq}, .libPaths())); '
    if source == "github":
        spec = f"{package}@{ref}" if ref else package
        return f'{setlib}remotes::install_github({spec!r}, lib={libq}, upgrade="never")'
    if source == "bioconductor":
        return f'{setlib}BiocManager::install({package!r}, lib={libq}, update=FALSE, ask=FALSE)'
    repos = repos or "https://cloud.r-project.org"
    return f'{setlib}install.packages({package!r}, lib={libq}, repos={repos!r})'


def r_install(source: str, package: str, *, project_id: str,
              ref: Optional[str] = None, timeout_s: int = 1800) -> dict:
    """Native install (CRAN / Bioconductor / GitHub) into the project R library.
    Runs through `micromamba run` so the conda env (toolchain, R_HOME, libs) is
    active for any source compilation."""
    err = validate_install(source, package, ref)
    if err:
        return {"status": "error", "note": err}
    ensure_r_runtime()
    lib = project_r_lib(project_id)
    expr = install_command(source, package, lib=str(lib), ref=ref)
    from core.exec.mamba import run_micromamba
    proc = run_micromamba(["run", "-p", str(tools_env()), "Rscript", "-e", expr],
                          timeout_s=timeout_s, check=False)
    ok = proc.returncode == 0
    return {
        "status": "ready" if ok else "error",
        "package": package, "source": source, "lib": str(lib),
        "returncode": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-2000:],
    }
