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

from core.config import ENVS_DIR, _LazyDir
from core.exec.materialize import tools_env

# Per-project R libraries — wipeable, under the materialized-tools area.
R_LIBS_ROOT = _LazyDir(lambda: ENVS_DIR / "r_libs")

# Minimum conda runtime to run R + install from any source (CRAN source compiles
# need the toolchain; GitHub needs remotes; Bioconductor needs BiocManager).
# r-base is PINNED to the SAME minor as install/core/r-environment.yml (the r-bio
# module). Both conda paths write the same tools env; if this were unpinned it would
# resolve the latest R (e.g. 4.5) while the module build pins 4.4, so the env's R
# would flip-flop between provisioning calls and orphan every package compiled against
# the other minor (the dotCall64 _MAYBE_SHARED ABI mismatch, 2026-07-12). Keep in
# lock-step with r-environment.yml's `r-base=4.4.*`.
R_BASE_PIN = "r-base=4.4.*"
RUNTIME_SPECS = [R_BASE_PIN, "r-remotes", "r-biocmanager", "compilers", "make", "pkg-config"]

# Foundational compiled R deps + system libs that most bioinformatics packages
# share. Kept in the runtime as conda BINARIES so installs find them on
# .libPaths() instead of source-compiling (igraph is slow + needs GLPK; irlba/
# Rcpp* are slow) or failing on a missing libxml2 (the gap that blocked
# pagoda2). Heavy frameworks (Seurat, tidyverse, DESeq2) stay in the on-demand
# curated base (r_base.yaml), NOT here.
R_CORE_DEPS = ["r-matrix", "r-rcpp", "r-rcpparmadillo", "r-rcppeigen", "r-rcppprogress",
               "r-igraph", "r-irlba", "r-xml2", "libxml2",
               # ggrepel (+ its ggplot2 dep) — non-overlapping point/text labels for
               # volcano/marker plots; the R analog of Python adjustText. Common enough
               # across DE/single-cell plotting to keep ready as a binary.
               "r-ggrepel",
               # biomaRt — ubiquitous for Ensembl ID↔symbol mapping / gene annotation.
               # Pre-installed as the conda Bioconductor BINARY: an on-demand source
               # compile pulls Biostrings/XVector/png and reliably blew the run_r 120s
               # timeout (see session thr_78efae37). r-binary-channels.
               "bioconductor-biomart",
               # KernSmooth — R "Recommended" pkg conda r-base omits; smoothScatter()
               # needs it, so pagoda2 adjustVariance(plot=TRUE) errors without it.
               "r-kernsmooth",
               # dplyr — the %>% pipe + group_by/top_n the agent reaches for in
               # marker post-processing (and tidyverse, which isn't installed); keep
               # it ready so `library(dplyr)` always works. ggplot2 is already present
               # via r-ggrepel. Together they cover the common Seurat plotting/wrangling.
               "r-dplyr"]

# Identifier validation so a package/ref string can't inject R code via `-e`.
_CRAN_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._]*$")          # CRAN/Bioc package name
_GH_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")   # owner/repo
_REF_RE = re.compile(r"^[A-Za-z0-9_./-]+$")                 # tag / branch / sha
_CONDA_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")     # conda package name (e.g. r-hdf5r)

# "ERROR/Warning: dependency 'X' is not available for package 'Y'" — a CRAN
# install can hard-depend on a Bioconductor package the CRAN/PPM repo lacks.
_MISSING_DEP_RE = re.compile(r"dependency [‘'\"]([A-Za-z][A-Za-z0-9.]+)[’'\"] is not available")


def _missing_r_deps(log: str) -> list[str]:
    """Unavailable R-package dependency names parsed from an install log (deduped)."""
    return list(dict.fromkeys(_MISSING_DEP_RE.findall(log or "")))


def _rscript() -> Path:
    return tools_env() / "bin" / "Rscript"


def r_runtime_ready() -> bool:
    """True if the conda R interpreter is present (cheap path check)."""
    return _rscript().exists()


_r_runtime_tag_cache: Optional[str] = None


def _r_runtime_tag() -> Optional[str]:
    """`R-<major>.<minor>-<arch>` for the tools-env R (e.g. R-4.4-aarch64), or None if
    it can't be determined yet. Cached — one cheap Rscript call. Used to SCOPE the
    project library so packages built for one R minor/arch are never on .libPaths()
    of a different one (the ABI-mismatch guard)."""
    global _r_runtime_tag_cache
    if _r_runtime_tag_cache is not None:
        return _r_runtime_tag_cache or None
    try:
        proc = _run_rscript(
            "v<-R.version; cat(v$major, strsplit(v$minor,'.',fixed=TRUE)[[1]][1], v$arch, sep='|')", 30)
        parts = (proc.stdout or "").strip().split("|")
        if proc.returncode == 0 and len(parts) == 3 and parts[0].isdigit():
            _r_runtime_tag_cache = f"R-{parts[0]}.{parts[1]}-{parts[2]}"
            return _r_runtime_tag_cache
    except Exception:  # noqa: BLE001
        pass
    _r_runtime_tag_cache = ""     # cache the failure so we don't re-probe every call
    return None


def project_r_lib(project_id: Optional[str]) -> Path:
    """Project-scoped R library, keyed by the tools-env R version+arch. A package built
    for R 4.4/aarch64 lives under a different dir than one for 4.5, so a runtime change
    (or a leftover from a prior minor) can NEVER be loaded into a mismatched R — it just
    isn't on that R's path. Falls back to the flat path only when the R version is
    undeterminable (R not built yet)."""
    base = R_LIBS_ROOT / str(project_id or "default")
    tag = _r_runtime_tag()
    p = (base / tag) if tag else base
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_r_runtime(cancel_token=None) -> None:
    """Materialize the minimum conda R runtime (r-base + toolchain + remotes +
    BiocManager) into the shared tools env. One batch solve; idempotent."""
    from core.exec.mamba import run_micromamba, installed_packages
    tenv = tools_env()
    have = installed_packages(tenv)
    specs = RUNTIME_SPECS + R_CORE_DEPS
    missing = [s for s in specs if s not in have]
    if not missing:
        return
    from core.runtime import progress
    progress.emit("conda: building R runtime (r-base + toolchain + core bio deps: "
                  "igraph/irlba/Rcpp*/xml2)…", phase="conda")
    verb = "install" if (tenv / "conda-meta").exists() else "create"
    run_micromamba([verb, "-y", "-p", str(tenv), "-c", "conda-forge", "-c", "bioconda",
                    *specs], cancel_token=cancel_token)


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


def _run_rscript(expr: str, timeout_s: int, cancel_token=None):
    """Run an R expression in the conda tools-env R via `micromamba run` (so the
    env's toolchain / R_HOME / libs are active). Returns the CompletedProcess
    (never raises on non-zero — callers inspect returncode). cancel_token makes
    a long install/compile abortable by Stop."""
    from core.exec.mamba import run_micromamba
    return run_micromamba(["run", "-p", str(tools_env()), "Rscript", "-e", expr],
                          timeout_s=timeout_s, check=False, cancel_token=cancel_token)


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


def _version_tuple(v: str):
    import re as _re
    return tuple(int(x) for x in _re.findall(r"\d+", v or ""))


def version_ge(installed: Optional[str], required: str) -> bool:
    """installed >= required, comparing numeric components (R-style "1.0.7" vs
    "1.1.0"). Unknown `installed` (None) → False (treat as needs-install)."""
    if not installed:
        return False
    return _version_tuple(installed) >= _version_tuple(required)


def r_package_version(pkg: str, project_id: Optional[str] = None,
                      timeout_s: int = 60) -> Optional[str]:
    """Installed version of `pkg` on the (project + base) library paths, or None
    if absent. Lets ensure_capability be version-aware, not presence-only."""
    if not _CRAN_RE.match(pkg or "") or not r_runtime_ready():
        return None
    import re as _re
    setlib = libpaths_expr(project_id)
    pre = (setlib + "; ") if setlib else ""
    expr = (f'{pre}v <- tryCatch(as.character(utils::packageVersion({pkg!r})), '
            f'error=function(e) ""); cat("ABA_VER=", v, "\\n", sep="")')
    try:
        out = _run_rscript(expr, timeout_s)
        m = _re.search(r"ABA_VER=(\S+)", out.stdout or "")
        return m.group(1) if (m and m.group(1)) else None
    except Exception:  # noqa: BLE001
        return None


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


def diagnose_install(text: str) -> dict:
    """Pull the actionable lines out of an R install/build log, and flag a
    likely missing system library. Returns {lines, missing_lib?}."""
    text = text or ""
    lines = text.splitlines()
    keep: list[str] = []
    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            continue
        low = s.lower()
        if low.startswith("error") or any(m in low for m in _ERR_MARKERS):
            keep.append(s)
            # R prints the actionable CAUSE on the indented line(s) right AFTER an
            # error HEADER (a line ending in ':', e.g. "Error in loadNamespace(…) :")
            # — like "namespace 'sccore' 1.0.7 is being loaded, but >= 1.1.0 is
            # required". Those match no marker, so grab the continuation or the WHY
            # is lost. Only after a header (':') so we don't slurp trailing banner.
            if s.endswith(":"):
                for j in range(i + 1, min(i + 3, len(lines))):
                    d = lines[j].strip()
                    if not d or d.lower().startswith(("error", "calls:", "execution halted",
                                                      "*", "warning", "in addition")):
                        break
                    keep.append(d)
    # Dedup (compile logs repeat lines) so the few REAL lines survive; keep the tail.
    seen: set = set()
    uniq: list[str] = []
    for ln in keep:
        if ln.lower() not in seen:
            seen.add(ln.lower())
            uniq.append(ln)
    out: dict = {"lines": "\n".join(uniq[-16:])[:1200]}
    m = _SYSLIB_RE.search(text)
    if m:
        out["missing_lib"] = next((g for g in m.groups() if g), None)
    return out


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


def _bioc_conda_spec(package: str) -> str:
    """bioconda mirrors Bioconductor as 'bioconductor-<name lowercased>'
    (DESeq2 → bioconductor-deseq2, edgeR → bioconductor-edger, limma → bioconductor-limma)."""
    return "bioconductor-" + (package or "").strip().lower()


def install_bioconductor_conda(package: str, library: str, *, project_id: Optional[str] = None,
                               cancel_token=None) -> Optional[dict]:
    """Install a Bioconductor package as a prebuilt CONDA BINARY into the shared
    tools env (the env's R finds it on .libPaths()), avoiding the slow + fragile
    BiocManager source compile of the whole dependency tree — the XVector-style
    failure that blocks heavy/popular packages (DESeq2/limma/edgeR). Returns a
    ready dict on success, or None if the binary isn't on bioconda / didn't load,
    so the caller falls back to the BiocManager source path."""
    from core.exec.mamba import run_micromamba, installed_packages
    from core.runtime import progress
    spec = _bioc_conda_spec(package)
    tenv = tools_env()
    if spec not in installed_packages(tenv):
        progress.emit(f"conda: installing {spec} (Bioconductor binary — avoids source compile)…",
                      phase="conda")
        try:
            verb = "install" if (tenv / "conda-meta").exists() else "create"
            run_micromamba([verb, "-y", "-p", str(tenv), "-c", "bioconda", "-c", "conda-forge", spec],
                           cancel_token=cancel_token)
        except Exception:  # noqa: BLE001 — not on bioconda / solve failed → caller falls back to source
            return None
    if r_has_package(library, project_id=project_id):
        return {"status": "ready", "package": package, "source": "bioconductor",
                "library": library, "via": "conda"}
    return None


def r_install(source: str, package: str, *, project_id: str, library: Optional[str] = None,
              ref: Optional[str] = None, force: bool = False,
              timeout_s: int = 1800, cancel_token=None) -> dict:
    """Native install (CRAN / Bioconductor / GitHub) into the project R library,
    then **verify it loads**. For CRAN/Bioc, a binary that installs but won't
    load under conda R (or an install that fails) triggers one automatic
    **source** retry (compiled via the conda toolchain → consistent ABI/libs).
    On genuine failure, returns an actionable diagnostic (incl. a missing
    system-library hint) rather than an opaque log."""
    err = validate_install(source, package, ref)
    if err:
        return {"status": "error", "note": err}
    ensure_r_runtime(cancel_token=cancel_token)
    lib = project_r_lib(project_id)
    libname = library or (
        package.split("/")[-1] if source == "github"
        else (package[2:] if source == "conda" and package.startswith("r-") else package))

    # conda source: install a prebuilt conda-forge/bioconda R binary into the
    # shared base (e.g. r-hdf5r, which bundles the HDF5 system lib — the
    # source-compile path can't find it). Goes to the tools env's R library, so
    # it's on .libPaths() for every project. This is the explicit, no-compile
    # path for R packages with heavy system-lib deps.
    if source == "conda":
        from core.runtime import progress
        progress.emit(f"R: installing {package} from conda into the shared base "
                      f"(prebuilt binary — no source compile)…", phase="r")
        try:
            ensure_r_base([package])
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "package": package, "source": "conda",
                    "note": f"conda install of {package!r} failed: {e}"}
        if r_has_package(libname, project_id=project_id):
            return {"status": "ready", "package": package, "source": "conda",
                    "library": libname, "lib": str(tools_env())}
        return {"status": "error", "package": package, "source": "conda",
                "note": f"{package!r} installed via conda but R library({libname}) "
                        f"isn't loadable — check the 'library' name (conda 'r-foo' → library 'foo')."}

    # Heavy/popular Bioconductor packages (DESeq2/limma/edgeR + their dep trees)
    # are fragile to source-compile (e.g. XVector → 'make Error 1'); prefer the
    # prebuilt conda binary from bioconda, falling back to BiocManager source only
    # if it isn't available there.
    if source == "bioconductor":
        cres = install_bioconductor_conda(package, libname, project_id=project_id,
                                          cancel_token=cancel_token)
        if cres is not None:
            return cres

    from core.runtime import progress

    def _attempt(force_source: bool):
        expr = install_command(source, package, lib=str(lib), ref=ref,
                               force_source=force_source, force=force)
        proc = _run_rscript(expr, timeout_s, cancel_token=cancel_token)
        loaded = proc.returncode == 0 and r_has_package(libname, project_id=project_id)
        return proc, loaded

    progress.emit(f"R: installing {package} from {source} (parallel build, j={build_jobs()})…", phase="r")
    proc, loaded = _attempt(force_source=False)
    used_source_fallback = False
    if not loaded and source in ("cran", "bioconductor"):
        # Binary missing/failed, or installed-but-won't-load → recompile from source.
        progress.emit(f"R: {package} binary didn't load — recompiling from source…", phase="r")
        used_source_fallback = True
        proc, loaded = _attempt(force_source=True)

    if loaded:
        return {"status": "ready", "package": package, "source": source, "lib": str(lib),
                "library": libname, "source_fallback": used_source_fallback}

    log = ((proc.stderr or "") + "\n" + (proc.stdout or ""))
    diag = diagnose_install(log)

    # Auto-recover a missing system library: conda-install the providing package
    # into the R env and retry once (compiled against the now-present lib). This
    # is the recover-smartly the diagnostic used to only *suggest* — the xml2
    # gap that blocked pagoda2 now self-heals.
    if not loaded and diag.get("missing_lib"):
        libpkg = _SYS_LIB_CONDA.get(diag["missing_lib"].lower(), diag["missing_lib"])
        progress.emit(f"R: missing system lib '{diag['missing_lib']}' — conda-installing "
                      f"{libpkg} and retrying…", phase="r")
        try:
            ensure_r_base([libpkg])
            proc, loaded = _attempt(force_source=True)
            used_source_fallback = True
        except Exception:  # noqa: BLE001 — recovery is best-effort
            pass
        if loaded:
            return {"status": "ready", "package": package, "source": source, "lib": str(lib),
                    "library": libname, "source_fallback": True, "recovered_lib": libpkg}
        log = ((proc.stderr or "") + "\n" + (proc.stdout or ""))
        diag = diagnose_install(log)

    # Auto-recover a missing R-package dependency that lives on Bioconductor: a
    # CRAN package (e.g. conos) can hard-depend on a Bioc package (ComplexHeatmap)
    # that the CRAN/PPM repo can't supply (→ "dependency 'X' is not available").
    # Install each such dep as a conda Bioc BINARY (onto .libPaths), then retry —
    # so a CRAN package with Bioc deps installs fast instead of needing GitHub/source.
    if not loaded and source == "cran":
        recovered = []
        for dep in _missing_r_deps(log):
            if r_has_package(dep, project_id=project_id):
                continue
            if install_bioconductor_conda(dep, dep, project_id=project_id, cancel_token=cancel_token):
                recovered.append(dep)
        if recovered:
            progress.emit(f"R: installed Bioconductor dep(s) {', '.join(recovered)} as conda binaries — "
                          f"retrying {package}…", phase="r")
            proc, loaded = _attempt(force_source=False)
            if loaded:
                return {"status": "ready", "package": package, "source": source, "lib": str(lib),
                        "library": libname, "recovered_deps": recovered}
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


def build_jobs() -> int:
    """Parallel-build width for source compiles. Override via ABA_R_BUILD_JOBS;
    else #CPUs capped at 8 (diminishing returns + memory pressure beyond that)."""
    import os
    env = os.environ.get("ABA_R_BUILD_JOBS")
    if env and env.isdigit():
        return max(1, int(env))
    return max(1, min(os.cpu_count() or 2, 8))


def _parallel_expr() -> str:
    """R prefix enabling parallel source builds: MAKEFLAGS=-jN parallelizes the
    compile *within* a package; Ncpus parallelizes builds *across* the
    dependency tree. Big win for source/GitHub installs (e.g. pagoda2, Seurat)."""
    n = build_jobs()
    return f'Sys.setenv(MAKEFLAGS="-j{n}"); options(Ncpus={n}); '


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


def install_command(source: str, package: str, *, lib: str, ref: Optional[str] = None,
                    repos: Optional[str] = None, force_source: bool = False,
                    force: bool = False) -> str:
    """Pure builder: the R expression to install `package` from `source` into
    `lib` (project library), prepended ahead of the base on .libPaths(). CRAN/
    Bioc installs set the PPM User-Agent so binaries are served when available
    (source otherwise); `force_source=True` requests source explicitly (the
    fallback when a binary won't load under conda R). GitHub always builds from
    source."""
    libq = repr(str(lib))
    setlib = f'.libPaths(c({libq}, .libPaths())); '
    par = _parallel_expr()
    n = build_jobs()
    if source == "github":
        spec = f"{package}@{ref}" if ref else package
        # force=TRUE + upgrade="always" so an UPGRADE actually reinstalls instead
        # of install_github skipping an already-present (but stale) package.
        upgrade = "always" if force else "never"
        force_arg = ", force=TRUE" if force else ""
        return (f'{setlib}{par}remotes::install_github({spec!r}, lib={libq}, '
                f'upgrade={upgrade!r}{force_arg})')
    ua = _ppm_ua_expr()
    typ = ', type="source"' if force_source else ''
    if source == "bioconductor":
        # NB: PPM does NOT build Bioconductor binaries (CRAN only, per Posit docs),
        # so this BiocManager path source-compiles the Bioc packages themselves.
        # r_install tries the conda binary (bioconductor-<pkg>) FIRST via
        # install_bioconductor_conda; this is only the fallback for Bioc packages not
        # on bioconda. We still point the CRAN *dependencies* at PPM (repos + UA) so
        # at least those resolve as binaries — only the actual Bioc pkgs compile.
        repos_opt = f'options(repos=c(CRAN={cran_repo()!r})); '
        return f'{setlib}{ua}{repos_opt}{par}BiocManager::install({package!r}, lib={libq}, update=FALSE, ask=FALSE, Ncpus={n}{typ})'
    repos = repos or cran_repo()
    return f'{setlib}{ua}{par}install.packages({package!r}, lib={libq}, repos={repos!r}, Ncpus={n}{typ})'


