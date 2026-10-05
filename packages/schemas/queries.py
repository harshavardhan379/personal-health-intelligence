"""Shared, user-scoped query helpers (YGH-14, YGH-39).

These exist so "filter by user_id" and "only confirmed events are authoritative" are
each written exactly once. Every other module should call these rather than building
its own query, so isolation and the review gate can't be forgotten at a new call site.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.schemas.models import Document, HealthEvent

CONFIRMED_STATUSES = ("confirmed", "edited")


def confirmed_events_query(db: Session, user_id: uuid.UUID):
    """Base query for a user's authoritative health events.

    Timeline, trends and retrieval must all build on this, never query
    HealthEvent directly, so unreviewed/rejected data can never leak into
    anything the user treats as fact.
    """
    return select(HealthEvent).where(
        HealthEvent.user_id == user_id,
        HealthEvent.review_status.in_(CONFIRMED_STATUSES),
    )


def user_documents_query(db: Session, user_id: uuid.UUID):
    return select(Document).where(Document.user_id == user_id)


def user_owns_document(db: Session, user_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    doc = db.get(Document, document_id)
    return doc is not None and doc.user_id == user_id
