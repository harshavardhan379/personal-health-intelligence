"""Normalized document representation (YGH-18) + the pipeline that builds it,
with error handling per stage (YGH-19, YGH-95).

Every failure leaves here as a ProcessingError that names the stage it happened in and carries a
fixed, user-safe message. The underlying exception text is never kept: parser and OCR errors can
quote the document, and a stored or logged copy would be a second copy of health content.
"""

import logging
from dataclasses import dataclass

from services.document_processing import ocr, parse
from services.document_processing.classify import classify

logger = logging.getLogger("app.document_processing")

PARSE = "parse"
OCR = "ocr"
CLASSIFY = "classify"


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
    """Raised for any failure in the ingest pipeline. `stage` says where; the message is fixed
    text that is safe to store on Document.processing_error and show to the user."""

    def __init__(self, message: str, stage: str = PARSE):
        super().__init__(message)
        self.stage = stage


def process_document(document_id: str, mime_type: str, content: bytes) -> NormalizedDocument:
    stage = PARSE
    try:
        pages: list[NormalizedPage] = []

        if mime_type == "application/pdf":
            try:
                raw_pages = parse.extract_pdf_pages(content)
            except Exception as exc:  # corrupt PDF
                logger.warning("pdf_parse_failed", extra={"error_type": type(exc).__name__})
                raise ProcessingError(
                    "The PDF could not be read. It may be damaged or password protected.",
                    PARSE) from exc

            for p in raw_pages:
                if p["text"]:
                    pages.append(NormalizedPage(page=p["page"], text=p["text"], is_ocr=False))
                else:
                    stage = OCR
                    ocr_text = ocr.ocr_pdf_page(content, p["page"])
                    pages.append(NormalizedPage(page=p["page"], text=ocr_text, is_ocr=True))

        elif mime_type in ("image/jpeg", "image/png"):
            try:
                ocr_text = ocr.ocr_image(content)
            except ocr.OcrTimeout:
                raise
            except Exception as exc:  # unreadable image
                logger.warning("image_read_failed", extra={"error_type": type(exc).__name__})
                raise ProcessingError("The image could not be read.", OCR) from exc
            pages.append(NormalizedPage(page=1, text=ocr_text, is_ocr=True))

        else:
            raise ProcessingError("This file type cannot be processed.", PARSE)

        stage = CLASSIFY
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
    except ocr.OcrTimeout as exc:
        raise ProcessingError(
            "Reading a scanned page took too long. Try processing the document again.",
            OCR) from exc
    except Exception as exc:  # catch-all: never let the pipeline crash the request
        logger.warning("processing_failed",
                       extra={"stage": stage, "error_type": type(exc).__name__})
        raise ProcessingError(_UNEXPECTED[stage], stage) from exc


_UNEXPECTED = {
    PARSE: "The document could not be read.",
    OCR: "Text could not be recognised on a scanned page.",
    CLASSIFY: "The document type could not be determined.",
}
