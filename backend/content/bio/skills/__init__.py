"""Bio skills registrar (code only).

The skill CONTENT (.md) lives in the content library — `content/bio/library/`
— kept separate from this package's code so authored content isn't scattered
through the source tree (and so it's ready to become a scope-overlay root
later). This module just registers those content dirs at import time: each
.md has frontmatter (name, description, when_to_use, capabilities_needed,
domain, …) and a body the agent reads on demand via `read_skill`.

Adding a skill is one .md file in the library — no code edit required: drop it
in core/ to make it always-visible, or recipes/<domain>/ to make it a searchable
recipe."""
from pathlib import Path

from core.skills import register_skill_dir

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
register_skill_dir(_LIB / "core", visibility="always")
register_skill_dir(_LIB / "recipes", visibility="local")
register_skill_dir(_LIB / "vendor_skills", visibility="local")
