"""The document pipeline as explicit stages, each with its own failure (YGH-95).

    read_file -> parse -> ocr -> classify -> extract -> save

Nothing is written until every stage has succeeded: pages and events are added in one transaction
and committed once at the end, so a failure leaves no half-processed document behind. When a stage
fails the transaction is rolled back and the document is marked `failed` with the stage and a fixed,
user-safe reason. The exception's own text is never stored or logged (it can quote the document);
only its class is.

A process that dies mid-run leaves a document `processing` for ever. recover_stale_documents marks
those `failed` at the `interrupted` stage so the user can run them again.
"""

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from packages.observability import metrics
from packages.observability.context import set_request_id
from packages.observability.resilience import POLICIES, call_with_retry, is_transient_db
from packages.schemas.db import SessionLocal
from packages.schemas.models import Document, DocumentPage, HealthEvent, Provenance
from services.api.app.core.storage import read_upload
from services.document_processing.normalize import ProcessingError, process_document
from services.extraction.pipeline import extract_all

logger = logging.getLogger("app.pipeline")

READ_FILE = "read_file"
EXTRACT = "extract"
SAVE = "save"
INTERRUPTED = "interrupted"

STALE_AFTER = timedelta(minutes=10)
INTERRUPTED_MESSAGE = (
    "Processing was interrupted before it finished, for example by a restart. "
    "Try processing the document again.")


class StageFailure(Exception):
    """A stage failed. `message` is fixed text, safe to show and to store."""

    def __init__(self, stage: str, message: str):
        super().__init__(message)
        self.stage = stage
        self.message = message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _read_file(storage_path: str) -> bytes:
    try:
        return read_upload(storage_path)
    except OSError as exc:
        logger.warning("stage_failed", extra={"stage": READ_FILE,
                                              "error_type": type(exc).__name__})
        raise StageFailure(READ_FILE, "The uploaded file could not be found or read.") from exc


def _extract(pages):
    try:
        with metrics.timed(metrics.EXTRACTION):
            return extract_all(pages)
    except Exception as exc:
        logger.warning("stage_failed", extra={"stage": EXTRACT, "error_type": type(exc).__name__})
        raise StageFailure(EXTRACT, "Values could not be read from the document.") from exc


def _save(db: Session, doc: Document, normalized, facts) -> None:
    """All of the document's results in one transaction."""
    try:
        doc.classification = normalized.classification
        doc.classification_confidence = normalized.classification_confidence
        for page in normalized.pages:
            db.add(DocumentPage(document_id=doc.id, page_number=page.page, text=page.text,
                                is_ocr=page.is_ocr))
        for fact in facts:
            event = HealthEvent(
                user_id=doc.user_id,
                document_id=doc.id,
                event_type=fact.event_type,
                event_date=fact.event_date,
                event_date_precision=fact.event_date_precision,
                data=fact.data,
                confidence=fact.confidence,
                extraction_method="regex",
                review_status="pending",
            )
            db.add(event)
            db.flush()
            db.add(Provenance(health_event_id=event.id, document_id=doc.id,
                              page_number=fact.source_page, quote=fact.quote))
        doc.processing_status = "processed"
        db.commit()
    except Exception as exc:
        logger.warning("stage_failed", extra={"stage": SAVE, "error_type": type(exc).__name__})
        raise StageFailure(SAVE, "The results could not be saved.") from exc


def mark_failed(document_id: str, stage: str, message: str) -> bool:
    """Record the failure on a fresh session (the working one may be broken), retrying a dropped
    database connection. Returns False if it still could not be written; recovery of stale
    documents catches that case later."""

    def write() -> None:
        db = SessionLocal()
        try:
            doc = db.get(Document, uuid.UUID(document_id))
            if doc is None:
                return
            doc.processing_status = "failed"
            doc.failed_stage = stage
            doc.processing_error = message
            db.commit()
        finally:
            db.close()

    try:
        call_with_retry(write, POLICIES["database"].retry, is_transient=is_transient_db,
                        name="database")
        return True
    except Exception as exc:
        logger.error("document_failure_not_recorded",
                     extra={"document_id": document_id, "stage": stage,
                            "error_type": type(exc).__name__})
        return False


def run_pipeline(document_id: str, mime_type: str, storage_path: str,
                 request_id: Optional[str] = None) -> None:
    """Runs in a background task with its own session; the request's is already closed."""
    set_request_id(request_id)  # the log lines of this run carry the upload's request id
    started = time.perf_counter()
    ok, ran = False, True
    db = SessionLocal()
    try:
        doc = db.get(Document, uuid.UUID(document_id))
        if doc is None:
            ran = False  # deleted since it was queued: nothing was processed, nothing to time
            return
        doc.processing_status = "processing"
        doc.processing_started_at = _now()
        doc.processing_error = None
        doc.failed_stage = None
        db.commit()

        content = _read_file(storage_path)
        normalized = process_document(document_id, mime_type, content)  # parse, ocr, classify
        facts = _extract(normalized.pages)
        _save(db, doc, normalized, facts)
        ok = True
        logger.info("document_processed", extra={
            "document_id": document_id, "pages": len(normalized.pages), "events": len(facts)})
    except (StageFailure, ProcessingError) as exc:
        db.rollback()
        message = exc.message if isinstance(exc, StageFailure) else str(exc)
        _fail(document_id, exc.stage, message)
    except Exception as exc:  # a bug, or the database: still end in a recorded failure
        db.rollback()
        logger.error("pipeline_crashed", extra={"document_id": document_id,
                                                "error_type": type(exc).__name__})
        _fail(document_id, "unknown", "Processing failed unexpectedly. Try again.")
    finally:
        db.close()
        if ran:
            metrics.registry.observe(metrics.PROCESSING, (time.perf_counter() - started) * 1000, ok)


def _fail(document_id: str, stage: str, message: str) -> None:
    logger.warning("document_failed", extra={"document_id": document_id, "stage": stage})
    mark_failed(document_id, stage, message)


def recover_stale_documents(db: Session, older_than: timedelta = STALE_AFTER,
                            now: Optional[datetime] = None) -> int:
    """Mark documents stuck `uploaded` or `processing` for longer than `older_than` as failed at
    the `interrupted` stage. Returns how many. Assumes one API process: a second worker starting
    must not be able to kill a run the first is still doing, hence the age threshold."""
    cutoff = _as_aware(now or _now()) - older_than
    started = func.coalesce(Document.processing_started_at, Document.uploaded_at)
    stuck = (db.query(Document)
             .filter(Document.processing_status.in_(("uploaded", "processing")),
                     started < cutoff)
             .all())
    for doc in stuck:
        doc.processing_status = "failed"
        doc.failed_stage = INTERRUPTED
        doc.processing_error = INTERRUPTED_MESSAGE
    if stuck:
        db.commit()
        logger.warning("stale_documents_recovered", extra={"count": len(stuck)})
    return len(stuck)


def clear_partial_results(db: Session, doc: Document) -> None:
    """Before running a failed document again: drop the pages and the still-unreviewed events of
    the earlier attempt. Confirmed or edited events are the user's and are never touched. The
    caller commits."""
    db.query(DocumentPage).filter_by(document_id=doc.id).delete()
    pending = [e.id for e in db.query(HealthEvent.id).filter_by(
        document_id=doc.id, review_status="pending")]
    if pending:
        db.query(Provenance).filter(Provenance.health_event_id.in_(pending)).delete(
            synchronize_session=False)
        db.query(HealthEvent).filter(HealthEvent.id.in_(pending)).delete(
            synchronize_session=False)
