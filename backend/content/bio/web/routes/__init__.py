"""Bio HTTP routes — per-entity sub-modules composed into one router.

Wave 1 Track B (misc/refactoring2.md §6.2): the 1576-LOC routes.py
monolith was split along entity-type lines. Each sub-module owns its
own `APIRouter()`; this package's top-level `router` aggregates them so
backend/main.py's `app.include_router(router)` keeps working without
any caller-side changes.

The original test gates assume a single file path — they're updated
to walk every `*.py` in this package (test_project_pinning_coverage.py)
or scan the package re-export (existing `from content.bio.web.routes
import router` imports go through this __init__).

Order of include_router calls is unrelated to FastAPI route resolution
— FastAPI dispatches by method + path. Listed below in roughly the
order the original monolith introduced each section.
"""
from __future__ import annotations

from fastapi import APIRouter

from .claims import router as _claims_router
from .results import router as _results_router
from .findings import router as _findings_router
from .runs import router as _runs_router
from .revisions import router as _revisions_router
from .datasets import router as _datasets_router
from .proposals import router as _proposals_router
from .advisors import router as _advisors_router
from .threads_bio import router as _threads_bio_router
from .misc import router as _misc_router


router = APIRouter()

router.include_router(_claims_router)
router.include_router(_results_router)
router.include_router(_findings_router)
router.include_router(_runs_router)
router.include_router(_revisions_router)
router.include_router(_datasets_router)
router.include_router(_proposals_router)
router.include_router(_advisors_router)
router.include_router(_threads_bio_router)
router.include_router(_misc_router)


__all__ = ["router"]
