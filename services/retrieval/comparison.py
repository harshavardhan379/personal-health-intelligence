"""Comparison retrieval (YGH-86): evidence for "compare my last two reports".

Groups confirmed events by their source Document, picks the N most recent
distinct documents (by their events' dates), and labels each event with which
report it came from — the model needs events grouped by report to compare
them, not just a flat relevance-ranked list.
"""

import uuid
from collections import defaultdict

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES
from services.retrieval.describe import describe_event


def search_comparison(
    db: Session, user_id: uuid.UUID, limit_documents: int = 2
) -> list[Evidence]:
    events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == user_id,
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .order_by(HealthEvent.event_date.desc())
        .all()
    )

    by_document: dict = defaultdict(list)
    document_order: list = []
    for event in events:
        doc_id = event.document_id
        if doc_id not in by_document and len(document_order) >= limit_documents:
            continue  # already have enough distinct documents
        if doc_id not in by_document:
            document_order.append(doc_id)
        by_document[doc_id].append(event)

    evidence = []
    for i, doc_id in enumerate(document_order, start=1):
        doc_events = by_document[doc_id]
        report_date = max(e.event_date for e in doc_events).isoformat()
        for event in doc_events:
            provenance = event.provenance[0] if event.provenance else None
            evidence.append(
                Evidence(
                    document_id=str(doc_id),
                    page=provenance.page_number if provenance else 0,
                    text=f"Report {i} ({report_date}): {describe_event(event)}",
                    event_ids=[str(event.id)],
                    relevance_score=1.0,
                    source="comparison",
                )
            )
    return evidence
