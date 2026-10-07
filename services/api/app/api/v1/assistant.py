"""Assistant Q&A endpoint (YGH-83)."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from packages.schemas.answer import Answer
from packages.schemas.db import get_db
from packages.schemas.models import User
from services.api.app.core.deps import get_current_user
from services.reasoning.generate import generate_answer

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AskRequest(BaseModel):
    question: str


@router.post("/ask", response_model=Answer)
def ask(
    payload: AskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return generate_answer(db, current_user.id, payload.question)
