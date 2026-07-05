"""Domain-neutral HTTP routers — the platform half of the route surface,
extracted from main.py (Item 2A.3). Each module owns an `APIRouter()` named
`router`; main.py mounts them via `app.include_router(...)`.

Bio-aware routes live in content/bio/web/routes/ instead. The Reasoning-plane
entries (/api/chat, /api/turns/*/resume, /tool_result) stay at the composition
root because they import guide.stream_response, which core/ must not import
(check_seam rule 4).
"""
