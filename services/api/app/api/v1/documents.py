"""Upload, status and document listing (YGH-48, YGH-49, YGH-50 API, YGH-51).

Also runs the document-processing + extraction pipeline as a background task,
so upload returns immediately and the client polls status (M4-T03's
Upload -> Processing -> Processed -> Review required -> Failed states).
"""

import logging
import uuid
from datetime import datetime, timezone
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

from packages.observability import metrics
from packages.observability.context import get_request_id
from packages.schemas.db import get_db
from packages.schemas.models import Document, HealthEvent, User
from services.api.app.core.deletion import delete_document
from services.api.app.core.deps import get_current_user
from services.api.app.core.pipeline import (
    clear_partial_results,
    recover_stale_documents,
    run_pipeline,
)
from services.api.app.core.storage import (
    ALLOWED_MIME_TYPES,
    MAX_FILE_SIZE_BYTES,
    UploadValidationError,
    read_upload,
    save_upload,
    validate_content,
    validate_upload,
)

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
    failed_stage: Optional[str] = None
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
        failed_stage=doc.failed_stage,
        uploaded_at=doc.uploaded_at,
    )


async def _read_bounded(file: UploadFile) -> bytes:
    """The upload's bytes, giving up as soon as it is over the size limit rather than holding
    an arbitrarily large body in memory first."""
    chunks, total = [], 0
    while chunk := await file.read(1024 * 1024):
        total += len(chunk)
        if total > MAX_FILE_SIZE_BYTES:
            raise UploadValidationError(
                f"File too large: more than {MAX_FILE_SIZE_BYTES} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("", response_model=DocumentOut, status_code=201)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        content = await _read_bounded(file)
        validate_upload(file.filename, file.content_type, len(content))
        validate_content(file.content_type, content)
    except UploadValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    with metrics.timed(metrics.UPLOAD):  # storing the file and recording the document
        try:
            storage_path, _ = save_upload(current_user.id, file.filename, content)
        except OSError as exc:
            logger.error("upload_not_stored", extra={"error_type": type(exc).__name__})
            raise HTTPException(
                status_code=503, detail="The file could not be stored. Please try again."
            ) from exc

        doc = Document(
            user_id=current_user.id,
            filename=file.filename,
            mime_type=file.content_type,
            storage_path=storage_path,
            processing_status="uploaded",
            processing_started_at=datetime.now(timezone.utc),
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)

    background_tasks.add_task(
        run_pipeline, str(doc.id), doc.mime_type, storage_path, get_request_id()
    )

    return _to_out(doc)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    recover_stale_documents(db)  # a run that died would otherwise show "processing" for ever
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


@router.post("/{document_id}/reprocess", response_model=DocumentOut, status_code=202)
def reprocess_document(
    document_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Run a failed document through the pipeline again. Owner only; nothing else can be re-run."""
    doc = _owned_document(db, current_user, document_id)
    if doc.processing_status != "failed":
        raise HTTPException(status_code=409, detail="Only a failed document can be processed again")
    clear_partial_results(db, doc)
    doc.processing_status = "uploaded"
    doc.processing_started_at = datetime.now(timezone.utc)
    doc.processing_error = None
    doc.failed_stage = None
    db.commit()
    db.refresh(doc)
    background_tasks.add_task(
        run_pipeline, str(doc.id), doc.mime_type, doc.storage_path, get_request_id()
    )
    return _to_out(doc)


@router.delete("/{document_id}", status_code=204)
def delete_document_endpoint(
    document_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete one document with everything derived from it: pages, extracted facts, their
    sources, their embeddings and the original file. Owner only; permanent."""
    delete_document(db, _owned_document(db, current_user, document_id))
    return Response(status_code=204)


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
