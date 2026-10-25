"""The account: a summary for the settings page (YGH-124), and deletion of the data (YGH-119) or
the whole account (YGH-121). Both deletions are permanent and ask for the password again, so a
stolen login token alone cannot wipe or remove an account."""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.models import Document, HealthEvent, User
from services.api.app.core.config import settings
from services.api.app.core.deletion import delete_user_data
from services.api.app.core.deps import get_current_user
from services.api.app.core.ratelimit import client_address, login_failures, refuse_if_limited
from services.api.app.core.security import verify_password

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


class PasswordConfirmation(BaseModel):
    password: str


class DeletedOut(BaseModel):
    documents: int
    events: int
    files_failed: int


def _confirm_password(request: Request, user: User, password: str) -> None:
    """Re-check the password for a destructive action. Wrong guesses count against the same limit
    as login, so this cannot be used to guess the password with a stolen token."""
    attempt = f"{client_address(request)}|{user.email.lower()}"
    refuse_if_limited(login_failures, attempt)
    if not verify_password(password, user.hashed_password):
        login_failures.record(attempt)
        # 403, not 401: the login is fine, the confirmation is not (a 401 means "log in again").
        raise HTTPException(status_code=403, detail="The password is incorrect")
    login_failures.reset(attempt)


@router.delete("/data", response_model=DeletedOut)
def delete_my_data(
    payload: PasswordConfirmation,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete every document, fact, source, embedding, trace and uploaded file; keep the account."""
    _confirm_password(request, current_user, payload.password)
    result = delete_user_data(db, current_user.id)
    return DeletedOut(documents=result.documents, events=result.events,
                      files_failed=result.files_failed)


@router.delete("", status_code=204)
def delete_my_account(
    payload: PasswordConfirmation,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Delete the account and everything stored for it. The login token stops working at once
    (it names a user that no longer exists)."""
    _confirm_password(request, current_user, payload.password)
    delete_user_data(db, current_user.id, remove_account=True)
    return Response(status_code=204)
