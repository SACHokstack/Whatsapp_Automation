"""Pull plain text out of an uploaded course document.

The client's files are a mix: some are digital PDFs with a real text layer, some are scans
(photographed or photocopied outlines) with no text at all. So extraction is two-tier — try the
cheap text layer first, and fall back to OCR only for the pages that come back empty. A file can
end up 'mixed' when only some of its pages needed OCR.

OCR is deliberately bounded: rasterising a PDF and running Tesseract is slow and memory-hungry,
and this runs on a small Railway instance, so pages are rendered one at a time (never the whole
document at once) and a page cap stops a 300-page upload from pinning the box.

Tesseract and poppler are system packages; if they are missing, extraction degrades to
"text layer only" with a clear error rather than crashing.
"""

from __future__ import annotations

import io
import logging
import os

logger = logging.getLogger(__name__)

# Rendering DPI for OCR. 200 is the usual sweet spot: below ~150 accuracy falls off,
# above ~300 the memory and time cost climbs with little gain.
_OCR_DPI = int(os.getenv("OCR_DPI", "200") or 200)


class ExtractionError(RuntimeError):
    """Raised when no text could be recovered from a document."""


def ocr_page_cap() -> int:
    """Max pages to OCR in one document. 0 disables OCR entirely."""
    from services.settings import get_int

    return max(0, get_int("OCR_PAGE_CAP", 40))


def extract_text(raw: bytes, filename: str, content_type: str = "") -> tuple[str, str]:
    """Return (text, method) for an uploaded file.

    `method` is one of 'text' (native text layer), 'ocr' (every page rasterised),
    'mixed' (some pages needed OCR), or 'plain' (the file was already text).
    """
    name = (filename or "").lower()
    if name.endswith(".pdf") or "pdf" in (content_type or ""):
        return _extract_pdf(raw)
    if name.endswith((".docx", ".doc")):
        return _extract_docx(raw)
    if name.endswith((".txt", ".md", ".markdown")):
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            raise ExtractionError("file is empty")
        return text, "plain"
    if name.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp")):
        text = _ocr_image_bytes(raw)
        if not text:
            raise ExtractionError("no text found in image (is Tesseract installed?)")
        return text, "ocr"
    raise ExtractionError(f"unsupported file type: {filename}")


def _extract_pdf(raw: bytes) -> tuple[str, str]:
    """Text layer per page, OCR only for the pages that have none."""
    try:
        import pdfplumber
    except ImportError as error:  # pragma: no cover - dependency is in requirements
        raise ExtractionError("pdfplumber is not installed") from error

    pages: list[str] = []
    empty_indexes: list[int] = []
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        total_pages = len(pdf.pages)
        for index, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").strip()
            pages.append(text)
            if not text:
                empty_indexes.append(index)

    if not empty_indexes:
        joined = "\n\n".join(p for p in pages if p).strip()
        if not joined:
            raise ExtractionError("PDF has no extractable text")
        return joined, "text"

    cap = ocr_page_cap()
    ocr_done = 0
    if cap:
        for index in empty_indexes[:cap]:
            try:
                text = _ocr_pdf_page(raw, index + 1)  # pdf2image pages are 1-based
            except ExtractionError:
                raise
            except Exception:  # noqa: BLE001 - one bad page must not fail the document
                logger.exception("event=ocr_page_failed page=%d", index + 1)
                continue
            if text:
                pages[index] = text
                ocr_done += 1
        if len(empty_indexes) > cap:
            logger.warning(
                "event=ocr_page_cap_reached cap=%d empty_pages=%d", cap, len(empty_indexes)
            )

    joined = "\n\n".join(p for p in pages if p).strip()
    if not joined:
        raise ExtractionError(
            "no text found — the PDF appears to be scanned and OCR produced nothing "
            "(is Tesseract installed?)"
        )
    had_text_layer = len(empty_indexes) < total_pages
    return joined, ("mixed" if had_text_layer and ocr_done else "ocr" if ocr_done else "text")


def _ocr_pdf_page(raw: bytes, page_number: int) -> str:
    """Rasterise exactly one page and OCR it, so peak memory stays at one page."""
    try:
        from pdf2image import convert_from_bytes
    except ImportError as error:
        raise ExtractionError("pdf2image is not installed (needs poppler-utils)") from error

    images = convert_from_bytes(raw, dpi=_OCR_DPI, first_page=page_number, last_page=page_number)
    if not images:
        return ""
    try:
        return _ocr_image(images[0])
    finally:
        images[0].close()


def _ocr_image_bytes(raw: bytes) -> str:
    try:
        from PIL import Image
    except ImportError as error:
        raise ExtractionError("Pillow is not installed") from error
    with Image.open(io.BytesIO(raw)) as image:
        return _ocr_image(image)


def _ocr_image(image) -> str:
    try:
        import pytesseract
    except ImportError as error:
        raise ExtractionError("pytesseract is not installed") from error
    try:
        return str(pytesseract.image_to_string(image) or "").strip()
    except Exception as error:  # noqa: BLE001 - almost always "tesseract binary not found"
        raise ExtractionError(f"OCR failed: {error}") from error


def _extract_docx(raw: bytes) -> tuple[str, str]:
    try:
        import docx
    except ImportError as error:
        raise ExtractionError("python-docx is not installed") from error

    document = docx.Document(io.BytesIO(raw))
    parts = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    text = "\n\n".join(parts).strip()
    if not text:
        raise ExtractionError("document has no text")
    return text, "text"
