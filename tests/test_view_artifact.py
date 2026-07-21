"""view_artifact MCP tool — vision/text inspection of project artifacts.

The agent can now LOOK at an artifact (image, PDF page, table head, or
short text doc). For images and PDFs the result carries a multimodal
content block into the next turn's context as `_vision_blocks`, which
the dispatcher (guide.py) hands to the API as the tool_result content —
the model literally sees the rendering.

Coverage:
  - PNG entity → vision envelope; bytes match source
  - PDF entity → rasterized vision envelope (page 1 default)
  - PDF multi-page → `page=N` reads the requested page
  - Path-based view (no entity) → vision envelope
  - CSV → text preview (shape + head + dtypes)
  - Markdown / JSON → text head
  - Non-supported suffix (.svg, .h5ad) → error with hint
  - Unknown entity id → error
  - Missing artifact on disk → error
  - Both entity_id+path given OR neither → error

Run: .venv/bin/python tests/test_view_artifact.py
"""
from __future__ import annotations
import base64, json, os, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_tmp = tempfile.mkdtemp(prefix="aba_view_art_")
os.environ["ABA_DB_PATH"]     = str(Path(_tmp) / "va.db")
os.environ["ABA_RUNTIME_DIR"] = _tmp
os.environ["ARTIFACTS_DIR"]   = str(Path(_tmp) / "artifacts")
os.environ["ABA_WORK_DIR"]    = str(Path(_tmp) / "work")
os.environ["DATA_DIR"]        = str(Path(_tmp) / "data")
sys.path.insert(0, str(ROOT / "backend"))

from core.graph._schema import set_db_path, init_db  # noqa: E402
set_db_path(os.environ["ABA_DB_PATH"])
init_db()

import content.bio  # noqa: F401, E402

from core.runtime.mcp import register_inprocess_server, _reset_for_testing  # noqa: E402
from content.bio.mcp_servers.aba_core import make_server  # noqa: E402
_reset_for_testing()
register_inprocess_server("aba_core", make_server)

from content.bio.tools import execute_tool  # noqa: E402
from core.graph.entities import create_entity  # noqa: E402

CTX = {"thread_id": "thr_view"}
_failures: list[str] = []


def call(name, **inp):
    return json.loads(execute_tool(name, inp, CTX))


def check(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}"
          + (f" — {detail}" if (detail and not cond) else ""))
    if not cond:
        _failures.append(label)


def main() -> int:
    # Fixtures
    png_bytes = base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNgYGBgAAAABQABXvMqOgAAAABJRU5ErkJggg=="
    )
    png_disk = Path(_tmp) / "tiny.png"; png_disk.write_bytes(png_bytes)

    pdf_disk_1 = Path(_tmp) / "single.pdf"
    pdf_disk_n = Path(_tmp) / "multi.pdf"
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages
    plt.figure(figsize=(3, 2)); plt.plot([1, 2, 3])
    plt.savefig(pdf_disk_1, format="pdf"); plt.close()
    with PdfPages(pdf_disk_n) as pp:
        for k in range(3):
            plt.figure(figsize=(3, 2)); plt.plot([k, k+1, k+2]); plt.title(f"Page {k+1}")
            pp.savefig(); plt.close()

    csv_disk = Path(_tmp) / "tiny.csv"
    csv_disk.write_text("col_a,col_b,col_c\n1,2,3\n4,5,6\n7,8,9\n10,11,12\n")
    md_disk = Path(_tmp) / "notes.md"
    md_disk.write_text("# Title\n\nSome notes with **bold** content.\n")

    # ── 1. PNG entity ───────────────────────────────────────────────
    print("PNG entity → vision envelope")
    png_id = create_entity(entity_type="figure", title="Tiny PNG",
                           artifact_path=str(png_disk),
                           metadata={"thread_id": CTX["thread_id"]})
    r = call("view_artifact", entity_id=png_id)
    check("PNG: no error", "error" not in r, str(r)[:200])
    blocks = r.get("_vision_blocks") or []
    check("PNG: 2 vision blocks", len(blocks) == 2, str(blocks)[:200])
    if len(blocks) == 2:
        check("PNG: block[0]=text", blocks[0].get("type") == "text")
        check("PNG: block[1]=image", blocks[1].get("type") == "image")
        src = blocks[1].get("source") or {}
        check("PNG: media_type=image/png", src.get("media_type") == "image/png")
        check("PNG: bytes match source",
              base64.b64decode(src.get("data", "")) == png_bytes)
        check("PNG: preamble mentions entity_id",
              png_id in blocks[0].get("text", ""))

    # ── 2. PDF entity (single page) ─────────────────────────────────
    print("PDF entity → rasterized vision envelope")
    pdf_id = create_entity(entity_type="figure", title="Single PDF",
                           artifact_path=str(pdf_disk_1),
                           metadata={"thread_id": CTX["thread_id"]})
    r = call("view_artifact", entity_id=pdf_id)
    check("PDF: no error", "error" not in r, str(r)[:200])
    blocks = r.get("_vision_blocks") or []
    if blocks and blocks[1].get("type") == "image":
        src = blocks[1].get("source") or {}
        raster = base64.b64decode(src.get("data", ""))
        check("PDF: PNG raster >500 bytes",
              len(raster) > 500, f"got {len(raster)}")
        check("PDF: PNG magic", raster.startswith(b"\x89PNG"))

    # ── 3. PDF page selection ───────────────────────────────────────
    print("PDF entity, page=2 → renders requested page")
    pdf_n_id = create_entity(entity_type="figure", title="Multi PDF",
                             artifact_path=str(pdf_disk_n),
                             metadata={"thread_id": CTX["thread_id"]})
    r1 = call("view_artifact", entity_id=pdf_n_id, page=1)
    r2 = call("view_artifact", entity_id=pdf_n_id, page=2)
    b1 = r1.get("_vision_blocks") or []; b2 = r2.get("_vision_blocks") or []
    if (b1 and b2 and len(b1) > 1 and len(b2) > 1
            and b1[1].get("type") == "image" and b2[1].get("type") == "image"):
        d1 = b1[1]["source"]["data"]; d2 = b2[1]["source"]["data"]
        check("PDF: page 1 differs from page 2", d1 != d2,
              "same bytes — page selection didn't work")
        # Preamble should mention page numbers for multi-page PDF
        check("PDF: preamble names page (multi-page)",
              "page" in b1[0].get("text", "").lower(),
              b1[0].get("text", "")[:200])

    # ── 4. Path-based view ──────────────────────────────────────────
    print("path-based view (no entity) → vision envelope")
    r = call("view_artifact", path=str(png_disk))
    check("path: no error", "error" not in r, str(r)[:200])
    blocks = r.get("_vision_blocks") or []
    check("path: vision_blocks present", len(blocks) == 2)

    # ── 5. CSV → text preview ───────────────────────────────────────
    print("CSV → text preview")
    csv_id = create_entity(entity_type="table", title="Tiny CSV",
                           artifact_path=str(csv_disk),
                           metadata={"thread_id": CTX["thread_id"]})
    r = call("view_artifact", entity_id=csv_id)
    check("CSV: no error", "error" not in r, str(r)[:200])
    check("CSV: kind=table", r.get("kind") == "table")
    check("CSV: shape=[4,3]", r.get("shape") == [4, 3], str(r.get("shape")))
    check("CSV: columns",
          r.get("columns") == ["col_a", "col_b", "col_c"], str(r.get("columns")))
    check("CSV: head_20_rows_text present",
          "1" in (r.get("head_20_rows_text") or "")
          and "12" in (r.get("head_20_rows_text") or ""),
          (r.get("head_20_rows_text") or "")[:200])
    check("CSV: NOT a vision envelope (text-only)",
          "_vision_blocks" not in r)

    # ── 6. Markdown / JSON → text head ──────────────────────────────
    print("markdown → text head")
    r = call("view_artifact", path=str(md_disk))
    check("md: kind=text", r.get("kind") == "text")
    check("md: text_head contains body",
          "**bold**" in (r.get("text_head") or ""), str(r)[:200])

    # ── 7. Unsupported (.svg) ───────────────────────────────────────
    print("SVG → error")
    svg_disk = Path(_tmp) / "x.svg"; svg_disk.write_text("<svg/>")
    r = call("view_artifact", path=str(svg_disk))
    check("svg: error", "error" in r and ".svg" in r["error"], str(r))

    # ── 8. Unknown entity ───────────────────────────────────────────
    print("unknown entity → error")
    r = call("view_artifact", entity_id="fig_nope")
    check("unknown entity error",
          "error" in r and "not found" in r["error"], str(r))

    # ── 9. Missing on disk ──────────────────────────────────────────
    print("missing path → error")
    r = call("view_artifact", path="/nonexistent/file.png")
    # Assert the SEMANTICS (an error that names the path), not one phrasing. There
    # are two honest wordings here — "not found" when resolution fails, "missing on
    # disk" when it resolved and the file then vanished — and pinning the second
    # left this guard permanently red once resolution started answering first.
    check("missing-path error",
          "error" in r and "/nonexistent/file.png" in r["error"], str(r))

    # ── 10. Both args / neither arg ─────────────────────────────────
    print("invalid arg combos")
    r = call("view_artifact", entity_id=png_id, path=str(png_disk))
    check("both args → error", "error" in r, str(r))
    r = call("view_artifact")
    check("neither arg → error", "error" in r, str(r))

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + ", ".join(_failures))
        return 1
    print("ALL VIEW-ARTIFACT CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
