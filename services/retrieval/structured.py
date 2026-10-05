"""Structured retrieval over confirmed health events (YGH-76)."""

import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES


def search_structured(
    db: Session, user_id: uuid.UUID, query: str, limit: int = 10
) -> list[Evidence]:
    """Keyword match against event_type and the JSON data payload's text-ish fields.
    No ORM-level JSON text search is assumed portable, so this filters in Python
    over the user's confirmed events — fine at MVP scale, revisit if it's ever slow.
    """
    events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == user_id,
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .all()
    )

    query_lower = query.lower()
    matches = []
    for event in events:
        haystack = " ".join(str(v) for v in event.data.values()) + " " + event.event_type
        if query_lower in haystack.lower():
            matches.append(event)

    evidence = []
    for event in matches[:limit]:
        provenance = event.provenance[0] if event.provenance else None
        evidence.append(
            Evidence(
                document_id=str(event.document_id),
                page=provenance.page_number if provenance else 0,
                text=provenance.quote if provenance else str(event.data),
                event_ids=[str(event.id)],
                relevance_score=1.0,  # exact substring match; ranking refines this
                source="structured",
            )
        )
    return evidence
