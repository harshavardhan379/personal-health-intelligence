"""Normalized document representation (YGH-18) + the pipeline that builds it,
with error handling per stage (YGH-19).
"""

import logging
from dataclasses import dataclass

from services.document_processing import ocr, parse
from services.document_processing.classify import classify

logger = logging.getLogger("app.document_processing")


@dataclass
class NormalizedPage:
    page: int
    text: str
    is_ocr: bool


@dataclass
class NormalizedDocument:
    document_id: str
    pages: list[NormalizedPage]
    classification: str
    classification_confidence: float


class ProcessingError(Exception):
    """Raised for any failure in the ingest pipeline; the message is safe to store
    on Document.processing_error (no raw document content)."""


def process_document(document_id: str, mime_type: str, content: bytes) -> NormalizedDocument:
    try:
        pages: list[NormalizedPage] = []

        if mime_type == "application/pdf":
            try:
                raw_pages = parse.extract_pdf_pages(content)
            except Exception as exc:  # corrupt PDF
                raise ProcessingError(f"Could not parse PDF: {exc}") from exc

            for p in raw_pages:
                if p["text"]:
                    pages.append(NormalizedPage(page=p["page"], text=p["text"], is_ocr=False))
                else:
                    ocr_text = ocr.ocr_pdf_page(content, p["page"])
                    pages.append(NormalizedPage(page=p["page"], text=ocr_text, is_ocr=True))

        elif mime_type in ("image/jpeg", "image/png"):
            try:
                ocr_text = ocr.ocr_image(content)
            except Exception as exc:  # unreadable image
                raise ProcessingError(f"Could not read image: {exc}") from exc
            pages.append(NormalizedPage(page=1, text=ocr_text, is_ocr=True))

        else:
            raise ProcessingError(f"Unsupported format for processing: {mime_type}")

        full_text = "\n".join(p.text for p in pages)
        label, confidence = classify(full_text)

        return NormalizedDocument(
            document_id=document_id,
            pages=pages,
            classification=label,
            classification_confidence=confidence,
        )

    except ProcessingError:
        raise
    except Exception as exc:  # catch-all: never let the pipeline crash the request
        logger.exception("Unexpected processing failure for document %s", document_id)
        raise ProcessingError(f"Unexpected processing failure: {exc}") from exc
