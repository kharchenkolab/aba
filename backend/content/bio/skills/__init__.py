"""Bio skills registrar (code only).

The skill CONTENT (.md) lives in the content library — `content/bio/library/`
— kept separate from this package's code so authored content isn't scattered
through the source tree.

**Layered content (L-A, misc/content_layers.md).** This module walks N
content roots lowest-to-highest precedence:

  1. The platform-shipped *system* layer at `content/bio/library/`.
  2. Zero or more *overlay* layers declared in deployment.yaml — the
     standard cookbook (`aba-recipes`) is the typical first overlay;
     an `institution` overlay is an optional further layer.

On name collision the higher (later-registered) layer wins, because
the loader does `_REGISTRY[spec.name] = spec` last-write-wins. Aliases
work the same way — an overlay can hijack a base name by declaring
`aliases: [base-name]` in its own recipe's frontmatter.

Each spec is stamped with `.layer` so `/api/admin/refresh-skills` can
say "+3 from aba-recipes" and the (i) drawer can show provenance.

Adding a skill is one .md file in the library — no code edit required:
drop it in core/ to make it always-visible, or recipes/<domain>/ to
make it a searchable recipe."""
from pathlib import Path

from core.skills import register_skill_dir
from core.config_layers import ContentLayer, load_content_layers

# Content library root (sibling of the code packages under content/bio/).
_LIB = Path(__file__).parent.parent / "library"

# Three tiers, by visibility (stamped from the folder, never per-file):
#   core/             — always rendered in the system prompt (operating + meta
#                       skills); small, hand-curated.
#   recipes/<domain>/ — the domain cookbook, retrieval-gated via search_skills;
#                       the <domain> subfolder supplies each recipe's facet.
#   vendor_skills/    — third-party skill folders bundled with installed
#                       packages (e.g. pagoda2's `inst/skill/` shape). Each
#                       entry is `<vendor_name>/SKILL.md + references/...`
#                       — usually a symlink into a git clone under
#                       `backend/vendor/<package>/skill`. Same tier as recipes.
# A generated recipe lands under recipes/ and therefore can never promote itself
# into the always-on prompt tier.


def _layer_roots() -> list[tuple[Path, str]]:
    """Resolved list of (root_path, layer_name) tuples, lowest-to-highest
    precedence. The system layer is always first. Overlays come from
    deployment.yaml; a configured layer whose path doesn't exist on disk
    is skipped silently (so a bootstrap script that hasn't run yet
    doesn't break server startup)."""
    out: list[tuple[Path, str]] = [(_LIB, "system")]
    for layer in load_content_layers():
        if not layer.exists():
            print(f"[skills] layer {layer.name!r} configured but path "
                  f"{layer.path!s} doesn't exist; skipping", flush=True)
            continue
        out.append((layer.path, layer.name))
    return out


def register_all_layers() -> dict[str, dict[str, int]]:
    """Register every configured layer's skills. Returns a {layer:
    {core/recipes/vendor: count}} dict for diagnostics (used by
    /api/admin/refresh-skills and startup logging)."""
    counts: dict[str, dict[str, int]] = {}
    for root, name in _layer_roots():
        # Some overlays may use "vendor" instead of "vendor_skills" as the
        # subfolder name — accept both. (L-B's aba-recipes layout uses
        # "vendor"; the system layer historically uses "vendor_skills".)
        vendor_root = (root / "vendor_skills") if (root / "vendor_skills").exists() else (root / "vendor")
        n_core    = register_skill_dir(root / "core",    visibility="always", layer=name)
        n_recipes = register_skill_dir(root / "recipes", visibility="local",  layer=name)
        n_vendor  = register_skill_dir(vendor_root,      visibility="local",  layer=name)
        counts[name] = {"core": n_core, "recipes": n_recipes, "vendor": n_vendor}
        print(f"[skills] layer={name!r}: core={n_core} recipes={n_recipes} "
              f"vendor={n_vendor} ({root})", flush=True)
    return counts


# Register on import (preserves today's startup behaviour for a bare
# install with no overlays configured: just the system layer loads).
register_all_layers()
