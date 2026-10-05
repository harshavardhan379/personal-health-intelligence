"""Hybrid retrieval endpoint (YGH-77, YGH-79). Lets the assistant (M11) and manual
testing both exercise retrieval without duplicating the query logic.
"""


from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from packages.schemas.db import get_db
from packages.schemas.evidence import Evidence
from packages.schemas.models import User
from services.api.app.core.deps import get_current_user
from services.retrieval.hybrid import hybrid_search

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=list[Evidence])
def search(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return hybrid_search(db, current_user.id, q)
