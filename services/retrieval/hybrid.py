"""Hybrid retrieval (YGH-77): structured + semantic, ranked into one evidence list."""

import uuid

from sqlalchemy.orm import Session

from packages.schemas.evidence import Evidence
from services.retrieval.ranking import rank_evidence
from services.retrieval.semantic import search_semantic
from services.retrieval.structured import search_structured


def hybrid_search(db: Session, user_id: uuid.UUID, query: str) -> list[Evidence]:
    structured = search_structured(db, user_id, query)
    semantic = search_semantic(db, user_id, query)
    return rank_evidence(structured + semantic)
