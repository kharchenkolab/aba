"""Phase 2 of artifact-as-truth/preview redesign.

Confirms the harvester reclassifies a SINGLE-PAGE PDF as a figure (in
the `plots` bucket) while keeping multi-page PDFs in `files`. Other
non-PDF formats (HTML, SVG, RDS, …) continue to land in `files`.

Why this matters: a single-page PDF written by cairo_pdf / PdfPages
IS a figure conceptually, and the redesign demands it be pinnable + on
the Result page. The page-count check separates "figure exported as
PDF" from "multi-page report PDF" (the latter stays a download).

Run: .venv/bin/python tests/test_harvest_pdf_as_figure.py
"""
from __future__ import annotations
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_pdf_harvest_")
os.environ["ABA_DB_PATH"]   = str(Path(_tmp) / "p.db")
os.environ["ABA_RUNTIME_DIR"] = str(_tmp)
os.environ["ARTIFACTS_DIR"] = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]  = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]      = str(Path(_tmp) / "data")
os.environ["ABA_ENVS_DIR"]  = "/workspace/aba-runtime/envs"
sys.path.insert(0, str(ROOT / "backend"))

# Bring the project current so artifacts get a real artifacts dir
from core import projects
projects.set_current("prj_pdfharvest")

_failures: list[str] = []


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not cond:
        _failures.append(label)


def _make_pdf(path: Path, n_pages: int):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(str(path)) as pdf:
        for i in range(n_pages):
            fig = plt.figure(figsize=(3, 2))
            plt.plot([1, 2, 3], [i + 1, i + 2, i + 3])
            pdf.savefig(fig)
            plt.close(fig)


def _make_png(path: Path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.figure(); plt.plot([1, 2, 3], [1, 4, 9])
    plt.savefig(str(path)); plt.close("all")


def test_single_page_pdf_lands_in_plots():
    print("\n[1] single-page PDF → harvester puts it in `plots` (figure bucket)")
    from core.exec.run import harvest_artifacts
    scratch = Path(tempfile.mkdtemp(prefix="scratch_1_"))
    _make_pdf(scratch / "umap_export.pdf", n_pages=1)
    _make_png(scratch / "umap.png")  # sanity: PNGs still work
    plots, tables, files, warns = harvest_artifacts(scratch)
    plot_names = [p["original_name"] for p in plots]
    file_names = [f["original_name"] for f in files]
    check("PNG present in plots", "umap.png" in plot_names, f"plots={plot_names}")
    check("1-page PDF promoted to plots", "umap_export.pdf" in plot_names,
          f"plots={plot_names}")
    check("1-page PDF NOT in files", "umap_export.pdf" not in file_names,
          f"files={file_names}")


def test_multi_page_pdf_stays_in_files():
    print("\n[2] multi-page PDF (report) stays in `files`")
    from core.exec.run import harvest_artifacts
    scratch = Path(tempfile.mkdtemp(prefix="scratch_2_"))
    _make_pdf(scratch / "report.pdf", n_pages=3)
    plots, tables, files, warns = harvest_artifacts(scratch)
    plot_names = [p["original_name"] for p in plots]
    file_names = [f["original_name"] for f in files]
    check("3-page PDF in files", "report.pdf" in file_names, f"files={file_names}")
    check("3-page PDF NOT in plots", "report.pdf" not in plot_names,
          f"plots={plot_names}")


def test_corrupt_pdf_treated_as_file_not_figure():
    print("\n[3] corrupt PDF (unparseable) falls through to `files` (safe default)")
    from core.exec.run import harvest_artifacts
    scratch = Path(tempfile.mkdtemp(prefix="scratch_3_"))
    (scratch / "bad.pdf").write_text("not a pdf")
    plots, tables, files, warns = harvest_artifacts(scratch)
    plot_names = [p["original_name"] for p in plots]
    file_names = [f["original_name"] for f in files]
    check("corrupt PDF goes to files", "bad.pdf" in file_names, f"files={file_names}")
    check("corrupt PDF NOT in plots", "bad.pdf" not in plot_names, f"plots={plot_names}")


def test_other_file_types_unchanged():
    print("\n[4] HTML / SVG / RDS / etc. still go to `files` (not affected)")
    from core.exec.run import harvest_artifacts
    scratch = Path(tempfile.mkdtemp(prefix="scratch_4_"))
    (scratch / "viz.html").write_text("<html><body>x</body></html>")
    (scratch / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    (scratch / "data.rds").write_bytes(b"\x1f\x8b\x08RDS-stub")
    plots, tables, files, warns = harvest_artifacts(scratch)
    file_names = {f["original_name"] for f in files}
    check("HTML in files", "viz.html" in file_names, f"files={file_names}")
    check("SVG in files", "icon.svg" in file_names, f"files={file_names}")
    check("RDS in files", "data.rds" in file_names, f"files={file_names}")
    plot_names = {p["original_name"] for p in plots}
    check("none of HTML/SVG/RDS in plots", not (file_names & plot_names),
          f"plots={plot_names}")


def test_pdf_plot_entry_carries_preview_url():
    """The chat <img src=...> can't render a PDF directly (browsers show
    a broken-image icon). When the harvester promotes a PDF to plots[],
    it must also annotate the entry with a preview_url pointing at the
    rasterized .preview.png so frontend rendering has something the
    browser can actually display. Regression guard for the
    chat-broken-icon bug (2026-06-07)."""
    print("\n[5] PDF plot entry carries a preview_url for the rasterized .preview.png")
    from core.exec.run import harvest_artifacts
    scratch = Path(tempfile.mkdtemp(prefix="scratch_5_"))
    _make_pdf(scratch / "fig.pdf", n_pages=1)
    plots, _, _, _ = harvest_artifacts(scratch)
    check("PDF promoted to plots", len(plots) == 1, f"got plots={plots}")
    if plots:
        p = plots[0]
        check("plot has preview_url", isinstance(p.get("preview_url"), str) and bool(p.get("preview_url")),
              f"got preview_url={p.get('preview_url')!r}")
        if p.get("preview_url"):
            check("preview_url ends in .preview.png",
                  p["preview_url"].endswith(".preview.png"),
                  f"got {p['preview_url']!r}")
            check("preview_url differs from canonical url",
                  p["preview_url"] != p.get("url"),
                  f"both = {p['preview_url']!r}")


def test_png_plot_entry_has_no_preview_url():
    print("\n[6] PNG plot has NO preview_url (preview = canonical for native rasters)")
    from core.exec.run import harvest_artifacts
    scratch = Path(tempfile.mkdtemp(prefix="scratch_6_"))
    _make_png(scratch / "p.png")
    plots, _, _, _ = harvest_artifacts(scratch)
    check("PNG in plots", len(plots) == 1)
    if plots:
        check("preview_url absent for PNG",
              "preview_url" not in plots[0] or not plots[0]["preview_url"],
              f"got {plots[0]!r}")


def main() -> int:
    test_single_page_pdf_lands_in_plots()
    test_multi_page_pdf_stays_in_files()
    test_corrupt_pdf_treated_as_file_not_figure()
    test_other_file_types_unchanged()
    test_pdf_plot_entry_carries_preview_url()
    test_png_plot_entry_has_no_preview_url()
    print()
    if _failures:
        print(f"FAILED: {len(_failures)} check(s):")
        for f in _failures: print(f"  - {f}")
        return 1
    print("ALL PDF-HARVEST-AS-FIGURE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
