"""Phase 3 runner — discovery + Skill dispatch.

Reuses run_phase() from p1_run.py; only the scenario set differs.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from scripts.p1_run import run_phase                            # noqa: E402
from tests.scenarios.p3 import P3_SCENARIOS                     # noqa: E402

if __name__ == "__main__":
    sys.exit(run_phase("Phase 3 — discovery (search_skills → Skill)",
                       P3_SCENARIOS))
