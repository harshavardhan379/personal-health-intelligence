"""Human review: confirm/edit/reject extracted facts (YGH-36, YGH-37 API, YGH-38, YGH-39)."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.models import HealthEvent, Provenance, User
from services.api.app.core.deps import get_current_user

router = APIRouter(prefix="/events", tags=["review"])

# The only legal transitions (YGH-38). Anything else is rejected with a 409.
_ALLOWED_TRANSITIONS = {
    "pending": {"confirmed", "edited", "rejected"},
}


class EventOut(BaseModel):
    id: str
    event_type: str
    event_date: str
    data: dict
    confidence: float
    review_status: str
    source_pages: list[int]

    model_config = {"from_attributes": True}


class EditRequest(BaseModel):
    data: dict


def _to_out(event: HealthEvent) -> EventOut:
    return EventOut(
        id=str(event.id),
        event_type=event.event_type,
        event_date=event.event_date.isoformat(),
        data=event.data,
        confidence=event.confidence,
        review_status=event.review_status,
        source_pages=[p.page_number for p in event.provenance],
    )


def _get_owned_event(db: Session, user: User, event_id: uuid.UUID) -> HealthEvent:
    event = db.get(HealthEvent, event_id)
    if event is None or event.user_id != user.id:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


def _require_transition(event: HealthEvent, to_status: str) -> None:
    allowed = _ALLOWED_TRANSITIONS.get(event.review_status, set())
    if to_status not in allowed:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot move event from '{event.review_status}' to '{to_status}'",
        )


@router.get("", response_model=list[EventOut])
def list_pending_events(
    status_filter: Optional[str] = "pending",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    query = db.query(HealthEvent).filter_by(user_id=current_user.id)
    if status_filter:
        query = query.filter_by(review_status=status_filter)
    events = query.order_by(HealthEvent.event_date.desc()).all()
    return [_to_out(e) for e in events]


@router.post("/{event_id}/confirm", response_model=EventOut)
def confirm_event(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = _get_owned_event(db, current_user, event_id)
    _require_transition(event, "confirmed")

    # Invariant from docs/06_DATA_MODEL.md: no confirmed event without provenance.
    has_provenance = db.query(Provenance).filter_by(health_event_id=event.id).count() > 0
    if not has_provenance:
        raise HTTPException(
            status_code=422, detail="Cannot confirm an event with no provenance"
        )

    event.review_status = "confirmed"
    event.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)
    return _to_out(event)


@router.post("/{event_id}/edit", response_model=EventOut)
def edit_event(
    event_id: uuid.UUID,
    payload: EditRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = _get_owned_event(db, current_user, event_id)
    _require_transition(event, "edited")

    event.data = payload.data
    event.review_status = "edited"
    event.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)
    return _to_out(event)


@router.post("/{event_id}/reject", response_model=EventOut)
def reject_event(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    event = _get_owned_event(db, current_user, event_id)
    _require_transition(event, "rejected")

    event.review_status = "rejected"
    event.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(event)
    return _to_out(event)
