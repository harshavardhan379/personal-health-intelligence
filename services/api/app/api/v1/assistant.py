"""Assistant Q&A endpoint (YGH-83), now gated by verification (YGH-56).

The response carries the numbered evidence the answer was generated from (YGH-122), so the
page can show each claim's sources. It is left empty whenever the safety layer blocked or
replaced the answer.

Every call is traced (YGH-92): what it did, as metadata only, written to `ai_traces`.
"""

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from packages.observability import trace
from packages.schemas.db import get_db
from packages.schemas.models import User
from packages.schemas.verification import VerifiedAnswer
from services.api.app.core.deps import get_current_user
from services.api.app.core.tracing import write_ai_trace
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
    started = time.perf_counter()
    trace.start_trace(question_chars=len(payload.question))
    try:
        verified, evidence = run_verified_pipeline(db, current_user.id, payload.question)
        if verified.safety_action not in BLOCKING_ACTIONS:
            verified.evidence = describe_sources(db, current_user.id, evidence)
        return verified
    except Exception:
        trace.record(status=trace.ERROR)
        raise
    finally:
        finished = trace.current_trace()
        trace.end_trace()
        write_ai_trace(db, current_user.id, finished, round((time.perf_counter() - started) * 1000))
