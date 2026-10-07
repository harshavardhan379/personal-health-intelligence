"""Temporal retrieval (YGH-85, YGH-87): evidence for "what changed" / "recent" /
"when did X start" questions.

Unlike hybrid_search (relevance-ranked), this is date-ordered chronologically
(oldest first) with the date baked directly into each Evidence's text — the
model can't reason about sequence, recency, or "when was this first
documented" (YGH-87) from unordered, undated evidence. Chronological order
serves both "what changed recently" and "when did this start" equally: the
model reads explicit dates either way, so one ordered, sufficiently-wide
evidence set covers both question shapes rather than needing two separate
retrieval paths.
"""

import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES
from services.retrieval.describe import describe_event


def search_temporal(db: Session, user_id: uuid.UUID, limit: int = 20) -> list[Evidence]:
    events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == user_id,
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .order_by(HealthEvent.event_date.asc())
        .limit(limit)
        .all()
    )

    evidence = []
    for event in events:
        provenance = event.provenance[0] if event.provenance else None
        evidence.append(
            Evidence(
                document_id=str(event.document_id),
                page=provenance.page_number if provenance else 0,
                text=f"{event.event_date.isoformat()}: {describe_event(event)}",
                event_ids=[str(event.id)],
                relevance_score=1.0,
                source="temporal",
            )
        )
    return evidence
