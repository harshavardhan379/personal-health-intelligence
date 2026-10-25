"""OCR for scanned pages (YGH-16).

Uses pytesseract + the system tesseract binary when available. Tesseract is a
native dependency that isn't guaranteed to be installed everywhere (it is
installed in the production Docker image, see services/api/Dockerfile), so this
degrades to a clearly-labeled placeholder instead of crashing the pipeline when
it's missing, per the error-handling requirement in YGH-19.

Each page is bounded by the "ocr" timeout policy (YGH-94): a page that hits the limit raises
OcrTimeout instead of holding the worker for ever, and is not retried.
"""

import io
import logging

from packages.observability.resilience import POLICIES

logger = logging.getLogger("app.document_processing.ocr")

try:
    import pytesseract
    from pdf2image import convert_from_bytes
    from pdf2image.exceptions import PDFPopplerTimeoutError

    _OCR_AVAILABLE = True
except ImportError:
    _OCR_AVAILABLE = False


class OcrTimeout(Exception):
    """One page took longer than the OCR policy allows."""


def ocr_available() -> bool:
    return _OCR_AVAILABLE


def _timeout_seconds() -> int:
    return int(POLICIES["ocr"].timeout_s)


def _recognise(image) -> str:
    try:
        return pytesseract.image_to_string(image, timeout=_timeout_seconds()).strip()
    except RuntimeError as exc:  # pytesseract signals its timeout with a RuntimeError
        if "timeout" in str(exc).lower():
            raise OcrTimeout("tesseract timed out") from exc
        raise


def ocr_pdf_page(pdf_content: bytes, page_number: int) -> str:
    """OCRs a single page of a PDF (1-indexed) and returns its text."""
    if not _OCR_AVAILABLE:
        logger.warning("OCR requested but tesseract/pdf2image is not installed; skipping")
        return ""

    try:
        images = convert_from_bytes(
            pdf_content, first_page=page_number, last_page=page_number,
            timeout=_timeout_seconds(),
        )
    except PDFPopplerTimeoutError as exc:
        raise OcrTimeout("page rendering timed out") from exc
    if not images:
        return ""
    return _recognise(images[0])


def ocr_image(content: bytes) -> str:
    if not _OCR_AVAILABLE:
        logger.warning("OCR requested but tesseract is not installed; skipping")
        return ""
    from PIL import Image

    image = Image.open(io.BytesIO(content))
    return _recognise(image)
