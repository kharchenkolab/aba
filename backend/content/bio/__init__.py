"""Bio content pack.

Importing this package wires up the bio-specific registrations:
  - per-entity-type focus card builders (bio/cards/)
  - the advisor specs + handlers (bio/advisors/)
  - skill names the plan validator references (bio/skills/catalog.py)
"""
from . import cards          # noqa: F401  — registers per-type card builders
from . import advisors       # noqa: F401  — loads YAML specs + handlers
from . import skills         # noqa: F401  — registers known skills
