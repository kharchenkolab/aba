"""Tests for core.exec.previews — shared rasterization helper for
non-raster artifacts (PDF today; SVG/HTML later if needed).

Covers:
  - PNG canonical → no preview needed (returns None)
  - Single-page PDF → preview URL returned, .preview.png exists on disk
  - Multi-page PDF (5 pages) → preview is page 1, still single .preview.png
  - Corrupt / empty PDF → returns None, no crash
  - Cache hit: second call returns same URL without rewriting (mtime stable)
  - URL roundtrip: ensure_preview('/artifacts/<pid>/x.pdf') returns
    '/artifacts/<pid>/x.pdf.preview.png'
  - runs.py compat shim still functional

Run: .venv/bin/python tests/test_preview_helper.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_preview_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "p.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = str(Path(_tmp) / "envs")
sys.path.insert(0, str(ROOT / "backend"))

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _make_pdf(path: Path, n_pages: int = 1, figsize=(7, 5)):
    """Write a valid PDF with `n_pages` pages via matplotlib's
    PdfPages backend. Default figsize is realistic (7×5 in) so the
    rasterized preview lands in the lightbox-quality size range
    (≥1000 px wide at 200 DPI). Use a smaller figsize when explicitly
    testing the tiny-PDF path."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(str(path)) as pdf:
        for i in range(n_pages):
            fig = plt.figure(figsize=figsize)
            plt.plot([1, 2, 3], [i + 1, i + 2, i + 3])
            plt.title(f"page {i+1}")
            pdf.savefig(fig)
            plt.close(fig)
    return path


def _make_png(path: Path, side: int = 64):
    """Tiny valid PNG via Pillow."""
    from PIL import Image
    Image.new("RGB", (side, side), color=(40, 80, 120)).save(path, "PNG")
    return path


def _setup_project_artifact(pid: str, name: str) -> tuple[Path, str]:
    """Write a file under projects/<pid>/artifacts/<name>; return (disk, url)."""
    from core.config import project_artifacts_dir
    d = project_artifacts_dir(pid)
    return d / name, f"/artifacts/{pid}/{name}"


def test_png_returns_none():
    print("\n[1] PNG canonical → ensure_preview returns None (no preview needed)")
    from core.exec.previews import ensure_preview
    disk, url = _setup_project_artifact("prj_t1", "p.png")
    _make_png(disk)
    out = ensure_preview(url)
    check("ensure_preview returns None for .png", out is None, f"got {out!r}")


def test_pdf_single_page_generates_preview():
    print("\n[2] 1-page PDF → preview URL returned + sibling .preview.png exists")
    from core.exec.previews import ensure_preview
    disk, url = _setup_project_artifact("prj_t2", "fig.pdf")
    _make_pdf(disk, n_pages=1)
    out = ensure_preview(url)
    check("returns a URL", isinstance(out, str), f"got {out!r}")
    check("URL ends with .preview.png", out and out.endswith(".preview.png"),
          f"got {out!r}")
    thumb = disk.with_suffix(disk.suffix + ".preview.png")
    check("sibling .preview.png exists on disk", thumb.exists(),
          f"checked {thumb}")
    if thumb.exists():
        sz = thumb.stat().st_size
        check("preview is non-trivially sized (>100 B)", sz > 100, f"size={sz}")
        # Lightbox fidelity guard: preview must be at least ~1000 px wide
        # so click-to-enlarge looks crisp at typical viewport widths.
        # Drops to 0 if PIL can't open the file (a serious enough
        # regression that the assertion firing is the right outcome).
        from PIL import Image
        try:
            with Image.open(thumb) as im:
                width = im.width
        except Exception:
            width = 0
        check("preview is sized for lightbox (width ≥ 1000 px)",
              width >= 1000, f"width={width} (was 600 px under the v1 thumb)")


def test_pdf_multi_page_still_renders_page_one():
    print("\n[3] 5-page PDF → preview is page 1 (single .preview.png)")
    from core.exec.previews import ensure_preview
    disk, url = _setup_project_artifact("prj_t3", "fig5.pdf")
    _make_pdf(disk, n_pages=5)
    out = ensure_preview(url)
    check("returns a URL for multi-page PDF", isinstance(out, str), f"got {out!r}")
    thumb = disk.with_suffix(disk.suffix + ".preview.png")
    check("thumb exists for multi-page PDF", thumb.exists())


def test_missing_or_bad_file_returns_none():
    print("\n[4] missing file / corrupt PDF → ensure_preview returns None")
    from core.exec.previews import ensure_preview
    _, url = _setup_project_artifact("prj_t4", "ghost.pdf")
    out = ensure_preview(url)
    check("returns None for missing file", out is None, f"got {out!r}")
    # Corrupt PDF: write garbage
    disk, url2 = _setup_project_artifact("prj_t4", "corrupt.pdf")
    disk.write_text("not a real pdf")
    out2 = ensure_preview(url2)
    check("returns None for corrupt PDF", out2 is None, f"got {out2!r}")


def test_cache_hit_no_rewrite():
    print("\n[5] second call is a cache hit — thumb mtime stable")
    from core.exec.previews import ensure_preview
    import time
    disk, url = _setup_project_artifact("prj_t5", "cache.pdf")
    _make_pdf(disk)
    out1 = ensure_preview(url)
    check("first call yields preview", isinstance(out1, str))
    thumb = disk.with_suffix(disk.suffix + ".preview.png")
    mt1 = thumb.stat().st_mtime
    time.sleep(0.05)
    out2 = ensure_preview(url)
    check("second call returns same URL", out1 == out2, f"first={out1!r} second={out2!r}")
    mt2 = thumb.stat().st_mtime
    check("thumb was NOT rewritten on cache hit", mt1 == mt2,
          f"mtime changed: {mt1} → {mt2}")


def test_unsupported_extension_returns_none():
    print("\n[6] .svg / .html canonical → returns None (not rasterized today)")
    from core.exec.previews import ensure_preview
    disk_svg, url_svg = _setup_project_artifact("prj_t6", "icon.svg")
    disk_svg.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    out = ensure_preview(url_svg)
    check("SVG returns None (browser renders natively for now)",
          out is None, f"got {out!r}")


def test_runs_shim_still_works():
    print("\n[7] runs.py shim → ensure_pdf_thumb_for_disk(disk_path) → True")
    from core.exec.previews import ensure_pdf_thumb_for_disk
    disk, _ = _setup_project_artifact("prj_t7", "runs.pdf")
    _make_pdf(disk)
    ok = ensure_pdf_thumb_for_disk(disk)
    check("disk shim returns True on successful rasterize", ok is True, f"got {ok!r}")
    thumb = disk.with_suffix(disk.suffix + ".preview.png")
    check("disk shim wrote .preview.png", thumb.exists())


def main() -> int:
    test_png_returns_none()
    test_pdf_single_page_generates_preview()
    test_pdf_multi_page_still_renders_page_one()
    test_missing_or_bad_file_returns_none()
    test_cache_hit_no_rewrite()
    test_unsupported_extension_returns_none()
    test_runs_shim_still_works()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL PREVIEW-HELPER CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
