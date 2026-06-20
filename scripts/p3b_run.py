"""Phase 3b runner — same prompts as P3, but the model can pick
fetch_recipe (if exposed). Requires the live server bounced with
ABA_EXPERIMENTAL_FETCH_RECIPE=1 so the tool is in the catalog.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from scripts.p1_run import run_phase                            # noqa: E402
from tests.scenarios.p3b import P3B_SCENARIOS                   # noqa: E402

if __name__ == "__main__":
    sys.exit(run_phase(
        "Phase 3b — discovery via fetch_recipe (or search→Skill)",
        P3B_SCENARIOS))
