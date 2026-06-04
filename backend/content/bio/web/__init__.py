"""Bio web routes — the entity-aware HTTP surface that lived inline in
backend/main.py until arch3.md Phase 8.

main.py mounts this package's `router` via `app.include_router(router)`;
all bio-shaped endpoints register against the same FastAPI app.
"""
from content.bio.web.routes import router  # noqa: F401
