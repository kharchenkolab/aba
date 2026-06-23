"""Shared test fixture: supply the bio capability catalog as an INSTALLATION
scope, the way the recipe pack supplies it in production.

Catalog content (capability seeds + the R-base manifest) is pack-sourced — it
lives in aba-recipe-pack and is imported into the installation scope, NOT
vendored in the backend. So tests that exercise the capability catalog point
ABA_INSTITUTION_BUNDLE at this fixture (the pack's seed catalog as test data,
under tests/fixtures/). The biomni reference collection is intentionally
excluded (it was dropped).

Call install() at the TOP of a test module — before importing core/content — so
the bundle composes it on first load (get_bundle() caches at first access).
"""
from __future__ import annotations
import os
from pathlib import Path

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "installation"


def install() -> str:
    """Point the installation scope at the fixture catalog. Returns the path."""
    os.environ["ABA_INSTITUTION_BUNDLE"] = str(FIXTURE)
    return str(FIXTURE)
