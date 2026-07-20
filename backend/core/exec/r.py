"""Typed R provisioning (r_provisioning.md).

R graduates from ad-hoc `subprocess(['Rscript', …])` to a first-class
provisioning layer with layered libraries via `.libPaths()`:

  - a curated conda **base** (r-base + toolchain + system libs + common
    packages), batch-solved, shared across projects;
  - per-project **native installs** (CRAN / Bioconductor / GitHub via
    install.packages / BiocManager / remotes) into a project-scoped library
    that stacks ahead of the base.

The persistent R kernel (the weft transport, kernels/weft.py) runs the same R,
with the project lib prepended to `.libPaths()`. Decision policy is mechanical:
in the library path → use it; else → project-native install. The shared base is
never mutated per request (curation only).
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Optional

from core import config

# Identifier validation so a package/ref string can't inject R code via `-e`.
_CRAN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._]*$")          # CRAN/Bioc package name
_GH_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")   # owner/repo
_REF_RE = re.compile(r"^[A-Za-z0-9_./-]+$")                 # tag / branch / sha
_CONDA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")     # conda package name (e.g. r-hdf5r)

def _version_tuple(v: str):
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", v or ""))


def version_ge(installed: Optional[str], required: str) -> bool:
    """installed >= required, comparing numeric components (R-style "1.0.7" vs
    "1.1.0"). Unknown `installed` (None) → False (treat as needs-install)."""
    if not installed:
        return False
    return _version_tuple(installed) >= _version_tuple(required)


_VER_REQ_RES = None


def parse_version_requirement(text: str) -> Optional[dict]:
    """Extract an unmet R version requirement from an install/load log so a
    failure is actionable. Handles e.g.
      • packageVersion("sccore") >= "1.1.0" is not TRUE
      • namespace 'sccore' 1.0.7 is already loaded, but >= 1.1.0 is required
      • package 'sccore' 1.0.7 was found, but >= 1.1.0 is required
    Returns {"package", "min_version"} or None."""
    global _VER_REQ_RES
    if not text:
        return None
    import re as _re
    if _VER_REQ_RES is None:
        Q = "['‘’\"]"            # straight + curly quotes
        _VER_REQ_RES = [
            _re.compile(rf'packageVersion\(\s*{Q}([\w.]+){Q}\s*\)\s*>=\s*{Q}([\d.]+){Q}\s+is not TRUE'),
            _re.compile(rf'namespace {Q}([\w.]+){Q}\s+[\d.]+\s+is already loaded,\s*but\s*>=\s*([\d.]+)\s+is required'),
            _re.compile(rf'package {Q}([\w.]+){Q}\s+[\d.]+\s+(?:was found|is loaded),?\s*but\s*>=\s*([\d.]+)\s+is required'),
            _re.compile(rf'{Q}([\w.]+){Q}\s*>=\s*{Q}?([\d.]+){Q}?\s+is not TRUE'),
        ]
    for rx in _VER_REQ_RES:
        m = rx.search(text)
        if m:
            return {"package": m.group(1), "min_version": m.group(2)}
    return None


def r_unload_namespace(pkg: str, thread_id: Optional[str]) -> bool:
    """Best-effort: unload `pkg` from the thread's running R kernel so a freshly
    reinstalled version loads on the next library() WITHOUT a full restart (the R
    analog of the pip cache-invalidation hook). False if no live R session.
    NB unloadNamespace fails when another LOADED namespace imports pkg — then the
    caller falls back to restart_kernel."""
    if not thread_id:
        return False
    try:
        from core.exec.kernels import get_pool
        sess = get_pool().peek(str(thread_id), "r")
        if sess is None:
            return False
        sess.execute(f'if (isNamespaceLoaded({pkg!r})) '
                     f'try(unloadNamespace({pkg!r}), silent=TRUE)', timeout_s=30)
        return True
    except Exception:  # noqa: BLE001
        return False


# Patterns that tell the agent (and the user) what actually went wrong, so a
# failed install is actionable rather than an opaque traceback.
# Strong FAILURE signals only. NOT "using C++ compiler" — that's R's benign
# per-file compile banner (printed dozens of times in a big build); keeping it
# drowned the real error (`hdf5r had non-zero exit status`) out of the window.
_ERR_MARKERS = (
    "there is no package called",
    "is not available",
    "had non-zero exit status",
    "dependenc",
    "cannot open shared object file",
    "unable to load shared object",
    "no such file or directory",
    "cannot find -l",
    "configuration failed",
    "compilation failed",
    "error:",            # "ERROR: …", "fatal error: hdf5.h: …", C/C++ compile errors
    "undefined reference",
    "installation of package",   # "installation of package 'X' had non-zero exit status"
)
# A missing *system* library — the case that may need conda (userspace) or, if
# root-only, the user. Capture the lib name where we can.
_SYSLIB_RE = re.compile(
    r"cannot find -l([A-Za-z0-9_.+-]+)"
    r"|([A-Za-z0-9_.+-]+\.h): No such file"
    r"|cannot open shared object file:[^\n]*?\b(lib[A-Za-z0-9_.+-]+\.so[0-9.]*)"
    r"|No package '([A-Za-z0-9_.+-]+)' found"               # pkg-config miss
    r"|\b([A-Za-z][A-Za-z0-9_.+-]{2,}) was not found"       # configure: 'libfoo was not found'
    r"|[Cc]annot find ([A-Za-z][A-Za-z0-9_.+-]{2,}) (?:library|headers?)"   # 'Cannot find X library'
    r"|could not find (?:your |the )?([A-Za-z][A-Za-z0-9_.+-]{2,})(?: installation)?",  # hdf5r: 'could not find your HDF5 installation'
    re.I,
)


# Map an R configure-error system-lib name → the conda package that provides it,
# so a failed install can self-heal (conda-install the lib, then retry). Default
# is the name itself when not listed.
_SYS_LIB_CONDA = {
    "xml2": "libxml2", "libxml-2.0": "libxml2", "libxml2": "libxml2",
    "curl": "libcurl", "libcurl": "libcurl", "openssl": "openssl", "ssl": "openssl",
    "fontconfig": "fontconfig", "freetype": "freetype", "freetype2": "freetype",
    "harfbuzz": "harfbuzz", "fribidi": "fribidi", "png": "libpng", "libpng": "libpng",
    "jpeg": "jpeg", "tiff": "libtiff", "z": "zlib", "zlib": "zlib", "bz2": "bzip2",
    "gsl": "gsl", "glpk": "glpk", "gmp": "gmp", "mpfr": "mpfr",
    "hdf5": "hdf5", "gdal": "gdal", "geos": "geos", "proj": "proj",
    "udunits2": "udunits2", "udunits": "udunits2", "magick": "imagemagick",
    "fftw": "fftw", "cairo": "cairo",
}


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
    env = config.settings.r_ppm_distro.get()
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
    base = (config.settings.r_ppm_base.get() or _PPM_BASE).rstrip("/")
    snap = config.settings.r_ppm_snapshot.get()
    distro = _ppm_distro()
    return f"{base}/__linux__/{distro}/{snap}" if distro else f"{base}/{snap}"


def _ppm_ua_expr() -> str:
    """R `options(HTTPUserAgent=...)` so PPM serves binaries (it keys binary vs
    source off the R version + platform in the UA). Harmless on a plain CRAN."""
    return ('options(HTTPUserAgent=sprintf("R/%s R (%s)", getRversion(), '
            'paste(getRversion(), R.version$platform, R.version$arch, R.version$os))); ')


def validate_install(source: str, package: str, ref: Optional[str]) -> Optional[str]:
    """Return an error string if the install spec is unsafe/malformed, else None."""
    if source not in ("cran", "bioconductor", "github", "conda"):
        return f"unknown R source {source!r} (cran|bioconductor|github|conda)"
    if source == "github":
        if not _GH_RE.match(package or ""):
            return (
                "github source requires the 'package' field to be "
                "'owner/repo' (the GitHub coordinate, e.g. "
                "'kharchenkolab/pagoda2'); use 'ref' for the branch / "
                "tag / commit (default 'main'). "
                f"Got package={package!r}."
            )
    elif source == "conda":
        # conda coordinate (conda-forge/bioconda binary, e.g. 'r-hdf5r') — these
        # legitimately contain hyphens, which the CRAN name rule rejects.
        if not _CONDA_RE.match(package or ""):
            return "conda package name has invalid characters"
    elif not _CRAN_RE.match(package or ""):
        return "package name has invalid characters"
    if ref and not _REF_RE.match(ref):
        return "ref has invalid characters"
    return None


