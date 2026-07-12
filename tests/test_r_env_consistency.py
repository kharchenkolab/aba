"""R runtime consistency (regression 2026-07-12: dotCall64 _MAYBE_SHARED ABI mismatch).

Both conda R paths — ensure_r_runtime (RUNTIME_SPECS) and the r-bio module
(install/core/r-environment.yml) — write the SAME tools env, so they MUST pin the same
r-base minor; and the project library must be keyed by R version+arch so a build for
one R minor is never loaded into another.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import core.exec.r as r          # noqa: E402


def test_r_base_pin_matches_r_environment_yml():
    yml = (ROOT / "install" / "core" / "r-environment.yml").read_text()
    m = re.search(r"^\s*-\s*r-base\s*=\s*(\S+)", yml, re.MULTILINE)
    assert m, "r-environment.yml has no r-base pin"
    assert r.R_BASE_PIN == f"r-base={m.group(1)}", (
        f"RUNTIME_SPECS pin {r.R_BASE_PIN!r} != r-environment.yml r-base={m.group(1)!r} "
        f"— the two conda R paths would install different R minors into the same env")
    assert r.R_BASE_PIN in r.RUNTIME_SPECS


def test_project_lib_is_scoped_by_runtime_tag(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "R_LIBS_ROOT", tmp_path)
    # determinable tag → path is scoped under it
    monkeypatch.setattr(r, "_r_runtime_tag", lambda: "R-4.4-aarch64")
    p = r.project_r_lib("prj_x")
    assert p == tmp_path / "prj_x" / "R-4.4-aarch64" and p.is_dir()
    # a different R minor → a DIFFERENT dir (no cross-contamination)
    monkeypatch.setattr(r, "_r_runtime_tag", lambda: "R-4.5-aarch64")
    assert r.project_r_lib("prj_x") == tmp_path / "prj_x" / "R-4.5-aarch64"
    # undeterminable → safe fallback to the flat path
    monkeypatch.setattr(r, "_r_runtime_tag", lambda: None)
    assert r.project_r_lib("prj_x") == tmp_path / "prj_x"
