"""Follow-ups from the pagoda2/hdf5r diagnosis (P5 fixes #5, #6b, #6c, #7).

  #7  hdf5r is baked into the R base spec (conda R is global-only — bake the
      common system-lib packages instead of recovering them on demand).
  #6b diagnose_install detects hdf5r's 'could not find your HDF5' wording so the
      runtime's missing-system-lib recovery fires.
  #6c a background job that exits 0 but whose log reports an error (a swallowed
      install failure) is re-labelled FAILED in the continuation, not 'no-op'.
  #5  the candidate resolver also offers the conda-forge binary for R packages.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.bio


# ── #7: hdf5r baked into the R base build spec ───────────────────────────────
def test_hdf5r_in_r_base_spec():
    yml = (ROOT / "install/mac/r-environment.yml").read_text()
    assert "r-hdf5r" in yml, "r-hdf5r should be baked into the R base (conda R is global-only)"


# ── #6b: missing-system-lib detection covers hdf5r's wording ─────────────────
def test_diagnose_install_detects_hdf5_not_found():
    from core.exec.r import diagnose_install, _SYS_LIB_CONDA
    log = ("checking for HDF5... no\n"
           "configure: error: We could not find your HDF5 installation. "
           "Use the --with-hdf5 argument...\n"
           "ERROR: configuration failed for package 'hdf5r'\n")
    diag = diagnose_install(log)
    lib = (diag.get("missing_lib") or "").lower()
    assert lib == "hdf5", diag
    # and it maps to a conda package the recovery can install
    assert _SYS_LIB_CONDA.get(lib) == "hdf5"


def test_diagnose_install_existing_patterns_still_work():
    from core.exec.r import diagnose_install
    assert (diagnose_install("/usr/bin/ld: cannot find -lglpk").get("missing_lib")) == "glpk"


# ── #6c: a swallowed (exit-0) install failure is surfaced as FAILED ──────────
def test_output_failure_lines_catches_swallowed_install():
    from core.jobs.continuation import _output_failure_lines
    tail = ("trying URL ...\n"
            "ERROR: dependency 'hdf5r' is not available for package 'pagoda2'\n"
            "* removing '.../pagoda2'\n"
            "Warning: installation of package 'pagoda2' had non-zero exit status\n"
            "installed: FALSE\n")
    lines = _output_failure_lines(tail)
    assert any("hdf5r" in ln for ln in lines)
    assert any("non-zero exit status" in ln for ln in lines)


def test_output_failure_lines_quiet_on_clean_output():
    from core.jobs.continuation import _output_failure_lines
    assert _output_failure_lines("made plot\nwrote results.csv\ndone\n") == []


def test_continuation_relabels_masked_failure(tmp_path):
    from core.graph import _schema
    from core.graph._schema import init_db
    from core.jobs.continuation import _continuation_message_text
    tok = _schema.bind_active_db(str(tmp_path / "p.db"))
    try:
        init_db()
        job = {"id": "job_x", "title": "Install pagoda2@devel from GitHub",
               "status": "done",
               "log_tail": "ERROR: dependency 'hdf5r' is not available\n"
                           "installation of package 'pagoda2' had non-zero exit status\n"}
        msg = _continuation_message_text(job, None)
        assert "FAILED" in msg and "hdf5r" in msg
        assert "no-op" not in msg.lower()
    finally:
        _schema.reset_active_db(tok)


# ── #5: resolver offers the conda-forge R binary ─────────────────────────────
def test_conda_r_alternative_shape():
    from content.bio.tools.discovery import _conda_r_alternative
    c = _conda_r_alternative("hdf5r")
    assert c["source"] == "conda" and c["archetype"] == "r_package"
    assert c["package"] == "r-hdf5r" and c["library"] == "hdf5r"


def test_resolver_appends_conda_for_r_packages():
    # if the search resolved an R-package candidate, a conda alternative is added
    import content.bio.tools.discovery as d
    cands = [{"source": "cran", "archetype": "r_package", "package": "hdf5r"}]
    if any(c.get("source") in ("cran", "bioconductor") for c in cands):
        cands.append(d._conda_r_alternative("hdf5r"))
    cands.sort(key=lambda c: d._SUGGESTION_ORDER.get(c.get("source", ""), 9))
    sources = [c["source"] for c in cands]
    assert "conda" in sources
    # cran stays the first-ranked suggestion; conda is the alternative
    assert sources[0] == "cran"
