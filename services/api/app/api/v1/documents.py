"""Upload, status and document listing (YGH-48, YGH-49, YGH-50 API, YGH-51).

Also runs the document-processing + extraction pipeline as a background task,
so upload returns immediately and the client polls status (M4-T03's
Upload -> Processing -> Processed -> Review required -> Failed states).
"""

import logging
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
)
from pydantic import BaseModel
from sqlalchemy.orm import Session

from packages.schemas.db import SessionLocal, get_db
from packages.schemas.models import Document, HealthEvent, Provenance, User
from services.api.app.core.deps import get_current_user
from services.api.app.core.storage import (
    ALLOWED_MIME_TYPES,
    UploadValidationError,
    read_upload,
    save_upload,
    validate_upload,
)
from services.document_processing.normalize import ProcessingError, process_document
from services.extraction.pipeline import extract_all

router = APIRouter(prefix="/documents", tags=["documents"])
logger = logging.getLogger("app.documents")


class DocumentOut(BaseModel):
    id: str
    filename: str
    mime_type: str
    processing_status: str
    classification: Optional[str] = None
    classification_confidence: Optional[float] = None
    processing_error: Optional[str] = None
    uploaded_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class DocumentProvenanceOut(BaseModel):
    page_number: int
    quote: str


class DocumentEventOut(BaseModel):
    """An event extracted from one document, whatever its review status, with where in the
    document it was found (YGH-118)."""

    id: str
    event_type: str
    event_date: str
    event_date_precision: str
    data: dict
    confidence: float
    review_status: str
    provenance: list[DocumentProvenanceOut]


def _to_out(doc: Document) -> DocumentOut:
    return DocumentOut(
        id=str(doc.id),
        filename=doc.filename,
        mime_type=doc.mime_type,
        processing_status=doc.processing_status,
        classification=doc.classification,
        classification_confidence=doc.classification_confidence,
        processing_error=doc.processing_error,
        uploaded_at=doc.uploaded_at,
    )


def _run_pipeline(document_id: str, user_id: str, mime_type: str, storage_path: str) -> None:
    """Runs in a background task (own DB session; the request's session is already closed)."""
    from services.api.app.core.storage import read_upload

    db = SessionLocal()
    try:
        doc = db.get(Document, uuid.UUID(document_id))
        if doc is None:
            return

        doc.processing_status = "processing"
        db.commit()

        try:
            content = read_upload(storage_path)
            normalized = process_document(document_id, mime_type, content)
        except ProcessingError as exc:
            doc.processing_status = "failed"
            doc.processing_error = str(exc)
            db.commit()
            logger.warning("Processing failed for document %s: %s", document_id, exc)
            return

        doc.classification = normalized.classification
        doc.classification_confidence = normalized.classification_confidence

        from packages.schemas.models import DocumentPage

        for page in normalized.pages:
            db.add(
                DocumentPage(
                    document_id=doc.id,
                    page_number=page.page,
                    text=page.text,
                    is_ocr=page.is_ocr,
                )
            )

        facts = extract_all(normalized.pages)
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
            db.add(
                Provenance(
                    health_event_id=event.id,
                    document_id=doc.id,
                    page_number=fact.source_page,
                    quote=fact.quote,
                )
            )

        doc.processing_status = "processed"
        db.commit()
    except Exception:
        logger.exception("Unexpected failure processing document %s", document_id)
        doc = db.get(Document, uuid.UUID(document_id))
        if doc is not None:
            doc.processing_status = "failed"
            doc.processing_error = "Unexpected processing failure"
            db.commit()
    finally:
        db.close()


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    content = await file.read()

    try:
        validate_upload(file.filename, file.content_type, len(content))
    except UploadValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    storage_path, _ = save_upload(current_user.id, file.filename, content)

    doc = Document(
        user_id=current_user.id,
        filename=file.filename,
        mime_type=file.content_type,
        storage_path=storage_path,
        processing_status="uploaded",
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)

    background_tasks.add_task(
        _run_pipeline, str(doc.id), str(current_user.id), doc.mime_type, storage_path
    )

    return _to_out(doc)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    docs = (
        db.query(Document)
        .filter_by(user_id=current_user.id)
        .order_by(Document.uploaded_at.desc())
        .all()
    )
    return [_to_out(d) for d in docs]


def _owned_document(db: Session, user: User, document_id: uuid.UUID) -> Document:
    doc = db.get(Document, document_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/{document_id}", response_model=DocumentOut)
def get_document_status(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _to_out(_owned_document(db, current_user, document_id))


@router.get("/{document_id}/file")
def get_document_file(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The original uploaded file, for the document page (YGH-118). Owner only."""
    doc = _owned_document(db, current_user, document_id)
    try:
        content = read_upload(doc.storage_path)
    except OSError as exc:  # missing, or a path outside the storage folder
        raise HTTPException(status_code=404, detail="The original file is not available") from exc

    allowed = doc.mime_type in ALLOWED_MIME_TYPES
    disposition = "inline" if allowed else "attachment"
    return Response(
        content=content,
        media_type=doc.mime_type if allowed else "application/octet-stream",
        headers={
            "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(doc.filename)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/{document_id}/events", response_model=list[DocumentEventOut])
def get_document_events(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Everything extracted from this document and its review status. Owner only."""
    doc = _owned_document(db, current_user, document_id)
    events = (
        db.query(HealthEvent)
        .filter(HealthEvent.document_id == doc.id, HealthEvent.user_id == current_user.id)
        .order_by(HealthEvent.event_date.asc(), HealthEvent.event_type.asc())
        .all()
    )
    return [
        DocumentEventOut(
            id=str(e.id),
            event_type=e.event_type,
            event_date=e.event_date.isoformat(),
            event_date_precision=e.event_date_precision,
            data=e.data,
            confidence=e.confidence,
            review_status=e.review_status,
            provenance=[
                DocumentProvenanceOut(page_number=p.page_number, quote=p.quote)
                for p in e.provenance
            ],
        )
        for e in events
    ]
