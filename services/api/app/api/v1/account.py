"""Account summary for the settings page (YGH-124): who you are, what is stored about you,
and where your data goes. Read-only: deleting data is M17 (YGH-119, YGH-121)."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.models import Document, HealthEvent, User
from services.api.app.core.config import settings
from services.api.app.core.deps import get_current_user

router = APIRouter(prefix="/account", tags=["account"])

LOCAL_PROVIDERS = {"ollama"}


class EventCounts(BaseModel):
    confirmed: int = 0
    edited: int = 0
    pending: int = 0
    rejected: int = 0


class PrivacyInfo(BaseModel):
    llm_provider: str
    # True only when the model runs on this machine: nothing about a question or your records
    # is sent to an outside service. Any other provider receives the evidence it answers from.
    ai_runs_locally: bool


class AccountOut(BaseModel):
    id: str
    email: str
    created_at: Optional[datetime] = None
    documents: int
    events: EventCounts
    privacy: PrivacyInfo


@router.get("", response_model=AccountOut)
def get_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    documents = (
        db.query(func.count(Document.id)).filter(Document.user_id == current_user.id).scalar()
    )
    by_status = dict(
        db.query(HealthEvent.review_status, func.count(HealthEvent.id))
        .filter(HealthEvent.user_id == current_user.id)
        .group_by(HealthEvent.review_status)
        .all()
    )
    provider = settings.llm_provider.lower()
    return AccountOut(
        id=str(current_user.id),
        email=current_user.email,
        created_at=current_user.created_at,
        documents=documents or 0,
        events=EventCounts(**{k: v for k, v in by_status.items() if k in EventCounts.model_fields}),
        privacy=PrivacyInfo(llm_provider=provider, ai_runs_locally=provider in LOCAL_PROVIDERS),
    )
