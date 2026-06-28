"""view_file — the agent's EXPLICIT way to pull a file's content into context.

Attachments (and on-disk files) do NOT auto-enter the model context: a data file
may only ever be read by a pipeline (run_python). When the agent actually needs
to SEE or READ a file, it calls view_file, which routes by type:
  - image  -> a vision block (the model sees it), via the _vision_blocks envelope
              guide.py passes through as the tool_result content;
  - pdf     -> extracted text (figures not included; degrades gracefully if no
              extractor is installed — tells the agent to use run_python);
  - text/code/csv/json/... -> the text (capped);
  - unknown/binary -> a hex + ascii head + a magic-byte type guess, so the agent
              can reason about it or ask the user (graceful unknown handling).
"""
from __future__ import annotations

import binascii
from pathlib import Path
from typing import Optional

from content.bio.tools.file_io import _resolve_project_path

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_TEXT_EXTS = {".txt", ".md", ".csv", ".tsv", ".tab", ".json", ".yaml", ".yml",
              ".py", ".r", ".log", ".bed", ".gtf", ".gff", ".gff3", ".fa",
              ".fasta", ".fai", ".sh", ".html", ".xml", ".toml", ".ini", ".cfg"}

# magic-byte signatures → a human-readable guess for unrecognized files.
_MAGIC = [
    (b"%PDF", "PDF"), (b"\x89PNG", "PNG image"), (b"\xff\xd8\xff", "JPEG image"),
    (b"GIF8", "GIF image"), (b"PK\x03\x04", "ZIP / xlsx / docx / pptx"),
    (b"\x1f\x8b", "gzip"), (b"BZh", "bzip2"), (b"\xfd7zXZ", "xz"),
    (b"SQLite format 3", "SQLite database"), (b"\x93NUMPY", "NumPy .npy"),
    (b"\x89HDF\r\n\x1a\n", "HDF5 (.h5 / .h5ad / .loom)"), (b"RIFF", "RIFF (wav/webp/avi)"),
    (b"\x50\x4b", "ZIP-family"), (b"\x42\x4d", "BMP image"), (b"II*\x00", "TIFF image"),
    (b"MM\x00*", "TIFF image"), (b"\xca\xfe\xba\xbe", "Java class / Mach-O"),
    (b"\x7fELF", "ELF executable"), (b"BAM\x01", "BAM alignment"),
]


def _magic_guess(head: bytes) -> str:
    for sig, name in _MAGIC:
        if head.startswith(sig):
            return name
    if head and all(32 <= b < 127 or b in (9, 10, 13) for b in head[:128]):
        return "plain text"
    return "unknown binary"


def _extract_pdf_text(p: Path, max_chars: int) -> dict:
    for mod in ("pypdf", "PyPDF2"):
        try:
            m = __import__(mod, fromlist=["PdfReader"])
            reader = m.PdfReader(str(p))
            parts: list[str] = []
            total = 0
            for pg in reader.pages:
                t = pg.extract_text() or ""
                parts.append(t)
                total += len(t)
                if total > max_chars:
                    break
            txt = "\n\n".join(parts)
            return {"text": txt[:max_chars], "pages": len(reader.pages),
                    "truncated": len(txt) > max_chars}
        except Exception:  # noqa: BLE001 — try the next extractor
            continue
    return {"text": "", "pages": None, "truncated": False,
            "error": "no PDF text extractor installed. ensure_capability('pypdf') "
                     "then extract via run_python, or render pages to images."}


def view_file_tool(input_: dict, ctx: Optional[dict] = None) -> dict:
    """See/read an attached or on-disk file's content. image->vision, pdf->text,
    text->text, unknown->hex+ascii head + a type guess."""
    raw_path = input_.get("path")
    if not raw_path:
        return {"error": "path is required"}
    abspath, err = _resolve_project_path(raw_path, ctx, must_exist=True, enforce_sandbox=False)
    if err:
        return {"error": err}
    p = Path(abspath)
    if not p.is_file():
        return {"error": f"not a file: {abspath}"}
    suf = p.suffix.lower()
    max_chars = max(500, min(int(input_.get("max_chars") or 20000), 200000))

    if suf in _IMAGE_EXTS:
        from core.runtime.attachments import _image_vision_block
        blk = _image_vision_block(str(p))
        if not blk:
            return {"error": f"could not decode image {p.name}"}
        return {"path": str(p), "kind": "image",
                "_vision_blocks": [{"type": "text", "text": f"Image {p.name}:"}, blk]}

    if suf == ".pdf":
        ext = _extract_pdf_text(p, max_chars)
        return {"path": str(p), "kind": "pdf", "size_bytes": p.stat().st_size, **ext,
                "note": "Extracted text only — figures/tables-as-images are not included. "
                        "Ask to render pages as images if you need the visuals."}

    if suf in _TEXT_EXTS:
        try:
            txt = p.read_text(errors="replace")
        except Exception as e:  # noqa: BLE001
            return {"error": f"could not read {p.name}: {e}"}
        return {"path": str(p), "kind": "text", "size_bytes": p.stat().st_size,
                "text": txt[:max_chars], "truncated": len(txt) > max_chars}

    # unknown / binary → sniff + guess, let the agent reason or ask.
    head = p.read_bytes()[:256]
    return {"path": str(p), "kind": "binary", "size_bytes": p.stat().st_size,
            "type_guess": _magic_guess(head),
            "head_hex": binascii.hexlify(head[:64]).decode(),
            "head_ascii": head[:160].decode("latin-1", "replace"),
            "note": "Unrecognized file — sniffed the first bytes. Tell the user what you "
                    "think it is (or ask). If it's data, process it with run_python; if it "
                    "needs a library to read, ensure_capability first."}
