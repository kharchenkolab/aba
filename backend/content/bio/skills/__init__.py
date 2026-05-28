"""Bio skills registrar (code only).

The skill CONTENT (.md) lives in the content library — `content/bio/library/`
— kept separate from this package's code so authored content isn't scattered
through the source tree (and so it's ready to become a scope-overlay root
later). This module just registers those content dirs at import time: each
.md has frontmatter (name, description, when_to_use, capabilities_needed,
domain, …) and a body the agent reads on demand via `read_skill`.

Adding a skill is one .md file in the library — no code edit required."""
from pathlib import Path

from core.skills import register_skill_dir

# Content library root (sibling of the code packages under content/bio/).
_LIB = Path(__file__).parent.parent / "library"

# Curated skills + the biomni-distilled recipes — same format, separate dirs so
# generated content stays auditable/regenerable.
register_skill_dir(_LIB / "skills")
register_skill_dir(_LIB / "recipes")
