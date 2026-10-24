"""Assistant Q&A endpoint (YGH-83), now gated by verification (YGH-56).

The response carries the numbered evidence the answer was generated from (YGH-122), so the
page can show each claim's sources. It is left empty whenever the safety layer blocked or
replaced the answer.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.models import User
from packages.schemas.verification import VerifiedAnswer
from services.api.app.core.deps import get_current_user
from services.safety.policy import BLOCKING_ACTIONS
from services.verification.sources import describe_sources
from services.verification.verify import run_verified_pipeline

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AskRequest(BaseModel):
    question: str


@router.post("/ask", response_model=VerifiedAnswer)
def ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    verified, evidence = run_verified_pipeline(db, current_user.id, payload.question)
    if verified.safety_action not in BLOCKING_ACTIONS:
        verified.evidence = describe_sources(db, current_user.id, evidence)
    return verified
