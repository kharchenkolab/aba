"""env_refactor.md P0 — real load-verification (Python + R).

The corruption that bit us was reported "ready" because the check was
PathFinder.find_spec (presence), not a real import. These tests pin the
present-but-unloadable case (ABI mismatch / partial install) that find_spec
misses but a real import catches.
"""
from __future__ import annotations
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.exec.env_integrity import verify_python_imports, verify_r_library  # noqa: E402

pytestmark = pytest.mark.platform


# ── Python ───────────────────────────────────────────────────────────────────
def test_verify_python_imports_good():
    ok, detail = verify_python_imports(["os", "sys", "json"], extra_paths=[])
    assert ok and detail == ""


def test_verify_python_imports_missing():
    ok, detail = verify_python_imports(["no_such_module_xyz_123"], extra_paths=[])
    assert not ok
    assert "No module named" in detail or "ModuleNotFoundError" in detail


def test_verify_python_imports_present_but_unloadable(tmp_path):
    """The tensorflow case: a package that EXISTS (find_spec passes) but raises
    on import. find_spec says ready; a real import does not."""
    pkg = tmp_path / "abadummy"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(
        "raise ImportError('numpy.core.multiarray failed to import')\n")
    # find_spec WOULD green-light it (the old, wrong check)
    from importlib.machinery import PathFinder
    assert PathFinder.find_spec("abadummy", [str(tmp_path)]) is not None
    # the real check catches it
    ok, detail = verify_python_imports(["abadummy"], extra_paths=[str(tmp_path)])
    assert not ok
    assert "multiarray" in detail


def test_verify_python_imports_empty_is_ok():
    assert verify_python_imports([], extra_paths=[]) == (True, "")


def test_verify_python_imports_appends_overlay_not_prepend(tmp_path):
    """Overlay paths must be APPENDED (base wins), matching the run_python
    preamble — verify by shadowing a stdlib name in the overlay and confirming
    the base copy still wins."""
    (tmp_path / "json.py").write_text("raise RuntimeError('overlay json should not win')\n")
    ok, _ = verify_python_imports(["json"], extra_paths=[str(tmp_path)])
    assert ok  # base json wins because the extra path is appended, not prepended


# ── base constraints (the numpy-drift guard) ────────────────────────────────
def test_ensure_base_constraints_pins_numpy(tmp_path, monkeypatch):
    import core.exec.env_integrity as ei
    monkeypatch.setattr(ei, "base_constraints_path", lambda: tmp_path / "c.txt")
    p = ei.ensure_base_constraints(force=True)
    assert p is not None and p.exists()
    lines = p.read_text().splitlines()
    assert any(ln.lower().startswith("numpy==") for ln in lines), "base must pin numpy"
    # only clean name==version lines (no editable/URL entries that break -c)
    import re
    assert all(re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*==", ln) for ln in lines if ln.strip())


def test_constraints_block_conflicting_install(tmp_path):
    """Proof the guard works: with numpy pinned high, requesting an old numpy
    must FAIL the resolve instead of silently downgrading the shared base."""
    cons = tmp_path / "c.txt"
    cons.write_text("numpy==2.4.6\n")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-c", str(cons),
             "--dry-run", "numpy==1.26.4"],
            capture_output=True, text=True, timeout=90)
    except subprocess.TimeoutExpired:
        pytest.skip("pip dry-run timed out (network)")
    out = (proc.stderr or "") + (proc.stdout or "")
    if any(s in out for s in ("Could not fetch", "Temporary failure",
                              "Network is unreachable", "Failed to establish")):
        pytest.skip("no network for pip metadata")
    assert proc.returncode != 0, f"constraint should have blocked the downgrade:\n{out[:600]}"


# ── P6: lazy-from-lock (canonical lock + materialize-from-lock) ──────────────
def test_canonical_lock_path(tmp_path, monkeypatch):
    from core.exec.env_integrity import canonical_lock_path
    monkeypatch.delenv("ABA_BASE_LOCK", raising=False)
    assert canonical_lock_path() is None
    lock = tmp_path / "canon.txt"
    lock.write_text("numpy==2.4.6\n")
    monkeypatch.setenv("ABA_BASE_LOCK", str(lock))
    assert canonical_lock_path() == lock


def test_ensure_base_constraints_prefers_canonical(tmp_path, monkeypatch):
    from core.exec import env_integrity as ei
    canon = tmp_path / "canon.txt"
    canon.write_text("numpy==2.4.6\nscanpy==1.12.1\n")
    monkeypatch.setenv("ABA_BASE_LOCK", str(canon))
    assert ei.ensure_base_constraints() == canon   # shipped canonical wins


def test_write_base_lock(tmp_path):
    from core.exec.env_integrity import write_base_lock
    out = write_base_lock(tmp_path / "lock.txt")
    assert out is not None and out.exists()
    lines = out.read_text().splitlines()
    assert any(ln.lower().startswith("numpy==") for ln in lines)


def test_materialize_from_lock_pins_version(tmp_path, monkeypatch):
    """Lazy-from-lock: a lock pinning `six` to an OLD version → materialize
    installs THAT version, not latest. Proves a minimal install grows to the
    canonical versions."""
    import glob
    from core.exec import env_integrity as ei
    lock = tmp_path / "lock.txt"
    lock.write_text("six==1.16.0\n")
    monkeypatch.setenv("ABA_BASE_LOCK", str(lock))
    prefix = tmp_path / "pfx"
    res = ei.materialize_from_lock(["six"], prefix=prefix, timeout_s=300)
    if not res["ok"] and any(s in str(res.get("error", "")) for s in
                             ("Could not fetch", "Temporary failure", "Network",
                              "Failed to establish")):
        pytest.skip("no network for the materialize")
    assert res["ok"], res["error"]
    assert res["lock"] == str(lock)
    dist = glob.glob(str(prefix) + "/lib/python*/site-packages/six-*.dist-info")
    assert any("six-1.16.0" in d for d in dist), f"expected six 1.16.0 from the lock, got {dist}"


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
    # python: base + shared overlay always; layers carry the expected fields
    tiers = [L["tier"] for L in d["python"]["layers"]]
    assert "base" in tiers and "shared overlay" in tiers
    base = next(L for L in d["python"]["layers"] if L["tier"] == "base")
    assert base["mutable"] is False and len(base["packages"]) > 0
    assert {"tier", "scope", "mutable", "path", "packages"} <= set(base)
    assert d["python"]["lock"]["pins"] >= 0
    # r structure present (packages populated only if R is provisioned)
    assert d["r"]["layers"] and d["r"]["layers"][0]["tier"] == "base"


# ── base self-heal + error surfacing (fix 1 + surfacing) ─────────────────────
def test_base_health_shape():
    from core.exec.env_integrity import base_health
    h = base_health(deep=False)   # fast pip-check; read-only
    assert set(("ok", "problems", "missing")) <= set(h)
    assert isinstance(h["problems"], list) and isinstance(h["missing"], list)


def test_missing_dep_parser():
    from core.exec.env_integrity import _MISSING_RE
    m = _MISSING_RE.search("pandas 2.3.3 requires six, which is not installed.")
    assert m and m.group(1) == "six"


def test_env_root_cause_ignores_plain_code_errors():
    """A normal code error (not import-shaped) must pass through untouched —
    we don't want to diagnose the base on every ValueError."""
    from core.exec.env_integrity import env_root_cause
    assert env_root_cause("ValueError: bad input", repair=False) is None
    assert env_root_cause("", repair=False) is None


def test_env_root_cause_none_when_base_healthy():
    """Import-shaped stderr but an intact base = the user's own missing import,
    not an env break — leave it alone (return None), don't claim the base broke."""
    from core.exec.env_integrity import env_root_cause, base_health
    if not base_health(deep=False)["ok"]:
        import pytest as _pt
        _pt.skip("base currently unhealthy on this box")
    assert env_root_cause("ModuleNotFoundError: No module named 'totallymadeup_xyz'",
                          repair=False) is None


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
    assert {"python", "shared_overlay", "project_overlay", "base_lock"} <= set(ov)
    assert ov["project_overlay"]["project_id"] == "prjX"
    assert "pylib_proj" in (ov["project_overlay"]["dir"] or "")


# ── R (wrapper logic; real Rscript load is exercised in the R-scenario test) ──
def test_verify_r_library_wraps_r_has_package(monkeypatch):
    import core.exec.r as r
    monkeypatch.setattr(r, "r_has_package", lambda pkg, project_id=None: pkg == "Matrix")
    assert verify_r_library("Matrix") == (True, "")
    ok, detail = verify_r_library("nonexistent_rpkg")
    assert not ok and "does not load" in detail


def test_verify_r_library_empty_is_ok():
    assert verify_r_library("") == (True, "")


def test_verify_r_library_real_load(monkeypatch):
    """End-to-end Rscript library() load of a baked base package — the R analog
    of the Python real-import test. Skips if the R runtime isn't provisioned."""
    from core.exec.materialize import tools_env
    if not (tools_env() / "bin" / "Rscript").exists():
        pytest.skip("R runtime not provisioned on this box")
    ok, _ = verify_r_library("Matrix")          # r-matrix is baked into the R base
    assert ok, "baked base package Matrix should load"
    ok2, detail = verify_r_library("NoSuchRPkgXyz")
    assert not ok2 and "does not load" in detail
