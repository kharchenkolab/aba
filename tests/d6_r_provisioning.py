"""
R package provisioning (r_provisioning.md) — the `r_package` archetype + the
typed R layer (core/exec/r.py) + project .libPaths().

Deterministic by default (no conda/Rscript): command construction, name
validation, propose/ensure routing (R layer mocked), read_capability, the base
manifest, and the kernel libPaths line. Guarded live (ABA_R_LIVE=1) does a real
minimal runtime + tiny CRAN + GitHub install into a project lib.

Run:
    .venv/bin/python tests/d6_r_provisioning.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_d6_")
os.environ["ABA_DB_PATH"] = str(Path(_tmp) / "d6.db")
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"] = str(Path(_tmp) / "work")
os.environ["ABA_ENVS_DIR"] = str(Path(_tmp) / "envs")
os.environ["DATA_DIR"] = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import init_db                # noqa: E402
import content.bio  # noqa: E402,F401
from core.exec import r as rexec                       # noqa: E402
from content.bio.tools import (                        # noqa: E402
    propose_capability_tool, ensure_capability, read_capability,
)
from content.bio.capabilities import load_r_base_specs  # noqa: E402

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        _failures.append(label)


def test_command_and_validation():
    print("install command + validation (pure)")
    c = rexec.install_command("cran", "DESeq2", lib="/L")
    check("cran -> install.packages with lib", "install.packages('DESeq2', lib='/L'" in c, c)
    b = rexec.install_command("bioconductor", "DESeq2", lib="/L")
    check("bioc -> BiocManager::install", "BiocManager::install('DESeq2'" in b, b)
    g = rexec.install_command("github", "satijalab/seurat", lib="/L", ref="v5.0.0")
    check("github -> install_github with ref", "install_github('satijalab/seurat@v5.0.0'" in g, g)
    check("command prepends project lib on .libPaths", c.startswith(".libPaths(c('/L'"), c[:30])
    check("libpaths_expr prepends project lib", "proj1" in rexec.libpaths_expr("proj1"))
    # PPM: CRAN install targets a Posit PM repo + sets the binary-serving UA.
    check("cran uses a PPM repo", "packagemanager.posit.co" in c, c)
    check("cran sets the PPM HTTPUserAgent", "HTTPUserAgent" in c, c)
    check("github does NOT force a repo/UA", "HTTPUserAgent" not in g and "posit.co" not in g)


def test_ppm_repo_config():
    print("PPM repo URL + config knobs")
    import os
    # auto-detected distro on this box (noble) → binary URL
    check("default repo is a PPM url", "packagemanager.posit.co" in rexec.cran_repo(), rexec.cran_repo())
    os.environ["ABA_R_PPM_DISTRO"] = "jammy"
    os.environ["ABA_R_PPM_SNAPSHOT"] = "2024-12-01"
    try:
        url = rexec.cran_repo()
        check("respects distro override", "__linux__/jammy" in url, url)
        check("respects snapshot override", url.endswith("/2024-12-01"), url)
        os.environ["ABA_R_PPM_DISTRO"] = ""   # explicit empty → source-only
        check("empty distro -> source-only (no __linux__)", "__linux__" not in rexec.cran_repo(), rexec.cran_repo())
    finally:
        os.environ.pop("ABA_R_PPM_DISTRO", None)
        os.environ.pop("ABA_R_PPM_SNAPSHOT", None)
    # validation
    check("rejects injection-y name", rexec.validate_install("cran", "x); system('rm')", None) is not None)
    check("github needs owner/repo", rexec.validate_install("github", "justname", None) is not None)
    check("rejects bad ref", rexec.validate_install("github", "a/b", "x;y") is not None)
    check("accepts clean cran", rexec.validate_install("cran", "DESeq2", None) is None)


def test_source_flag_and_diagnostics():
    print("force_source flag + error diagnostics (pure)")
    c = rexec.install_command("cran", "xml2", lib="/L", force_source=True)
    check("force_source adds type=source (cran)", 'type="source"' in c, c)
    b = rexec.install_command("bioconductor", "limma", lib="/L", force_source=True)
    check("force_source adds type=source (bioc)", 'type="source"' in b, b)
    check("default (no force) has no type=source",
          'type="source"' not in rexec.install_command("cran", "xml2", lib="/L"))
    # diagnostics pull the actionable lines + a missing system-lib name
    log = ("** building package indices\n"
           "/usr/bin/ld: cannot find -lgdal\n"
           "ERROR: configuration failed for package 'rgdal'\n"
           "installation of package 'rgdal' had non-zero exit status\n")
    d = rexec.diagnose_install(log)
    check("diagnose flags missing system lib", d.get("missing_lib") == "gdal", str(d))
    check("diagnose keeps actionable lines", "had non-zero exit status" in (d.get("lines") or ""), str(d))


def test_verify_and_source_fallback():
    print("post-install verify + auto source-fallback (mocked)")
    import types
    orig = (rexec.ensure_r_runtime, rexec._run_rscript, rexec.r_has_package)
    rexec.ensure_r_runtime = lambda: None

    def proc(rc, err=""):
        return types.SimpleNamespace(returncode=rc, stdout="", stderr=err)

    try:
        # (1) binary installs (rc0) but won't load → source retry loads → ready+fallback
        runs = []
        rexec._run_rscript = lambda expr, t: (runs.append(expr), proc(0))[1]
        loads = iter([False, True])  # verify after binary fails, after source succeeds
        rexec.r_has_package = lambda pkg, project_id=None, timeout_s=60: next(loads)
        r = rexec.r_install("cran", "xml2", project_id="t", library="xml2")
        check("won't-load binary → source fallback → ready", r.get("status") == "ready", str(r))
        check("flagged as source_fallback", r.get("source_fallback") is True)
        check("two attempts made (binary + source)", len(runs) == 2)
        check("second attempt forced source", 'type="source"' in runs[1])

        # (2) both attempts fail → error with diagnostic + missing_lib
        rexec._run_rscript = lambda expr, t: proc(1, "cannot find -lpng\nhad non-zero exit status\n")
        rexec.r_has_package = lambda pkg, project_id=None, timeout_s=60: False
        r2 = rexec.r_install("cran", "Cairo", project_id="t", library="Cairo")
        check("both fail → error", r2.get("status") == "error", str(r2))
        check("error carries missing_lib", r2.get("missing_lib") == "png", str(r2))
        check("note mentions system library + conda/user", "system library" in (r2.get("note") or ""))

        # (3) github won't load → NO source fallback (already source) → error
        calls = []
        rexec._run_rscript = lambda expr, t: (calls.append(expr), proc(0))[1]
        rexec.r_has_package = lambda pkg, project_id=None, timeout_s=60: False
        r3 = rexec.r_install("github", "owner/pkg", project_id="t", library="pkg")
        check("github won't-load → error (no fallback)", r3.get("status") == "error", str(r3))
        check("github attempted once (no source retry)", len(calls) == 1)
    finally:
        rexec.ensure_r_runtime, rexec._run_rscript, rexec.r_has_package = orig


def test_propose():
    print("propose r_package")
    r1 = propose_capability_tool({"name": "DESeq2", "archetype": "r_package", "source": "bioconductor"})
    check("bioc r_package approved", r1.get("status") == "approved" and r1.get("archetype") == "r_package", str(r1))
    from core.catalog import resolve_capability
    cap = resolve_capability("DESeq2")
    rprov = (cap.get("provisioning") or {}).get("r") or {}
    check("provisioning.r stored", rprov.get("source") == "bioconductor" and rprov.get("package") == "DESeq2", str(rprov))
    # github: library defaults to the repo segment
    propose_capability_tool({"name": "presto", "archetype": "r_package", "source": "github",
                             "package": "immunogenomics/presto"})
    gcap = resolve_capability("presto")
    gr = (gcap.get("provisioning") or {}).get("r") or {}
    check("github library defaults to repo segment", gr.get("library") == "presto", str(gr))


def test_ensure_routing():
    print("ensure_capability routing (R layer mocked)")
    calls = {"install": 0}
    orig = (rexec.ensure_r_runtime, rexec.r_has_package, rexec.r_install)
    rexec.ensure_r_runtime = lambda: None
    try:
        # Already present in the library path → ready, NO install.
        rexec.r_has_package = lambda pkg, project_id=None, timeout_s=60: True
        rexec.r_install = lambda *a, **k: calls.__setitem__("install", calls["install"] + 1) or {"status": "ready"}
        res = ensure_capability({"name": "DESeq2"})
        check("present -> ready", res.get("status") == "ready", str(res))
        check("present -> no install attempted", calls["install"] == 0)
        # Missing → project-native install.
        rexec.r_has_package = lambda pkg, project_id=None, timeout_s=60: False
        rexec.r_install = lambda source, package, **k: (calls.__setitem__("install", calls["install"] + 1),
                                                        {"status": "ready"})[1]
        res2 = ensure_capability({"name": "DESeq2"})
        check("missing -> install -> ready", res2.get("status") == "ready", str(res2))
        check("missing -> install attempted", calls["install"] == 1)
    finally:
        rexec.ensure_r_runtime, rexec.r_has_package, rexec.r_install = orig


def test_read_capability():
    print("read_capability for r_package")
    r = read_capability({"name": "DESeq2"})
    check("shows r_source + library", r.get("r_source") == "bioconductor" and r.get("library") == "DESeq2", str(r))
    check("note points at run_r library()", "run_r" in (r.get("note") or "") and "library(" in (r.get("note") or ""))


def test_base_manifest():
    print("curated base manifest")
    specs = load_r_base_specs()
    for want in ("r-seurat", "bioconductor-deseq2", "r-tidyverse", "r-rcpparmadillo", "r-cairo"):
        check(f"manifest includes {want}", want in specs, str(specs))


def test_kernel_libpaths():
    print("R kernel sets project .libPaths()")
    from core.exec.kernels.jupyter import _r_setup_code
    code = _r_setup_code("/tmp/work")
    check("setup prepends a project lib on .libPaths()", ".libPaths(c(" in code and "r_libs" in code, code[:80])
    check("setup still sets DATA_DIR + cwd", "DATA_DIR <-" in code and "setwd(" in code)


def test_live():
    if os.environ.get("ABA_R_LIVE") != "1":
        print("live R install: SKIPPED (set ABA_R_LIVE=1 to run)")
        return
    print("live: runtime + PPM CRAN (pure-R + compiled) + GitHub install")
    rexec.ensure_r_runtime()
    check("R runtime ready", rexec.r_runtime_ready())
    print(f"    CRAN repo in use: {rexec.cran_repo()}")
    # pure-R from PPM
    cr = rexec.r_install("cran", "praise", project_id="d6live")
    check("CRAN install (praise) ready", cr.get("status") == "ready", str(cr)[:300])
    check("praise loadable in project lib", rexec.r_has_package("praise", project_id="d6live"))
    # compiled package from PPM — the real test of the binary route under conda R
    jc = rexec.r_install("cran", "jsonlite", project_id="d6live")
    check("CRAN install (jsonlite, compiled) ready", jc.get("status") == "ready", str(jc)[:300])
    check("jsonlite loadable in project lib", rexec.r_has_package("jsonlite", project_id="d6live"))
    # GitHub (CRAN mirror, pure-R) → source build path
    gh = rexec.r_install("github", "cran/crayon", project_id="d6live")
    check("GitHub install (cran/crayon) ready", gh.get("status") == "ready", str(gh)[:300])
    check("crayon loadable in project lib", rexec.r_has_package("crayon", project_id="d6live"))


def main() -> int:
    init_db()
    test_command_and_validation()
    test_ppm_repo_config()
    test_source_flag_and_diagnostics()
    test_verify_and_source_fallback()
    test_propose()
    test_ensure_routing()
    test_read_capability()
    test_base_manifest()
    test_kernel_libpaths()
    test_live()
    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL R-PROVISIONING CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
