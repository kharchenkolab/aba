---
name: extract-pdf-content
description: Download and extract text from a PDF file given a direct or indirect URL
when_to_use: When a user provides a URL to a PDF (or a page containing a PDF link) and needs the full text content
requires_tools: [run_python]
capabilities_needed: [requests, PyPDF2]
keywords: [PDF, text extraction, paper download, full text, document parsing]
produces: [extracted plain text from PDF]
domain: literature
source: biomni:tool/literature.py::extract_pdf_content
---
# Extract PDF Content

Distilled from a biomni implementation. In ABA, implement with the tools below — not biomni.

## Approach
1. If `url` does not end with `.pdf`, fetch the page and scan HTML for `href` attributes matching `*.pdf` with a regex; use the first match (resolving relative paths against the base URL).
2. `requests.get(url, timeout=30)` to download the PDF bytes.
3. Validate the response: check `Content-Type` for `application/pdf` OR check magic bytes `response.content.startswith(b"%PDF")`.
4. Wrap bytes in `BytesIO` and parse with `PyPDF2.PdfReader`.
5. Concatenate `page.extract_text()` across all pages.
6. Clean whitespace with `re.sub(r"\s+", " ", text).strip()`.
7. If no text extracted, warn that OCR may be required (image-based PDF).

## Key decisions
- Two-step URL resolution (page scan then direct download) handles publisher landing pages that link to the actual PDF.
- Magic-byte check (`%PDF`) as fallback when `Content-Type` is misconfigured.
- PyPDF2 is the primary extractor; add `pdfminer.six` as a fallback for better layout-aware extraction on complex PDFs.

## Caveats
- Image-only PDFs yield no text; OCR (e.g. `pytesseract` + `pdf2image`) is needed for those.
- Some publishers redirect PDF downloads behind auth walls; direct URL access may fail.
- PyPDF2 can mis-order columns in multi-column papers; `pdfminer.six` handles layout better.

## In ABA
Implement with `run_python`; `ensure_capability("requests", "PyPDF2")`. Original impl: `source` -> lift to lakeFS later.
