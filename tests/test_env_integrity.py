"""env diagnostics + base-stage marker (core.exec.env_integrity).

The honest import/GPU load-verification tests moved to test_verify.py, and the
served-base heal/repair/lock machinery + its tests were deleted with the served
base (W3.4/W3.5 — weft owns environment realization). What remains here is the
read-only diagnostics layer (the (i)-drawer Env tab data + per-package status)
and the install-time base-stage marker. FS self-checks are covered in
test_selfcheck.py.
"""
from __future__ import annotations
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

pytestmark = pytest.mark.platform


# ── env_layers (the (i)-drawer Env-tab data) ─────────────────────────────────
def test_py_packages_enumerates_base():
    import sysconfig
    from core.exec.env_integrity import _py_packages
    pkgs = _py_packages([sysconfig.get_path("purelib")])
    names = {p["name"].lower() for p in pkgs}
    assert "numpy" in names and all("version" in p and "name" in p for p in pkgs)
    # sorted, deduped
    assert [p["name"].lower() for p in pkgs] == sorted(p["name"].lower() for p in pkgs)


def test_env_layers_structure():
    from core.exec.env_integrity import env_layers
    d = env_layers("prjX")
    assert set(("python", "r", "project_id")) <= set(d)
    # W3.5 weft-only: the Python env is the weft session (+ isolated envs) — no
    # served-base venv/overlay tiers. Populated only when a python pack is declared.
    assert isinstance(d["python"]["layers"], list)
    assert all(L["tier"] in ("session", "isolated") for L in d["python"]["layers"])
    assert "overlay" not in " ".join(L["tier"] for L in d["python"]["layers"])
    # R symmetric: session/isolated tiers, populated only when an R pack is declared.
    assert isinstance(d["r"]["layers"], list)
    assert all(L["tier"] in ("session", "isolated") for L in d["r"]["layers"])


def test_rlib_scan_skips_lock_and_temp_dirs(tmp_path):
    """The cran-layer overlay scan must count only REAL package dirs (each has a
    DESCRIPTION), never the installer's transient 00LOCK-/staging dirs — else a
    crashed or in-flight install surfaces a phantom '00LOCK-foo' package."""
    from core.exec.env_integrity import _rlib_package_names
    (tmp_path / "charts").mkdir()
    (tmp_path / "charts" / "DESCRIPTION").write_text("Package: charts\n")
    (tmp_path / "frames").mkdir()
    (tmp_path / "frames" / "DESCRIPTION").write_text("Package: frames\n")
    (tmp_path / "00LOCK-charts").mkdir()                  # interrupted-install lock dir
    (tmp_path / "00LOCK-charts" / "DESCRIPTION").write_text("x")   # even if it has one
    (tmp_path / "file2a3f").mkdir()                       # staging temp, no DESCRIPTION
    assert _rlib_package_names(tmp_path) == ["charts", "frames"]
    from pathlib import Path
    assert _rlib_package_names(Path(str(tmp_path / "missing"))) == []


# ── env diagnostics (the agent's read layer for troubleshooting) ─────────────
def test_python_package_status_good():
    from core.exec.env_integrity import python_package_status
    st = python_package_status("json", extra_paths=[])
    assert st["loads"] is True and st["error"] is None
    assert st["version"] is not None or st["location"] is not None


def test_python_package_status_missing():
    from core.exec.env_integrity import python_package_status
    st = python_package_status("no_such_pkg_zzz", extra_paths=[])
    assert st["loads"] is False and st["error"]


def test_python_package_status_present_but_broken(tmp_path):
    from core.exec.env_integrity import python_package_status
    pkg = tmp_path / "abadbg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("raise ImportError('numpy.core.multiarray failed to import')\n")
    st = python_package_status("abadbg", extra_paths=[str(tmp_path)])
    assert st["loads"] is False and "multiarray" in st["error"]


def test_env_overview_shape():
    from core.exec.env_integrity import env_overview
    ov = env_overview("prjX")
    # W3.5 weft-only: the Python env is the project's weft session + the aba
    # runtime interpreter — no served-base pip overlays / base lock.
    assert {"python", "session"} <= set(ov)
    assert "base_lock" not in ov          # dropped with the served base (W3.4)
    assert ov["session"]["project_id"] == "prjX"
    assert ov["session"]["active"] is False   # no pack declared in this bare env


# ── lazy/staged env init (ABA_ENV_PREWARM) — lazy_env_init.md ────────────────
import core.exec.env_integrity as _ei_lazy  # noqa: E402


def test_base_stage_reads_marker(tmp_path, monkeypatch):
    monkeypatch.setattr(_ei_lazy, "_base_prefix", lambda: tmp_path)
    marker = tmp_path / ".aba-base-stage"
    assert _ei_lazy.base_stage() == "ready"           # absent ⇒ ready (eager/pre-staging)
    for v in ("boot", "completing", "ready"):
        marker.write_text(v)
        assert _ei_lazy.base_stage() == v
    marker.write_text("garbage")
    assert _ei_lazy.base_stage() == "ready"           # unknown ⇒ ready (safe default)
