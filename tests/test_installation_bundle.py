"""A deployment's installation bundle is assembled, complete, and its own.

The SIF baked `ABA_INSTITUTION_BUNDLE=/opt/aba/installation` into the image's
%environment. That path holds ONLY `envs/` with the two base specs, and because
an env var outranks site.yaml it permanently occupied the institution slot — so
everything `deploy.sh` staged to `$SHARE/installation` was written and never
read: the derived GPU pack (which is why it could not be published) and the
site's reference-source catalogues.

A personal install already does this correctly: `~/.aba/installation` carries
the base specs, the GPU spec, catalog, knowhow and skills TOGETHER. These guard
the same shape for a share-based deployment, where staging and production are
the same code path with a different $SHARE.

Run: python tests/test_installation_bundle.py   (or via pytest)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend"))

from assemble_installation import AssemblyError, assemble  # noqa: E402


def _mk(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)   # an EMPTY dir must still exist:
    # without this, `_mk(x, {})` returned a path that was never created, so the
    # empty-but-present case fell through to the missing-dir check and the
    # degenerate shape this guard exists for was never exercised.
    for rel, body in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return root


# ── assembly ────────────────────────────────────────────────────────────────

def test_the_bundle_carries_image_content_and_site_content_together(tmp_path):
    """THE property that was false: all env packs in ONE bundle. The base specs
    ship in the image; the site's derived/extra specs and its reference-source
    catalogues sit beside them, not in a scope that never loads."""
    img = _mk(tmp_path / "img", {
        "envs/python_bio.yaml": "name: python-bio\n",
        "envs/r_bio.yaml": "name: r-bio\n"})
    site = _mk(tmp_path / "site", {
        "envs/python_bio_cuda.yaml": "name: python-bio-cuda\n",
        "knowhow/refsources/provider-a.yaml": "provider: a\n",
        "nextflow/site.config": "// site\n",
        "README.md": "ignored\n"})
    r = assemble(img, site, tmp_path / "dest")
    d = tmp_path / "dest"
    for rel in ("envs/python_bio.yaml", "envs/r_bio.yaml",
                "envs/python_bio_cuda.yaml",
                "knowhow/refsources/provider-a.yaml", "nextflow/site.config"):
        assert (d / rel).is_file(), f"{rel} missing from the assembled bundle"
    assert not (d / "README.md").exists(), "the overlay's own README is not content"
    assert len(r["from_image"]) == 2 and len(r["from_site"]) == 3


def test_the_site_overlays_the_image_and_says_so(tmp_path):
    """A site must be able to override a shipped spec — that is the whole point
    of a site-wide bundle — and the assembly must REPORT it, so an override is
    a visible decision rather than a silent divergence."""
    img = _mk(tmp_path / "img", {"envs/python_bio.yaml": "name: python-bio\nv: image\n"})
    site = _mk(tmp_path / "site", {"envs/python_bio.yaml": "name: python-bio\nv: site\n"})
    r = assemble(img, site, tmp_path / "dest")
    assert "v: site" in (tmp_path / "dest" / "envs/python_bio.yaml").read_text()
    assert r["overridden"] == ["envs/python_bio.yaml"]


def test_assembly_is_idempotent_and_drops_removed_files(tmp_path):
    """Rebuilt, never merged into: a spec deleted upstream must disappear here,
    or the bundle accretes stale packs that nothing declares any more."""
    img = _mk(tmp_path / "img", {"envs/a.yaml": "name: a\n", "envs/gone.yaml": "name: gone\n"})
    assemble(img, None, tmp_path / "dest")
    assert (tmp_path / "dest" / "envs/gone.yaml").exists()
    (img / "envs/gone.yaml").unlink()
    assemble(img, None, tmp_path / "dest")
    assert not (tmp_path / "dest" / "envs/gone.yaml").exists()
    assert (tmp_path / "dest" / "envs/a.yaml").exists()


def test_an_empty_or_missing_image_bundle_is_REFUSED(tmp_path):
    """WIDE — the degenerate source, and the dangerous one. Assembling from
    nothing yields a bundle with no base env packs, and every session then
    silently solves a private base instead of adopting the published image.
    'Nothing to copy' must never read as success."""
    missing = tmp_path / "does-not-exist"
    empty = _mk(tmp_path / "empty", {})
    assert empty.is_dir(), "the empty case must actually exist to be a case"
    for src in (missing, empty):
        try:
            assemble(src, None, tmp_path / "dest")
        except AssemblyError:
            continue
        raise AssertionError(f"assembling from {src} should have refused")


def test_no_site_overlay_is_fine(tmp_path):
    """A deployment with no site additions is normal, not an error."""
    img = _mk(tmp_path / "img", {"envs/a.yaml": "name: a\n"})
    r = assemble(img, None, tmp_path / "dest")
    assert (tmp_path / "dest" / "envs/a.yaml").is_file() and r["from_site"] == []


# ── the scope the bundle lands in ───────────────────────────────────────────

def _resolve(site_yaml: Path | None, env_extra: dict, tmp_path):
    from core.bundle.scope_resolver import resolve_scopes
    env = {"HOME": str(tmp_path / "home"), "USER": "tester",
           "ABA_HOME": str(tmp_path / "abahome")}
    env.update(env_extra)
    r = resolve_scopes(env=env, site_config_path=site_yaml, auto_create=False)
    return {s.name: s for s in r.scope_chain}


def test_site_yaml_decides_the_institution_path_when_nothing_overrides(tmp_path):
    """The deployment declares where its bundle lives. This is what the baked
    env var made impossible."""
    share = _mk(tmp_path / "share" / "installation", {"envs/a.yaml": "name: a\n"})
    sy = tmp_path / "site.yaml"
    sy.write_text(f"scopes:\n  institution:\n    bundle_path: {share}\n")
    inst = _resolve(sy, {}, tmp_path)["institution"]
    assert inst.path == share.resolve(), inst.path
    assert inst.present is True


def test_an_explicit_env_override_still_wins(tmp_path):
    """ARMED the other way: ABA_INSTITUTION_BUNDLE is a genuine operator
    override for debugging. The fix is that the IMAGE must not set it — not
    that the mechanism goes away."""
    share = _mk(tmp_path / "share" / "installation", {"envs/a.yaml": "name: a\n"})
    other = _mk(tmp_path / "other", {"envs/b.yaml": "name: b\n"})
    sy = tmp_path / "site.yaml"
    sy.write_text(f"scopes:\n  institution:\n    bundle_path: {share}\n")
    inst = _resolve(sy, {"ABA_INSTITUTION_BUNDLE": str(other)}, tmp_path)["institution"]
    assert inst.path == other.resolve(), inst.path


def test_without_a_site_config_it_falls_back_to_ABA_HOME(tmp_path):
    """The personal-install case must keep working untouched: no site.yaml,
    bundle at $ABA_HOME/installation."""
    home_bundle = _mk(tmp_path / "abahome" / "installation", {"envs/a.yaml": "name: a\n"})
    inst = _resolve(None, {}, tmp_path)["institution"]
    assert inst.path == home_bundle.resolve(), inst.path


def _standalone() -> int:
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    rc = 0
    for t in fns:
        try:
            t(Path(tempfile.mkdtemp()))
            print(f"  [PASS] {t.__name__}")
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(); print(f"  [FAIL] {t.__name__}: {e}"); rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(_standalone())
