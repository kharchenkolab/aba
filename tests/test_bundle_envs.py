"""weft rewrite W1: the envs/ bundle facet — named environment packs composed
by the loader (a peer to catalog/refsources), override-by-name, narrowest scope
wins. Domain enters as content: the platform knows "env pack"; only the YAML
names scanpy.

Run:  .venv/bin/python tests/test_bundle_envs.py   (also collected by pytest)
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from core.bundle.loader import _compose_envs, Provenance      # noqa: E402
from core.bundle.scope_resolver import ScopeBundle            # noqa: E402


def _scope(name: str, root: Path, files: dict[str, str]) -> ScopeBundle:
    edir = root / "envs"
    edir.mkdir(parents=True, exist_ok=True)
    for fn, text in files.items():
        (edir / fn).write_text(text)
    return ScopeBundle(name=name, label=name.title(), path=root, present=True)


def _fixture_scopes(tmp: Path):
    sys_scope = _scope("system", tmp / "sys", {
        "python_bio.yaml": (
            "name: python-bio\n"
            "title: Single-cell Python\n"
            "languages: [python]\n"
            "default_state: on\n"
            "first_use: [scanpy, anndata]\n"
            "role: base\n"
            "import_names: {scvi: scvi-tools}\n"
            "spec:\n"
            "  platforms: [linux-64]\n"
            "  deps:\n"
            "    conda: [python =3.12, 'numpy <2.5', scanpy]\n"
        ),
        "r_bio.yaml": (
            "name: r-bio\n"
            "languages: [r]\n"
            "default_state: first_use\n"
            "spec: {deps: {conda: [r-base =4.4, r-seurat]}}\n"
        ),
    })
    # institution scope: OVERRIDE python-bio + add a lab-only pack
    inst_scope = _scope("institution", tmp / "inst", {
        "python_bio.yaml": (
            "name: python-bio\n"
            "title: Lab single-cell Python\n"
            "languages: [python]\n"
            "default_state: on\n"
            "spec: {deps: {conda: [python =3.12, scanpy, 'harmonypy']}}\n"
        ),
        "spatial.yaml": (
            "name: spatial\n"
            "languages: [python]\n"
            "spec: {deps: {pypi: [squidpy]}}\n"
        ),
    })
    return sys_scope, inst_scope


def test_compose_override_and_layering():
    tmp = Path(tempfile.mkdtemp(prefix="aba_bundleenvs_"))
    sys_scope, inst_scope = _fixture_scopes(tmp)
    prov = Provenance()
    packs = {p.name: p for p in _compose_envs([sys_scope, inst_scope], prov)}

    assert set(packs) == {"python-bio", "r-bio", "spatial"}
    # narrowest wins: institution's python-bio shadows system's
    assert packs["python-bio"].spec["title"] == "Lab single-cell Python"
    assert packs["python-bio"].source_scope == "institution"
    assert "harmonypy" in packs["python-bio"].spec["spec"]["deps"]["conda"]
    # untouched system pack survives
    assert packs["r-bio"].source_scope == "system"
    assert packs["spatial"].source_scope == "institution"


def test_provenance_records_shadowing():
    tmp = Path(tempfile.mkdtemp(prefix="aba_bundleenvs_"))
    sys_scope, inst_scope = _fixture_scopes(tmp)
    prov = Provenance()
    _compose_envs([sys_scope, inst_scope], prov)
    assert prov.env_packs["python-bio"]["effective_scope"] == "institution"
    assert "system" in prov.env_packs["python-bio"]["shadowed_in"]
    assert prov.env_packs["r-bio"]["effective_scope"] == "system"


def test_packs_list_form_and_missing_dir():
    tmp = Path(tempfile.mkdtemp(prefix="aba_bundleenvs_"))
    multi = _scope("system", tmp / "m", {
        "all.yaml": (
            "packs:\n"
            "  - {name: a, spec: {deps: {pypi: [x]}}}\n"
            "  - {name: b, spec: {deps: {pypi: [y]}}}\n"
        ),
    })
    empty = ScopeBundle(name="user", label="User", path=tmp / "nope", present=True)
    packs = {p.name: p for p in _compose_envs([multi, empty], Provenance())}
    assert set(packs) == {"a", "b"}          # list form works, missing dir skipped


def test_spec_is_verbatim_weft_envspec():
    """aba adds nothing to the pack's `spec:` at compose time — solving is the
    compute substrate's job. The spec passes through byte-for-byte."""
    tmp = Path(tempfile.mkdtemp(prefix="aba_bundleenvs_"))
    s = _scope("system", tmp / "s", {
        "p.yaml": "name: p\nspec:\n  platforms: [linux-64]\n  deps: {conda: [python =3.12]}\n",
    })
    packs = {p.name: p for p in _compose_envs([s], Provenance())}
    assert packs["p"].spec["spec"] == {
        "platforms": ["linux-64"], "deps": {"conda": ["python =3.12"]}}


if __name__ == "__main__":
    import traceback
    fails = 0
    for fn in [test_compose_override_and_layering, test_provenance_records_shadowing,
               test_packs_list_form_and_missing_dir, test_spec_is_verbatim_weft_envspec]:
        try:
            fn()
            print(f"  [PASS] {fn.__name__}")
        except Exception:  # noqa: BLE001
            fails += 1
            print(f"  [FAIL] {fn.__name__}")
            traceback.print_exc()
    print("ALL ENV-FACET CHECKS PASSED" if not fails else f"FAILED ({fails})")
    sys.exit(1 if fails else 0)
