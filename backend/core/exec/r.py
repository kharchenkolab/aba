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


def _run_rscript(expr: str, timeout_s: int):
    """Run an R expression in the conda tools-env R via `micromamba run` (so the
    env's toolchain / R_HOME / libs are active). Returns the CompletedProcess
    (never raises on non-zero — callers inspect returncode)."""
    from core.exec.mamba import run_micromamba
    return run_micromamba(["run", "-p", str(tools_env()), "Rscript", "-e", expr],
                          timeout_s=timeout_s, check=False)


def r_has_package(pkg: str, project_id: Optional[str] = None, timeout_s: int = 60) -> bool:
    """True if `pkg` is *loadable* (requireNamespace dlopens it) on the
    (project + base) library paths — a real load check, not just file presence."""
    if not _CRAN_RE.match(pkg or "") or not r_runtime_ready():
        return False
    setlib = libpaths_expr(project_id)
    pre = (setlib + "; ") if setlib else ""
    expr = f'{pre}quit(status=if (requireNamespace({pkg!r}, quietly=TRUE)) 0L else 1L)'
    try:
        return _run_rscript(expr, timeout_s).returncode == 0
    except Exception:  # noqa: BLE001
        return False


# Patterns that tell the agent (and the user) what actually went wrong, so a
# failed install is actionable rather than an opaque traceback.
_ERR_MARKERS = (
    "there is no package called",
    "is not available",
    "had non-zero exit status",
    "dependenc",
    "cannot open shared object file",
    "unable to load shared object",
    "No such file or directory",
    "cannot find -l",
    "configuration failed",
    "C++ compiler",
    "compilation failed",
)
# A missing *system* library — the case that may need conda (userspace) or, if
# root-only, the user. Capture the lib name where we can.
_SYSLIB_RE = re.compile(
    r"cannot find -l([A-Za-z0-9_.+-]+)"
    r"|([A-Za-z0-9_.+-]+\.h): No such file"
    r"|cannot open shared object file:[^\n]*?\b(lib[A-Za-z0-9_.+-]+\.so[0-9.]*)",
    re.I,
)


def diagnose_install(text: str) -> dict:
    """Pull the actionable lines out of an R install/build log, and flag a
    likely missing system library. Returns {lines, missing_lib?}."""
    text = text or ""
    keep = [ln.strip() for ln in text.splitlines()
            if any(m.lower() in ln.lower() for m in _ERR_MARKERS)]
    out: dict = {"lines": "\n".join(keep[-8:])[:800]}
    m = _SYSLIB_RE.search(text)
    if m:
        out["missing_lib"] = next((g for g in m.groups() if g), None)
    return out


def r_install(source: str, package: str, *, project_id: str, library: Optional[str] = None,
              ref: Optional[str] = None, timeout_s: int = 1800) -> dict:
    """Native install (CRAN / Bioconductor / GitHub) into the project R library,
    then **verify it loads**. For CRAN/Bioc, a binary that installs but won't
    load under conda R (or an install that fails) triggers one automatic
    **source** retry (compiled via the conda toolchain → consistent ABI/libs).
    On genuine failure, returns an actionable diagnostic (incl. a missing
    system-library hint) rather than an opaque log."""
    err = validate_install(source, package, ref)
    if err:
        return {"status": "error", "note": err}
    ensure_r_runtime()
    lib = project_r_lib(project_id)
    libname = library or (package.split("/")[-1] if source == "github" else package)

    def _attempt(force_source: bool):
        expr = install_command(source, package, lib=str(lib), ref=ref, force_source=force_source)
        proc = _run_rscript(expr, timeout_s)
        loaded = proc.returncode == 0 and r_has_package(libname, project_id=project_id)
        return proc, loaded

    proc, loaded = _attempt(force_source=False)
    used_source_fallback = False
    if not loaded and source in ("cran", "bioconductor"):
        # Binary missing/failed, or installed-but-won't-load → recompile from source.
        used_source_fallback = True
        proc, loaded = _attempt(force_source=True)

    if loaded:
        return {"status": "ready", "package": package, "source": source, "lib": str(lib),
                "library": libname, "source_fallback": used_source_fallback}

    log = ((proc.stderr or "") + "\n" + (proc.stdout or ""))
    diag = diagnose_install(log)
    note = f"R install of {package!r} ({source}) failed"
    if used_source_fallback:
        note += " (binary + source both failed)"
    note += "."
    if diag.get("missing_lib"):
        note += (f" Looks like a missing system library '{diag['missing_lib']}': install it "
                 f"via conda (propose_capability + ensure_capability) and retry, or — if it's "
                 f"root-only — ask the user.")
    return {"status": "error", "package": package, "source": source,
            "returncode": proc.returncode, "diagnostic": diag.get("lines"),
            "missing_lib": diag.get("missing_lib"),
            "stderr": (proc.stderr or "")[-1500:], "note": note}


# Posit Package Manager (PPM): binary CRAN/Bioc packages (fast) with automatic
# source fallback. Works with conda R via repo selection — R's compiled-package
# ABI is stable per minor version, so a binary built for R x.y loads in any R
# x.y; the only risk is exotic system-lib linkage, where PPM falls back to
# source (which compiles via the conda toolchain). Reproducibility = pin
# ABA_R_PPM_SNAPSHOT to a date instead of 'latest'.
_PPM_BASE = "https://packagemanager.posit.co/cran"
# Distros PPM ships Linux binaries for (others → source-only).
_PPM_DISTROS = {"focal", "jammy", "noble", "bullseye", "bookworm",
                "rhel8", "rhel9", "opensuse154", "opensuse155", "centos7"}


def _ppm_distro() -> str:
    """Linux distro codename for PPM binaries; '' → source-only snapshot.
    Explicit ABA_R_PPM_DISTRO wins (empty disables binaries); else auto-detect
    from /etc/os-release, but only if it's a PPM-supported distro."""
    import os
    env = os.environ.get("ABA_R_PPM_DISTRO")
    if env is not None:
        return env.strip()
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("VERSION_CODENAME="):
                code = line.split("=", 1)[1].strip().strip('"')
                return code if code in _PPM_DISTROS else ""
    except Exception:  # noqa: BLE001
        pass
    return ""


def cran_repo() -> str:
    """The CRAN repo URL for installs — a PPM snapshot (binary-enabled when on a
    supported distro, else source). Override via ABA_R_PPM_BASE/SNAPSHOT/DISTRO."""
    import os
    base = os.environ.get("ABA_R_PPM_BASE", _PPM_BASE).rstrip("/")
    snap = os.environ.get("ABA_R_PPM_SNAPSHOT", "latest")
    distro = _ppm_distro()
    return f"{base}/__linux__/{distro}/{snap}" if distro else f"{base}/{snap}"


def _ppm_ua_expr() -> str:
    """R `options(HTTPUserAgent=...)` so PPM serves binaries (it keys binary vs
    source off the R version + platform in the UA). Harmless on a plain CRAN."""
    return ('options(HTTPUserAgent=sprintf("R/%s R (%s)", getRversion(), '
            'paste(getRversion(), R.version$platform, R.version$arch, R.version$os))); ')


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


def install_command(source: str, package: str, *, lib: str, ref: Optional[str] = None,
                    repos: Optional[str] = None, force_source: bool = False) -> str:
    """Pure builder: the R expression to install `package` from `source` into
    `lib` (project library), prepended ahead of the base on .libPaths(). CRAN/
    Bioc installs set the PPM User-Agent so binaries are served when available
    (source otherwise); `force_source=True` requests source explicitly (the
    fallback when a binary won't load under conda R). GitHub always builds from
    source."""
    libq = repr(str(lib))
    setlib = f'.libPaths(c({libq}, .libPaths())); '
    if source == "github":
        spec = f"{package}@{ref}" if ref else package
        return f'{setlib}remotes::install_github({spec!r}, lib={libq}, upgrade="never")'
    ua = _ppm_ua_expr()
    typ = ', type="source"' if force_source else ''
    if source == "bioconductor":
        # PPM also serves Bioc; the UA gets binaries where available, else source.
        return f'{setlib}{ua}BiocManager::install({package!r}, lib={libq}, update=FALSE, ask=FALSE{typ})'
    repos = repos or cran_repo()
    return f'{setlib}{ua}install.packages({package!r}, lib={libq}, repos={repos!r}{typ})'


