"""OCR for scanned pages (YGH-16).

Uses pytesseract + the system tesseract binary when available. Tesseract is a
native dependency that isn't guaranteed to be installed everywhere (it is
installed in the production Docker image, see services/api/Dockerfile), so this
degrades to a clearly-labeled placeholder instead of crashing the pipeline when
it's missing, per the error-handling requirement in YGH-19.
"""

import io
import logging

logger = logging.getLogger("app.document_processing.ocr")

try:
    import pytesseract
    from pdf2image import convert_from_bytes

    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


def ocr_available() -> bool:
    return _OCR_AVAILABLE


def ocr_pdf_page(pdf_content: bytes, page_number: int) -> str:
    """OCRs a single page of a PDF (1-indexed) and returns its text."""
    if not _OCR_AVAILABLE:
        logger.warning("OCR requested but tesseract/pdf2image is not installed; skipping")
        return ""

    images = convert_from_bytes(
        pdf_content, first_page=page_number, last_page=page_number
    )
    if not images:
        return ""
    return pytesseract.image_to_string(images[0]).strip()


def ocr_image(content: bytes) -> str:
    if not _OCR_AVAILABLE:
        logger.warning("OCR requested but tesseract is not installed; skipping")
        return ""
    from PIL import Image

    image = Image.open(io.BytesIO(content))
    return pytesseract.image_to_string(image).strip()
