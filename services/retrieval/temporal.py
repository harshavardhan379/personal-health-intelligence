"""Temporal retrieval (YGH-85): evidence for "what changed" / "recent" questions.

Unlike hybrid_search (relevance-ranked), this is date-ordered and includes the
date explicitly in each Evidence's text — the model can't reason about "recent"
or "what changed" from unordered, undated evidence.
"""

import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from packages.schemas.models import HealthEvent
from packages.schemas.queries import CONFIRMED_STATUSES


def _describe_event(event: HealthEvent) -> str:
    """Same per-type rendering the frontend uses (apps/web timeline describeEvent),
    kept in sync deliberately — same facts, same words, whether a human or the
    model is reading them.
    """
    data = event.data
    if event.event_type == "lab":
        return f"{data.get('test_name')}: {data.get('value')} {data.get('unit')}"
    if event.event_type == "medication":
        dose = f" {data['dose']}" if data.get("dose") else ""
        return f"{data.get('medication')}{dose}"
    if event.event_type == "encounter":
        return str(data.get("encounter_type"))
    if event.event_type == "symptom":
        return f"Symptom: {data.get('symptom')}"
    return str(data)


def search_temporal(db: Session, user_id: uuid.UUID, limit: int = 10) -> list[Evidence]:
    events = (
        db.query(HealthEvent)
        .filter(
            HealthEvent.user_id == user_id,
            HealthEvent.review_status.in_(CONFIRMED_STATUSES),
        )
        .order_by(HealthEvent.event_date.desc())
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
                # The date is IN the text on purpose — this is what lets the
                # model order/compare events, not just list them.
                text=f"{event.event_date.isoformat()}: {_describe_event(event)}",
                event_ids=[str(event.id)],
                relevance_score=1.0,  # recency-ordered, not relevance-ranked
                source="temporal",
            )
        )
    return evidence
