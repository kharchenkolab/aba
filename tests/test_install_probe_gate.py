"""The install gate must not be disabled by a missing data file.

`live_install_probe --pack-provided-only` is the regression gate for a live
incident: a library the shipped base packs PROVE they load must cost zero
environments to "install". Its expectation comes from the packs themselves, so
it has to keep working when the (larger, optional) package matrix is absent.

It did not. `_load_matrix` raised SystemExit on a missing file, SystemExit is a
BaseException, and the `except Exception` around the lookup let it straight
through — so deleting a JSON file silently turned the gate off. That is the
same shape as the defect the gate exists to catch: an instrument that cannot
run reads as an instrument that found nothing wrong.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "regtest" / "harness"))


def test_pack_scope_survives_a_missing_matrix(monkeypatch, tmp_path, capsys):
    """THE regression: no matrix file, gate still enumerates the packs."""
    import live_install_probe as lip
    monkeypatch.setattr(sys, "argv", [
        "p", "--base", "http://127.0.0.1:1", "--pack-provided-only",
        "--matrix", str(tmp_path / "nope.json"), "--limit", "0"])
    # stop before any HTTP: we are testing enumeration, not the turns
    monkeypatch.setattr(lip, "probe_one",
                        lambda *a, **k: {"name": k.get("entry", {}).get("name", "x"),
                                         "verdict": "ready_from_pack"})
    try:
        lip.main()
    except SystemExit as e:            # must NOT be the "no matrix" bail-out
        assert "no matrix file" not in str(e), e
    out = capsys.readouterr().out
    assert "pack-provided names known:" in out, out
    n = int(out.split("pack-provided names known:")[1].split()[0])
    assert n > 10, f"expected the shipped packs to advertise many names, got {n}"


def test_pack_expectation_comes_from_the_shipped_packs():
    """The gate must state an INDEPENDENT expectation.

    Reading it from the running server would mean asking the system under test
    what it believes it provides — which is precisely the belief that was
    wrong."""
    import live_install_probe as lip
    provided = lip.pack_provided()
    assert len(provided) > 10, provided
    assert set(provided.values()) <= {"r", "python"}, provided
    # every name must be a real load target, not a conda package name
    assert not [n for n in provided if n.startswith(("r-", "bioconductor-"))], (
        "pack_provided() must yield LIBRARY names (what a user asks for), not "
        "conda package names: " + str([n for n in provided
                                       if n.startswith(("r-", "bioconductor-"))]))


def test_a_matrix_file_enriches_but_does_not_gate(tmp_path):
    """WIDE: merging is additive and later files win on conflicts."""
    import live_install_probe as lip
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"entries": [
        {"name": "alpha", "language": "python", "ecosystem": "pypi", "package": "alpha"},
        {"name": "beta", "language": "r", "ecosystem": "cran", "package": "r-beta"}]}))
    b.write_text(json.dumps({"entries": [
        {"name": "alpha", "language": "python", "ecosystem": "pypi",
         "package": "alpha-real", "n_recipes": 4}]}))
    got = {e["name"]: e for e in lip._load_matrix(f"{a},{b}")}
    assert set(got) == {"alpha", "beta"}
    assert got["alpha"]["package"] == "alpha-real"     # later source wins
    assert got["alpha"]["n_recipes"] == 4
    assert got["beta"]["package"] == "r-beta"          # earlier survives


def test_missing_matrix_is_a_normal_exception():
    """So a caller can CATCH it. SystemExit could not be caught by the
    `except Exception` that was meant to keep the gate alive."""
    import live_install_probe as lip
    with pytest.raises(FileNotFoundError):
        lip._load_matrix("/nonexistent/one.json,/nonexistent/two.json")
