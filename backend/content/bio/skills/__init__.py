"""Bio skills package — markdown files in this directory are the catalog.

B2: replaces the prior hardcoded stopgap. Each .md has frontmatter
(name, description, when_to_use, requires_tools, produces) and a body
the agent can read on demand via the `read_skill` tool.

Adding a new skill is one .md file — no code edit required."""
from pathlib import Path

from core.skills import register_skill_dir

_HERE = Path(__file__).parent

# Import-time side effect: walk this dir, register every .md found,
# feed the plan validator's known-skill catalog.
register_skill_dir(_HERE)
