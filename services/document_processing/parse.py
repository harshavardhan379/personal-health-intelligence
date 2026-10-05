"""PDF text extraction (YGH-15)."""

import io
from typing import TypedDict

from pypdf import PdfReader


class PageText(TypedDict):
    page: int
    text: str


def extract_pdf_pages(content: bytes) -> list[PageText]:
    """Returns one entry per page. A page with no extractable text (a scan) comes
    back with an empty string, that is the signal services/document_processing/ocr.py
    uses to decide which pages need OCR.
    """
    reader = PdfReader(io.BytesIO(content))
    pages: list[PageText] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        pages.append({"page": i, "text": text})
    return pages
