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

pytestmark = pytest.mark.platform


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
    # W3.5 weft-only: the Python env is the project's weft session (+ the aba
    # runtime interpreter + backend base lock) — no served-base pip overlays.
    assert {"python", "session", "base_lock"} <= set(ov)
    assert ov["session"]["project_id"] == "prjX"
    assert ov["session"]["active"] is False   # no pack declared in this bare env


# ── R (wrapper logic; real Rscript load is exercised in the R-scenario test) ──


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


def test_self_heal_skips_while_staging(tmp_path, monkeypatch):
    """The installer owns the base until `ready`; startup self-heal must NOT
    deep-verify/repair/freeze mid-write (it would fight the in-flight env update)."""
    site = tmp_path / "lib" / "python3.12" / "site-packages"
    site.mkdir(parents=True)
    monkeypatch.setattr(_ei_lazy, "_base_site_dir", lambda: site)
    monkeypatch.setattr(_ei_lazy, "base_stage", lambda: "completing")
    def _boom(**k):
        raise AssertionError("must not deep-verify the base mid-staging")
    monkeypatch.setattr(_ei_lazy, "base_health", _boom)
    res = _ei_lazy.self_heal_base(log=lambda *_a, **_k: None)
    assert res.get("skipped") == "staging" and res.get("stage") == "completing"


def test_staging_import_note_paths(monkeypatch):
    trace = "Traceback (most recent call last):\nModuleNotFoundError: No module named 'scanpy'\n"
    # ready base → not applicable (normal env_root_cause / user-error path)
    monkeypatch.setattr(_ei_lazy, "base_stage", lambda: "ready")
    assert _ei_lazy.staging_import_note(trace, wait_s=0) is None
    # non-import failure → not applicable even while completing
    monkeypatch.setattr(_ei_lazy, "base_stage", lambda: "completing")
    assert _ei_lazy.staging_import_note("ValueError: bad input", wait_s=0) is None
    # completing base + missing import → 'finishing setup' note naming the module
    note = _ei_lazy.staging_import_note(trace, wait_s=0)
    assert note is not None and note["ready"] is False and note["module"] == "scanpy"
    assert "finishing setup" in note["note"]


