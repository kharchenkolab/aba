"""Shared raster-preview helper for non-raster artifacts (PDF first; SVG
and other formats can plug in here as the need arises).

WHY: a figure can be PNG, PDF, SVG, etc. The UI's `<img>` only renders
rasters, so non-raster artifacts need a derived PNG to display
faithfully. We always RASTERIZE FROM THE CANONICAL artifact (rather
than re-rendering from code) so the preview cannot drift from the
download — what the user sees in the panel is exactly what they get
when they click "open PDF".

WHO USES THIS:
  - `lifecycle/artifacts.materialize_entity_from_artifact` —
    populate `metadata.preview_path` at pin time.
  - `lifecycle/revisions.make_revision` — same, for revisions.
  - `lifecycle/runs.refresh_output_manifest` — Run-view plots grid
    (already used the inlined helper before this refactor).

DESIGN:
  - One function: `ensure_preview(artifact_url_or_path) -> Optional[str]`.
  - Returns the preview URL ("/artifacts/.../<name>.thumb.png") for
    artifacts that need one; returns `None` for artifacts where the
    canonical IS already a raster (PNG/JPG/GIF/WebP) — caller falls
    back to artifact_path in that case.
  - Idempotent: the rasterized preview is a sibling file
    `<canonical>.thumb.png`, regenerated only when the canonical is
    newer than the cached preview (mtime check).
  - Best-effort: a corrupt PDF / missing pypdfium2 / write failure
    returns None rather than raising. Pin/revise flows must tolerate
    a missing preview.

NOT YET HANDLED: SVG (browser can render natively in many cases; we
may add an explicit rasterization pass later for chat-thumbnail use).
HTML reports stay as files; they are not figures.
"""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import quote

_log = logging.getLogger(__name__)


# Extensions that browsers display natively — no preview needed; the
# canonical URL IS the display URL.
_NATIVE_RASTER = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
# Extensions for which we KNOW how to rasterize. SVG is intentionally
# excluded for now (renders natively in most browsers; rasterizer can
# be added later if we need a thumbnail elsewhere).
_RASTERIZABLE = {".pdf"}

# Sibling cache filename for rasterized previews. Exported as a
# constant so call sites that construct preview URLs by hand (e.g.
# the Run-view manifest in `lifecycle/runs.refresh_output_manifest`)
# stay in lockstep — change once, everywhere agrees.
#
# The "v2" semantic: this used to be ".thumb.png" at ~600px wide,
# which looked acceptable on the panel but blurry in the click-to-
# enlarge lightbox. Renaming the cache file (rather than just bumping
# resolution) sidesteps cache-invalidation pain — old .thumb.png
# files stay on disk as harmless orphans until normal GC reaps them.
PREVIEW_SUFFIX = ".preview.png"

# Rasterization quality. We aim for ~200 dpi (≈ 2× typical monitor
# pixel density) so the preview looks crisp at lightbox-full-screen
# without being preposterously large. The pixel cap stops poster-
# sized PDFs (30+ inches wide) from generating 6000+ px images.
_TARGET_DPI = 200
_MAX_PX_WIDE = 3000


def _url_to_disk(url_or_path: str) -> Optional[Path]:
    """Resolve an `/artifacts/...` URL or a bare disk path to an
    absolute Path. Returns None if the URL shape doesn't match or the
    path escapes a project boundary. Mirrors `main._artifact_url_to_path`
    deliberately (kept local to avoid a backend → core import)."""
    if not url_or_path:
        return None
    if url_or_path.startswith("/artifacts/"):
        parts = url_or_path[len("/artifacts/"):].split("/")
        if (len(parts) == 2 and parts[0] and parts[1]
                and ".." not in parts[0] and ".." not in parts[1]):
            from core.config import project_artifacts_dir
            return project_artifacts_dir(parts[0]) / parts[1]
        if len(parts) == 1 and parts[0] and ".." not in parts[0]:
            # Legacy workspace-level fallback: /artifacts/<name>
            from core.config import ARTIFACTS_DIR
            return ARTIFACTS_DIR / parts[0]
        return None
    # Treat anything else as a disk path — used by callers that
    # generate previews on raw scratch files before they're addressable.
    return Path(url_or_path)


def _disk_to_url(disk_path: Path) -> Optional[str]:
    """Inverse of `_url_to_disk` for files that live under the project
    artifacts dir. Returns None if the path doesn't sit in a known
    artifacts root (callers must handle that)."""
    try:
        from core.config import project_artifacts_dir, ARTIFACTS_DIR
    except Exception:  # noqa: BLE001
        return None
    p = disk_path.resolve()
    # Per-project: /artifacts/<pid>/<name>
    # We don't know the pid up front; check whether the path is two
    # levels deep under any project artifacts root by walking up.
    for ancestor in (p.parent, p.parent.parent):
        try:
            pid = ancestor.name
            root = project_artifacts_dir(pid)
            if root.resolve() == p.parent.resolve():
                return f"/artifacts/{pid}/{quote(p.name)}"
        except Exception:  # noqa: BLE001
            continue
    # Legacy workspace-level fallback: <ARTIFACTS_DIR>/<name>
    try:
        if p.parent.resolve() == Path(ARTIFACTS_DIR).resolve():
            return f"/artifacts/{quote(p.name)}"
    except Exception:  # noqa: BLE001
        pass
    return None


def _preview_disk_path(canonical_disk: Path) -> Path:
    """Sibling cache file for the rasterized preview. Idempotent +
    mtime-checked so a regenerated PDF refreshes its preview on next
    use. The suffix comes from PREVIEW_SUFFIX so external callers
    that build URLs by hand can reference the same constant."""
    return canonical_disk.with_suffix(canonical_disk.suffix + PREVIEW_SUFFIX)


def _rasterize_pdf(pdf_disk: Path) -> bool:
    """Render PDF page 1 to a sibling preview PNG. Returns True on
    success, False on any failure (missing pypdfium2, corrupt PDF,
    empty document). Never raises.

    Sized for two roles at once: the panel thumbnail (CSS downscales
    via `max-width: 100%` on `.rv-panel__img`) AND the click-to-
    enlarge lightbox (shown at native size or up-scaled to viewport).
    A single high-resolution raster serves both — keeps the cache
    simple and the lightbox sharp.
    """
    try:
        thumb = _preview_disk_path(pdf_disk)
        if thumb.exists() and thumb.stat().st_mtime >= pdf_disk.stat().st_mtime:
            return True
        import pypdfium2 as pdfium  # type: ignore[import-not-found]
        doc = pdfium.PdfDocument(str(pdf_disk))
        if len(doc) == 0:
            return False
        page = doc[0]
        # Two scale ceilings, lower wins:
        #   - DPI-driven: target dpi / 72 (uniform render quality
        #     regardless of page size — a 7-inch fig at 200 DPI
        #     ≈ 1400 px wide)
        #   - max-pixel cap: max_px / page_width_pt (so a
        #     30-inch poster doesn't generate a 6000 px monster)
        # A floor at 0.5 keeps the scale sane for pathologically
        # tiny pages.
        page_w_pt = max(50, page.get_width())
        scale = max(0.5, min(_TARGET_DPI / 72, _MAX_PX_WIDE / page_w_pt))
        bitmap = page.render(scale=scale)
        bitmap.to_pil().save(thumb, "PNG", optimize=True)
        return True
    except Exception as e:  # noqa: BLE001 — best-effort
        _log.warning("preview rasterize failed for %s: %s", pdf_disk, e)
        return False


def ensure_preview(artifact_url_or_path: str) -> Optional[str]:
    """Return a browser-displayable preview URL for `artifact_url_or_path`.

    Returns:
      - `None` when the canonical is already a raster the browser can
        render directly (PNG/JPG/GIF/WebP). Caller should fall back
        to the canonical URL in that case.
      - `None` when rasterization isn't supported for this format
        (e.g. SVG today, HTML, anything not in `_RASTERIZABLE`).
      - `None` when rasterization was attempted but failed (corrupt
        PDF, missing dependency, disk error). Caller should still
        show the download affordance but `<img>` will be broken.
      - A URL string ("/artifacts/<pid>/<name>.thumb.png") when a
        preview was generated (or was already cached and is fresh).

    The function is idempotent and cheap on the cache hit — only a
    stat() call. Safe to invoke on every page load.
    """
    if not artifact_url_or_path:
        return None
    suf = Path(artifact_url_or_path).suffix.lower()
    if suf in _NATIVE_RASTER:
        return None
    if suf not in _RASTERIZABLE:
        return None
    disk = _url_to_disk(artifact_url_or_path)
    if disk is None or not disk.exists():
        return None
    if suf == ".pdf":
        if not _rasterize_pdf(disk):
            return None
    thumb_disk = _preview_disk_path(disk)
    # If the canonical lived under a recognizable artifacts root, return
    # the canonical's URL with .thumb.png appended. Otherwise return the
    # disk URL form — callers serving from non-/artifacts roots will
    # need to map it; the only such caller today (runs.py) does its own
    # URL minting through the /api/runs/.../file endpoint.
    if artifact_url_or_path.startswith("/artifacts/"):
        return artifact_url_or_path + PREVIEW_SUFFIX
    derived = _disk_to_url(thumb_disk)
    return derived  # may be None — caller decides how to surface


# ── For runs.py — a thin shim that preserves the previous return
# contract (`True`/`False`) so the existing call site doesn't need to
# change while still going through the canonical helper. The disk-path
# variant is what runs.py wants (it computes its own URL via the
# /api/runs/.../file endpoint, not the /artifacts/ URL scheme).
def ensure_pdf_thumb_for_disk(pdf_disk: Path) -> bool:
    """Run-view helper. Generates the sibling .thumb.png; returns success."""
    return _rasterize_pdf(Path(pdf_disk))
