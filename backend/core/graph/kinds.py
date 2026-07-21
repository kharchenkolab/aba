"""Well-known entity-kind names core code may reason about — the ONE
sanctioned crossing of the platform/content seam's literal ban.

Rule 2 of scripts/check_seam.sh bans content entity-type literals in
backend/core/ so the platform tier stays vocabulary-agnostic. A few core
subsystems legitimately special-case a handful of kinds (provenance
evidence, exec-record input capture, the data ledger). They import THESE
symbols instead of writing literals, so the check still fails loudly on
any new literal anywhere else in core/ — the crossing stays single,
documented, and greppable.

Do not grow this list casually: a new entry means core is learning more
content vocabulary, which is an architectural decision, not a convenience.
"""

DATASET = "dataset"    # noqa: seam
ANALYSIS = "analysis"  # noqa: seam
RESULT = "result"      # noqa: seam
FIGURE = "figure"      # noqa: seam
TABLE = "table"
