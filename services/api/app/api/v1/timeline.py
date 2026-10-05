"""Timeline API (YGH-27, YGH-29, YGH-30 dedup, YGH-31 temporal normalization)."""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.models import HealthEvent, User
from packages.schemas.queries import CONFIRMED_STATUSES
from services.api.app.core.deps import get_current_user

router = APIRouter(prefix="/timeline", tags=["timeline"])


class ProvenanceOut(BaseModel):
    document_id: str
    page_number: int
    quote: str

    model_config = {"from_attributes": True}


class TimelineEventOut(BaseModel):
    id: str
    event_type: str
    event_date: str
    event_date_precision: str
    data: dict
    confidence: float
    review_status: str
    provenance: list[ProvenanceOut]

    model_config = {"from_attributes": True}


def _dedup_key(event: HealthEvent) -> tuple:
    """Two confirmed events are "the same fact" if they share type, date and the
    event-defining field (YGH-30). Re-uploading the same report must not double
    the timeline.
    """
    if event.event_type == "lab":
        identity = (event.data.get("test_name"), event.data.get("value"), event.data.get("unit"))
    elif event.event_type == "medication":
        identity = (event.data.get("medication"), event.data.get("dose"))
    elif event.event_type == "encounter":
        identity = (event.data.get("encounter_type"), event.data.get("provider_or_facility"))
    else:
        identity = (event.data.get("symptom"),)
    return (event.event_type, event.event_date, identity)


def _to_out(event: HealthEvent) -> TimelineEventOut:
    return TimelineEventOut(
        id=str(event.id),
        event_type=event.event_type,
        event_date=event.event_date.isoformat(),
        event_date_precision=event.event_date_precision,
        data=event.data,
        confidence=event.confidence,
        review_status=event.review_status,
        provenance=[
            ProvenanceOut(
                document_id=str(p.document_id), page_number=p.page_number, quote=p.quote
            )
            for p in event.provenance
        ],
    )


@router.get("", response_model=list[TimelineEventOut])
def get_timeline(
    event_type: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(HealthEvent).filter(
        HealthEvent.user_id == current_user.id,
        HealthEvent.review_status.in_(CONFIRMED_STATUSES),
    )
    if event_type:
        query = query.filter(HealthEvent.event_type == event_type)

    events = query.order_by(HealthEvent.event_date.asc()).all()

    # Deduplicate: keep the first (earliest-reviewed) occurrence of each identity.
    seen = set()
    deduped = []
    for event in events:
        key = _dedup_key(event)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(event)

    return [_to_out(e) for e in deduped]


@router.get("/{event_id}", response_model=TimelineEventOut)
def get_event_detail(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = db.get(HealthEvent, event_id)
    if (
        event is None
        or event.user_id != current_user.id
        or event.review_status not in CONFIRMED_STATUSES
    ):
        raise HTTPException(status_code=404, detail="Event not found")
    return _to_out(event)
