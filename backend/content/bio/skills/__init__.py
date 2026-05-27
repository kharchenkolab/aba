"""Bio skills catalog (MVP — T2.5).

For now the skills catalog is a flat allowlist that the plan validator
consults to flag unknown skill references. The proper skill-file layout
(markdown + frontmatter, layered loader per inst_aba_base.md §4) is a
future initiative; this file is the stopgap.

When the skill loader ships, replace this file with a call to
load_skills(...).register_with_validator() and delete the inline list.
"""
from core.planning.validator import register_skill

_KNOWN_SKILLS = [
    "scrna-qc-clustering",          # the scanpy QC + clustering recipe
    "bulk-rnaseq-de",               # pydeseq2 differential expression
    "inspect-upload",               # opaque-file inspector
    "branch-from-figure",           # scenario / variant creation
    "register-artifact",            # add a produced file to the graph
    "summarize-thread",             # rolling-summary procedure
    "promote-result",               # turn a kept observation into a finding
    "compare-branches",             # baseline vs scenario diff
]

for _s in _KNOWN_SKILLS:
    register_skill(_s)
