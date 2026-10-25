"""Deleting a person's data (YGH-119) and their account (YGH-121).

What exists per person, and where each piece goes:

  rows      documents, document_pages, health_events, provenance, embeddings, ai_traces, users
  files     the original uploads, in storage/<user id>/
  cache     none on the server: there is no cache layer, and the in-process metrics hold only
            timings (no user ids, no content). The browser keeps one login token, which the page
            clears itself when the account is deleted.

Order matters. `provenance.document_id` and `embeddings.document_id` have no ON DELETE CASCADE, so
deleting a document before its facts would be refused by Postgres. The facts go first: their
`health_event_id` foreign keys DO cascade, which removes their sources and embeddings with them,
and only then the pages and the document. This depends on a database that enforces foreign keys
(SQLite, which the unit tests use, does not: tests/integration/test_data_deletion.py runs all of
this against the real Postgres). It is ONE transaction: either everything for the document is gone
or nothing is.

Files go AFTER the commit. If a file cannot be removed the records are already gone, the failure
is logged (class only) and reported to the caller, and the orphan is private and unreachable (no
row points at it). The reverse order could leave a record whose file is gone.
"""

import logging
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from packages.schemas.models import (
    AITrace,
    Document,
    DocumentPage,
    Embedding,
    HealthEvent,
    User,
)
from services.api.app.core import storage

logger = logging.getLogger("app.deletion")


@dataclass
class DeletionResult:
    documents: int = 0
    events: int = 0
    files_failed: int = 0
    failed_paths: list = field(default_factory=list)


def _delete_document_rows(db: Session, document_ids: list) -> int:
    """The facts of these documents (their sources and embeddings cascade), the pages, then the
    documents. Returns the number of facts removed."""
    if not document_ids:
        return 0
    events = db.execute(
        delete(HealthEvent).where(HealthEvent.document_id.in_(document_ids))).rowcount
    db.execute(delete(DocumentPage).where(DocumentPage.document_id.in_(document_ids)))
    db.execute(delete(Document).where(Document.id.in_(document_ids)))
    return events or 0


def _remove_files(paths: list, result: DeletionResult) -> None:
    for path in paths:
        try:
            storage.delete_stored_file(path)
        except OSError as exc:
            result.files_failed += 1
            result.failed_paths.append(path)
            logger.error("stored_file_not_deleted", extra={"error_type": type(exc).__name__})


def delete_document(db: Session, document: Document) -> DeletionResult:
    """One document with its pages, facts, sources, embeddings and original file."""
    path = document.storage_path
    result = DeletionResult(documents=1)
    try:
        result.events = _delete_document_rows(db, [document.id])
        db.commit()
    except Exception:
        db.rollback()
        raise
    _remove_files([path], result)
    logger.info("document_deleted", extra={"events": result.events,
                                           "files_failed": result.files_failed})
    return result


def delete_user_data(db: Session, user_id: uuid.UUID, remove_account: bool = False
                     ) -> DeletionResult:
    """All of a user's documents, facts, embeddings, traces and files. With remove_account the
    user row goes too. The user's storage folder is removed whole, which also takes any file no
    row points at."""
    result = DeletionResult()
    try:
        document_ids = list(db.scalars(select(Document.id).where(Document.user_id == user_id)))
        result.documents = len(document_ids)
        result.events = _delete_document_rows(db, document_ids)
        # Anything still carrying the user id (a row written by a run that was mid-flight).
        db.execute(delete(Embedding).where(Embedding.user_id == user_id))
        db.execute(delete(HealthEvent).where(HealthEvent.user_id == user_id))
        db.execute(delete(AITrace).where(AITrace.user_id == user_id))
        if remove_account:
            db.execute(delete(User).where(User.id == user_id))
        db.commit()
    except Exception:
        db.rollback()
        raise
    try:
        storage.delete_user_storage(user_id)
    except OSError as exc:
        result.files_failed += 1
        logger.error("user_storage_not_deleted", extra={"error_type": type(exc).__name__})
    logger.info("user_data_deleted", extra={"documents": result.documents,
                                            "events": result.events,
                                            "account_removed": remove_account,
                                            "files_failed": result.files_failed})
    return result
