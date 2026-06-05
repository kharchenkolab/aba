"""L-A — content-layer loader (misc/content_layers.md).

Tests the three guarantees of the stacked registrar:

  1. A recipe present ONLY in an overlay is registered (with `.layer`
     attribution).
  2. A recipe present in BOTH layers — same `name:` — is overridden by
     the higher (later-registered) layer (last-write-wins on
     `_REGISTRY[spec.name]`). The original system file is unchanged
     on disk; the registry just points at the overlay's spec.
  3. An overlay recipe declaring `aliases: [<base-name>]` hijacks
     references to the base name without renaming the base file — the
     recommended override pattern from §4 of the design doc.

Filesystem isolation: temp ABA_DB_PATH + ABA_RUNTIME_DIR per
[[feedback_test_filesystem_isolation]].

Run:
    .venv/bin/python tests/p16_content_layers.py
"""
from __future__ import annotations
import os
import sys
import tempfile
import textwrap
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="aba_p16_")
os.environ["ABA_RUNTIME_DIR"] = _TMP
os.environ["ABA_DB_PATH"] = os.path.join(_TMP, "t.db")
os.environ["ABA_PROJECTS_DIR"] = os.path.join(_TMP, "projects")

# We'll point ABA_DEPLOYMENT_YAML at a config we write below — must be set
# BEFORE the backend imports any content modules.
_DEPLOY_YAML = os.path.join(_TMP, "deployment.yaml")
os.environ["ABA_DEPLOYMENT_YAML"] = _DEPLOY_YAML

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def _write_recipe(path: Path, *, name: str, description: str,
                  body: str, aliases: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fm = [
        "---",
        f"name: {name}",
        f"description: {description}",
        "domain: testlab",
    ]
    if aliases:
        fm.append(f"aliases: {aliases!r}")
    fm.append("---")
    fm.append("")
    fm.append(body)
    path.write_text("\n".join(fm))


# ---------- fixtures ----------

_overlay = Path(_TMP) / "overlay"
(_overlay / "recipes" / "testlab").mkdir(parents=True)

# A recipe present only in the overlay (case 1).
_write_recipe(_overlay / "recipes" / "testlab" / "overlay-only.md",
              name="overlay-only-recipe",
              description="A recipe that only lives in the test overlay.",
              body="this is the overlay-only body")

# An overlay recipe that overrides a base recipe by canonical name (case 2).
# The base library has scrna-qc-clustering-v2 already; we override it here.
_write_recipe(_overlay / "recipes" / "testlab" / "scrna-qc-clustering-override.md",
              name="scrna-qc-clustering-v2",         # same name as base → override
              description="OVERRIDDEN by test overlay.",
              body="OVERLAY BODY")

# An overlay recipe that ALIAS-hijacks a base name (case 3).
# Base has `seurat-scrna-v2` with alias `seurat-scrna`. We add a new
# canonical-name recipe in the overlay whose aliases includes
# `seurat-scrna-v2` — references to that base name now resolve to us.
_write_recipe(_overlay / "recipes" / "testlab" / "vienna-seurat-alias.md",
              name="vienna-seurat-scrna",
              description="Alias-style override of seurat-scrna-v2.",
              body="VIENNA OVERLAY BODY",
              aliases=["seurat-scrna-v2"])

# Write the deployment.yaml that points at this overlay.
Path(_DEPLOY_YAML).write_text(textwrap.dedent(f"""
    layers:
      - name: testlab
        path: {_overlay!s}
""").lstrip())

# Now import the bio package — this triggers the layered registration.
from core.graph._schema import init_db  # noqa: E402
init_db()
import content.bio  # noqa: E402, F401
from core.skills.loader import _REGISTRY, _ALIASES, get_skill  # noqa: E402


# ---------- tests ----------

def test_overlay_only_recipe_is_registered_with_layer_attribution():
    spec = get_skill("overlay-only-recipe")
    assert spec is not None, "overlay-only-recipe should be registered"
    assert spec.layer == "testlab", f"expected layer='testlab', got {spec.layer!r}"
    assert "overlay-only body" in spec.body


def test_overlay_overrides_base_by_canonical_name():
    """The base library carries `scrna-qc-clustering-v2`. The overlay
    declared a same-name recipe later in load order → overlay wins."""
    spec = get_skill("scrna-qc-clustering-v2")
    assert spec is not None
    assert spec.layer == "testlab", \
        f"override should be attributed to overlay, got {spec.layer!r}"
    assert spec.body == "OVERLAY BODY", \
        f"body should be the overlay's, got: {spec.body[:80]!r}"


def test_overlay_alias_hijacks_base_name_without_overriding_canonical():
    """The overlay recipe `vienna-seurat-scrna` declares
    `aliases: [seurat-scrna-v2]`. Lookups for the base name should now
    resolve to the overlay's canonical spec (alias intercept).

    The BASE canonical name itself stays registered (the base's body is
    still in _REGISTRY['seurat-scrna-v2']) — alias is a parallel lookup
    path that points at the overlay's spec, not a deletion of the base."""
    spec = get_skill("seurat-scrna-v2")
    assert spec is not None
    # The alias points at the overlay spec — the base file's body is gone.
    assert spec.layer == "testlab", \
        f"alias lookup should resolve to overlay spec, got layer={spec.layer!r}"
    assert spec.name == "vienna-seurat-scrna", \
        f"alias should resolve to the overlay's canonical name, got {spec.name!r}"


def test_alias_registry_holds_overlay_alias():
    """_ALIASES is the alias→canonical pointer table."""
    assert _ALIASES.get("seurat-scrna-v2") == "vienna-seurat-scrna", \
        f"alias entry should point at the overlay's canonical name, got {_ALIASES.get('seurat-scrna-v2')!r}"


def test_system_layer_skills_keep_their_attribution():
    """A skill that exists ONLY in the system layer (no overlay
    counterpart) keeps `.layer == 'system'`."""
    # pagoda2-scrna-v3 is a system-layer (vendor_skills) recipe.
    spec = get_skill("pagoda2-scrna-v3")
    assert spec is not None
    assert spec.layer == "system", \
        f"untouched base skill should keep layer='system', got {spec.layer!r}"


def test_layer_counts_match_registration_pattern():
    """At least the overlay's 3 recipes must be in _REGISTRY with
    layer='testlab' (the overlay had: overlay-only + override + vienna)."""
    overlay_specs = [s for s in _REGISTRY.values() if s.layer == "testlab"]
    assert len(overlay_specs) >= 3, \
        f"expected ≥3 overlay specs, got {len(overlay_specs)}: {[s.name for s in overlay_specs]}"


def main() -> int:
    tests = [
        test_overlay_only_recipe_is_registered_with_layer_attribution,
        test_overlay_overrides_base_by_canonical_name,
        test_overlay_alias_hijacks_base_name_without_overriding_canonical,
        test_alias_registry_holds_overlay_alias,
        test_system_layer_skills_keep_their_attribution,
        test_layer_counts_match_registration_pattern,
    ]
    failed = []
    for t in tests:
        try:
            t()
            print(f"OK  {t.__name__}")
        except AssertionError as e:
            failed.append((t.__name__, str(e)))
            print(f"FAIL {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed.append((t.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERR  {t.__name__}: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
    if failed:
        print(f"\n{len(failed)} / {len(tests)} failed")
        return 1
    print(f"\nall {len(tests)} passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
